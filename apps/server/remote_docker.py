"""Linux container operations on enrolled hosts, through their pinned SSH identity.

Docker's dial-stdio transport works with Linux Docker engines on Linux, macOS,
and Windows. It uses zoo-node's outbound mTLS tunnel; no Docker TCP port is opened.
The existing Docker backend retains sizing, policies, volumes, and archive behavior.
"""

import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, cast

from docker.transport.sshconn import SSHHTTPAdapter

from apps.server import docker, ssh
from apps.server.errors import ZooError
from db.connection import db_manager

EGRESS_IMAGE = os.environ.get("ZOO_EGRESS_IMAGE", "docker.io/chann44/zoo-egress:latest")
_identifier = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def is_remote(runtime_id: str | None) -> bool:
    return bool(runtime_id and runtime_id.startswith("linux:"))


def parse(runtime_id: str) -> tuple[str, str]:
    parts = runtime_id.split(":")
    if len(parts) != 3 or parts[0] != "linux" or not all(_identifier.fullmatch(p) for p in parts[1:]):
        raise ZooError("bad_request", "invalid remote Linux runtime ID")
    return parts[1], parts[2]


class NodeSSHAdapter(SSHHTTPAdapter):
    def __init__(self, server):
        self.server = server
        self.connection_lock = threading.RLock()
        super().__init__(f"ssh://{server.id}", timeout=60)

    def _create_paramiko_client(self, base_url):
        self.ssh_client = None

    def _connect(self):
        self.ssh_client = ssh.open_client(self.server.id, self.server.docker_url)

    def get_connection(self, url, proxies=None):
        with self.connection_lock:
            if not ssh.alive(self.ssh_client):
                if self.ssh_client is not None:
                    self.ssh_client.close()
                cast(Any, self.pools).clear()
                self._connect()
            return super().get_connection(url, proxies)


def new_client(server):
    client = docker.docker.DockerClient(base_url="unix:///var/run/docker.sock", version="1.41", timeout=60)
    api = cast(Any, client.api)
    try:
        adapter = NodeSSHAdapter(server)
        api.adapters["http+docker://"].close()
        api.mount("http+docker://", adapter)
        api.base_url = "http+docker://ssh"
        api._version = api._retrieve_server_version()
    except BaseException:
        client.close()
        raise
    return client


@dataclass
class Host:
    client: Any
    network: str
    runtime: str
    lock: Any

    def context(self):
        return docker.on_host(self.client, self.network, self.runtime)


_hosts: dict[str, Host] = {}
_host_locks: dict[str, Any] = {}
_lock = threading.RLock()


def host(server_id: str) -> Host:
    with _lock:
        if server_id in _hosts:
            return _hosts[server_id]
        connection_lock = _host_locks.setdefault(server_id, threading.RLock())
    with connection_lock:
        with _lock:
            if server_id in _hosts:
                return _hosts[server_id]
        with db_manager.session() as db:
            server = db.get_server(id=server_id)
        if server is None:
            raise ZooError("not_found", "remote host not found")
        client = new_client(server)
        try:
            info = client.info()
            if info.get("OSType") != "linux":
                raise ZooError("not_supported", "this host's Docker engine must run Linux containers")
            available = docker.KATA in info.get("Runtimes", {})
            if docker.ISOLATION == "kata" and not available:
                raise ZooError("unavailable", "the remote Docker engine has no Kata runtime")
            isolation = docker.KATA if available and docker.ISOLATION != "runc" else "runc"
        except BaseException:
            client.close()
            raise
        found = Host(client, f"zoo-{server_id}", isolation, threading.RLock())
        with _lock:
            _hosts[server_id] = found
        return found


def forget(server_id: str):
    with _lock:
        found = _hosts.pop(server_id, None)
    if found is not None:
        found.client.close()


def shutdown():
    with _lock:
        hosts = list(_hosts.values())
        _hosts.clear()
    for found in hosts:
        found.client.close()


def available(server_id: str) -> bool:
    from apps.server import nodes

    with db_manager.session() as db:
        node = db.get_node_by_server(server_id=server_id)
    if node is None or not nodes.fresh(node):
        return False
    try:
        return bool(nodes.hub.connected(server_id)) and any(
            d.get("name") == "docker" and d.get("available") for d in json.loads(node.drivers)
        )
    except (ZooError, ValueError, TypeError):
        return False


def ensure_image(client, image: str):
    try:
        client.images.get(image)
    except docker.docker.errors.ImageNotFound:
        client.images.pull(image)


def prepare(server_id: str, image: str, egress: bool = False) -> Host:
    found = host(server_id)
    with found.lock:
        ensure_image(found.client, image)
        try:
            found.client.networks.get(found.network)
        except docker.docker.errors.NotFound:
            found.client.networks.create(found.network, driver="bridge")
        if egress:
            try:
                daemon = found.client.containers.get(docker.EGRESS_NAME)
                if daemon.status != "running":
                    daemon.start()
            except docker.docker.errors.NotFound:
                ensure_image(found.client, EGRESS_IMAGE)
                found.client.containers.run(
                    EGRESS_IMAGE,
                    name=docker.EGRESS_NAME,
                    detach=True,
                    command=["-dir", docker.EGRESS_DIR],
                    network_mode="host",
                    user="0:0",
                    cap_drop=["ALL"],
                    cap_add=["NET_ADMIN"],
                    security_opt=["no-new-privileges:true"],
                    restart_policy={"Name": "unless-stopped"},
                    volumes={docker.EGRESS_VOLUME: {"bind": docker.EGRESS_DIR, "mode": "rw"}},
                    labels={"zoo.egress": "1"},
                )
    return found


def run_container(server, name, image, sandbox_id, env, desktop, size):
    found = prepare(server.id, image, egress=True)
    with found.context():
        raw_id, _ = docker.run_container(name, image, sandbox_id, env, desktop, size)
    return f"linux:{server.id}:{raw_id}", server.bind_address if desktop else None


class Runtime:
    def __init__(self, runtime_id: str):
        self.server_id, _ = parse(runtime_id)

    def __getattr__(self, operation: str):
        def invoke(runtime_id: str, *args, **kwargs):
            server_id, raw_id = parse(runtime_id)
            if server_id != self.server_id:
                raise ZooError("bad_request", "cannot switch remote host during an operation")
            with host(server_id).context():
                return getattr(docker, operation)(raw_id, *args, **kwargs)

        return invoke


def volume_operation(server_id: str, operation: str, sandbox_id: str, *args):
    from apps.server import remote_snapshots

    found = prepare(server_id, docker.IMAGE) if operation in ("snapshot", "restore_snapshot") else host(server_id)
    with found.context():
        module = remote_snapshots if operation in ("snapshot", "restore_snapshot") else docker
        return getattr(module, operation)(sandbox_id, *args)


def probe(server) -> dict:
    try:
        info = host(server.id).client.info()
        return {
            "online": True,
            "name": info.get("Name"),
            "os": info.get("OperatingSystem"),
            "cpus": info.get("NCPU"),
            "memory_total": info.get("MemTotal"),
            "docker_version": info.get("ServerVersion"),
            "microvm": docker.KATA in info.get("Runtimes", {}),
            "containers_running": info.get("ContainersRunning"),
        }
    except Exception as error:
        return {"online": False, "error": str(error)}
