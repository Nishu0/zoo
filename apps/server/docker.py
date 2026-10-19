import io
import json
import os
import shlex
import tarfile
import threading
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import docker
import docker.errors
import requests

from apps.server import egress, objects, sizes
from apps.server.errors import ZooError
from apps.server.logger import logger

# two image buckets: the platform's own images (api, gateway, egress, web) come from ZOO_REGISTRY, the sandbox
# images (zoo-sandbox-*) from ZOO_SANDBOX_REGISTRY; both default to the same registry
REGISTRY = os.environ.get("ZOO_REGISTRY", "docker.io/chann44")
SANDBOX_REGISTRY = os.environ.get("ZOO_SANDBOX_REGISTRY", REGISTRY)
IMAGE = os.environ.get("ZOO_SANDBOX_IMAGE", f"{SANDBOX_REGISTRY}/zoo-sandbox-desktop:latest")
BROWSER_IMAGE = os.environ.get("ZOO_BROWSER_IMAGE", f"{SANDBOX_REGISTRY}/zoo-sandbox-browser:latest")
CODE_IMAGE = os.environ.get("ZOO_CODE_IMAGE", f"{SANDBOX_REGISTRY}/zoo-sandbox-code:latest")
HOME = "/home/zoo"
# where a pooled sandbox's secrets go once it is claimed (guest/sys_unix.go)
SECRETS_DIR = "/run/zoo"
NETWORK = os.environ.get("ZOO_NETWORK")
KATA = "io.containerd.kata.v2"
# How sandboxes are isolated. kata: a micro-VM each, needs KVM (Linux hosts). runc: plain containers, for Docker
# Desktop on macOS and Windows, which has no KVM. auto (the default): kata when Docker has it registered, else runc.
ISOLATION = os.environ.get("ZOO_ISOLATION", "auto")
ISOLATIONS = ("auto", "kata", "runc")
# the one host every sandbox runs on, as the pause sweeper names nodes
NODE = "local"
ERRORS = (docker.errors.DockerException,)
NOT_FOUND_ERRORS = (docker.errors.NotFound,)

_host: ContextVar[tuple[Any, str, str] | None] = ContextVar("docker_host", default=None)


@contextmanager
def on_host(client, network: str, runtime: str):
    """Scope Docker operations to one authenticated remote host, including nested helpers."""
    token = _host.set((client, network, runtime))
    try:
        yield
    finally:
        _host.reset(token)


def network_name() -> str | None:
    host = _host.get()
    return host[1] if host is not None else NETWORK


class LocalDocker:
    """The API host's own Docker, connected on first use."""

    def __init__(self):
        self.client: docker.DockerClient | None = None

    def __getattr__(self, name: str):
        host = _host.get()
        if host is not None:
            return getattr(host[0], name)
        if self.client is None:
            endpoint = urllib.parse.urlsplit(os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"))
            if endpoint.scheme not in ("unix", "npipe") and endpoint.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ZooError(
                    "not_supported",
                    "Docker must run on the API host",
                    "Set DOCKER_HOST to the host's local Docker socket.",
                )
            self.client = docker.from_env()
        return getattr(self.client, name)


docker_client: Any = LocalDocker()


def container(container_id: str):
    return docker_client.containers.get(container_id)


def info() -> dict:
    return docker_client.info()


_runtime: str | None = None


def container_runtime() -> str:
    """The OCI runtime sandboxes run under, chosen once from ZOO_ISOLATION."""
    global _runtime
    host = _host.get()
    if host is not None:
        return host[2]
    if _runtime is None:
        if ISOLATION == "auto":
            _runtime = KATA if KATA in info().get("Runtimes", {}) else "runc"
        else:
            _runtime = KATA if ISOLATION == "kata" else "runc"
        if _runtime != KATA:
            logger.warning("sandboxes run as plain containers (runc), not Kata micro-VMs: Docker has no Kata runtime")
    return _runtime


def ping():
    return docker_client.ping()


def guest_url() -> str:
    return "ws://gateway:8001/guest/connect"


class DockerEvents(Iterator[dict]):
    """Sandbox events with a fresh inventory on every connection and cancellable socket reads."""

    def __init__(self, stop: threading.Event):
        self.stop = stop
        self.stream: Any = None
        self.lock = threading.Lock()
        self.iterator = self._iterate()
        self.cancellation = threading.Thread(target=self._cancel, daemon=True)
        self.cancellation.start()

    def __next__(self) -> dict:
        return next(self.iterator)

    def close(self):
        self.stop.set()
        self._close_stream()

    def _cancel(self):
        self.stop.wait()
        self._close_stream()

    def _close_stream(self):
        with self.lock:
            stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.close()
            except (docker.errors.DockerException, requests.exceptions.RequestException, OSError):
                pass

    def _iterate(self) -> Iterator[dict]:
        delay = 1.0
        try:
            while not self.stop.is_set():
                try:
                    stream = docker_client.events(
                        decode=True,
                        filters={"type": "container", "label": "zoo.sandbox", "event": ["die", "oom", "destroy"]},
                    )
                    with self.lock:
                        self.stream = stream
                    if self.stop.is_set():
                        return
                    sandboxes = docker_client.containers.list(
                        all=True, filters={"label": "zoo.sandbox"}, ignore_removed=True
                    )
                    inventory = []
                    for sandbox in sandboxes:
                        if self.stop.is_set():
                            return
                        state = sandbox.attrs.get("State", {})
                        active = sandbox.status in {"running", "paused", "restarting"}
                        failed = state.get("OOMKilled") or state.get("Dead") or state.get("ExitCode", 0) != 0
                        inventory.append(
                            {
                                "runtime_id": sandbox.id,
                                "sandbox_id": sandbox.labels.get("zoo.sandbox"),
                                "status": "running" if active else "failed" if failed else "stopped",
                            }
                        )
                    if self.stop.is_set():
                        return
                    yield {
                        "type": "reconcile",
                        "running_ids": [
                            sandbox["runtime_id"] for sandbox in inventory if sandbox["status"] == "running"
                        ],
                        "sandboxes": inventory,
                    }
                    delay = 1.0
                    for event in stream:
                        if self.stop.is_set():
                            return
                        action = event.get("Action")
                        actor = event.get("Actor", {})
                        attributes = actor.get("Attributes", {})
                        runtime_id = actor.get("ID")
                        sandbox_id = attributes.get("zoo.sandbox")
                        if action not in {"die", "oom", "destroy"} or not runtime_id or not sandbox_id:
                            continue
                        failed = action == "oom" or action == "die" and attributes.get("exitCode", "0") != "0"
                        yield {
                            "type": "state",
                            "action": action,
                            "runtime_id": runtime_id,
                            "sandbox_id": sandbox_id,
                            "status": "failed" if failed else "stopped",
                        }
                except (docker.errors.DockerException, requests.exceptions.RequestException, OSError) as error:
                    if not self.stop.is_set():
                        logger.warning("Docker event stream disconnected", extra={"error": str(error)})
                finally:
                    self._close_stream()
                if self.stop.wait(delay):
                    return
                delay = min(delay * 2, 30.0)
        finally:
            self.close()


def events(stop: threading.Event) -> DockerEvents:
    return DockerEvents(stop)


def stats(container_id: str) -> dict:
    return container(container_id).stats(stream=False)


def pause(container_id: str):
    container(container_id).pause()


def resume(container_id: str):
    container(container_id).unpause()


def pressured_nodes(share: float) -> set[str]:
    """The Docker host, when less than `share` of its memory is available; it is the one node."""
    with open("/proc/meminfo") as meminfo:
        found = {line.split(":")[0]: int(line.split()[1]) for line in meminfo if line.split()}
    return {NODE} if found["MemAvailable"] < share * found["MemTotal"] else set()


def stop(container_id: str):
    container(container_id).stop()


def exec_command(container_id: str, cmd: str | list[str], **options) -> tuple[int, bytes, bytes]:
    options.setdefault("user", "zoo")
    result = container(container_id).exec_run(cmd, demux=True, **options)
    if result.exit_code is None:
        raise ZooError(
            "upstream_error", "Docker returned no command exit code", "Run commands without detach or stream."
        )
    stdout, stderr = result.output
    return result.exit_code, stdout or b"", stderr or b""


def volume_name(sandbox_id: str) -> str:
    return f"zoo-home-{sandbox_id}"


def adoptable(client: docker.DockerClient, name: str, image: str, sandbox_id: str):
    """The sandbox's container left running by an earlier attempt, so a retried boot reuses it; anything else under
    that name is removed."""
    try:
        existing = client.containers.get(name)
    except docker.errors.NotFound:
        return None
    if (
        existing.status == "running"
        and existing.labels.get("zoo.sandbox") == sandbox_id
        and existing.attrs["Config"]["Image"] == image
        and existing.attrs["HostConfig"].get("Runtime") == container_runtime()
    ):
        return existing
    existing.remove(force=True)
    return None


def default_image(kind: str) -> str:
    return {"code": CODE_IMAGE, "browser": BROWSER_IMAGE}.get(kind, IMAGE)


def run_container(
    name: str,
    image: str,
    sandbox_id: str,
    env: dict[str, str],
    desktop: bool = True,
    size: sizes.Size = sizes.DEFAULT,
) -> tuple[str, str | None]:
    """Starts (or adopts) the sandbox's container, at its size (server/sizes.py). Returns its id and, for a desktop,
    its host; the gateway reaches the screen through the guest."""
    client = docker_client
    created = adoptable(client, name, image, sandbox_id)
    if created is None:
        created = client.containers.run(
            image,
            name=name,
            detach=True,
            runtime=container_runtime(),
            environment=env,
            mem_limit=f"{size.memory_mb}m",
            nano_cpus=int(size.cpus * 1e9),
            # a disk size needs a storage driver with quotas; Docker refuses the container on one without
            storage_opt={"size": f"{size.disk_gb}G"} if size.disk_gb else None,
            pids_limit=1024,
            shm_size="1g",
            # network policy is enforced on the host (egress_daemon), so the sandbox keeps no way to change its
            # own addresses or forge packets: no NET_ADMIN, no raw sockets
            cap_drop=["NET_RAW"],
            security_opt=["no-new-privileges"],
            volumes={volume_name(sandbox_id): {"bind": HOME, "mode": "rw"}},
            labels={"zoo.sandbox": sandbox_id},
            network=network_name(),
        )
    container_id = created.id
    if container_id is None:
        raise RuntimeError("docker returned a container without an id")
    if not desktop:
        return container_id, None
    return container_id, name if network_name() else "127.0.0.1"


def remove_container(container_id: str):
    try:
        found = container(container_id)
    except docker.errors.NotFound:
        return
    found.remove(force=True)
    forget_policy(docker_client, found.id or container_id)


def remove_named_container(name: str):
    remove_container(name)


def remove_volume(sandbox_id: str):
    try:
        docker_client.volumes.get(volume_name(sandbox_id)).remove(force=True)
    except docker.errors.NotFound:
        pass


def helper(volumes: dict[str, str], image: str = IMAGE):
    """A one-shot root container with the given volumes (name: path) mounted, for work on a sandbox's disk."""
    return docker_client.containers.create(
        image,
        runtime=container_runtime(),
        entrypoint=["sleep", "21600"],
        user="root",
        network=network_name(),
        volumes={name: {"bind": path, "mode": "rw"} for name, path in volumes.items()},
        labels={"zoo.helper": "1"},
    )


def helper_check(found, script: str, environment: dict[str, str] | None = None) -> str:
    result = found.exec_run(["bash", "-c", "set -euo pipefail\n" + script], user="root", environment=environment)
    output = result.output.decode(errors="replace") if isinstance(result.output, bytes) else ""
    if result.exit_code != 0:
        raise RuntimeError(output.strip()[-500:] or f"exit code {result.exit_code}")
    return output


HOME_PART_BYTES = 1 << 30

# Packs the home volume and uploads it in parts, one presigned URL per part (/tmp/urls, one per line). Only one
# part is on disk at a time; S3 needs each PUT's length up front, so a part can't be streamed.
UPLOAD_HOME = r"""
export SHELL=/bin/bash
cd /from
tar -czf - . | split -d -a 4 -b "$PART" --filter '
    cat > /tmp/part && url=$(sed -n "$((10#${FILE#p} + 1))p" /tmp/urls) && [ -n "$url" ] &&
    curl -fsS --retry 3 -T /tmp/part "$url" && echo "$FILE"' - p
"""
DOWNLOAD_HOME = r"""
find /to -mindepth 1 -delete
while read -r url; do curl -fsS --retry 3 "$url"; done < /tmp/urls | tar -xzf - -C /to
"""


def snapshot_prefix(snapshot_id: str) -> str:
    return f"snapshots/{snapshot_id}/"


def snapshot(sandbox_id: str, snapshot_id: str) -> int:
    """Uploads the home as one gzip tar split into numbered S3 objects; returns its logical size in bytes."""
    reader = helper({volume_name(sandbox_id): "/from"})
    try:
        reader.start()
        size = int(helper_check(reader, "du -sb /from | cut -f1").strip() or 0)
        remove_snapshot(snapshot_id)
        # gzip can't grow data by more than a little, so this many parts always fit
        keys = [f"{snapshot_prefix(snapshot_id)}home.{n:04d}" for n in range(size // HOME_PART_BYTES + 2)]
        urls = "\n".join(objects.presign("PUT", k) for k in keys) + "\n"
        if not reader.put_archive("/tmp", tar_file("urls", urls.encode())):
            raise ZooError("upstream_error", "Could not write snapshot upload URLs", "Retry the snapshot.")
        helper_check(reader, UPLOAD_HOME, {"PART": str(HOME_PART_BYTES)})
        return size
    except Exception:
        remove_snapshot(snapshot_id)
        raise
    finally:
        reader.remove(force=True)


def restore_snapshot(sandbox_id: str, snapshot_id: str):
    """Replaces the stopped sandbox's home volume contents with its snapshot from object storage."""
    prefix = snapshot_prefix(snapshot_id)
    keys = sorted(objects.keys(prefix))
    if not keys:
        raise ZooError("gone", "The snapshot is gone from object storage", "Create a new snapshot before restoring.")
    if keys != [f"{prefix}home.{part:04d}" for part in range(len(keys))]:
        raise ZooError(
            "upstream_error", "The snapshot's numbered parts are incomplete", "Create a new snapshot before restoring."
        )
    writer = helper({volume_name(sandbox_id): "/to"})
    try:
        writer.start()
        downloads = "\n".join(objects.presign("GET", k) for k in keys) + "\n"
        if not writer.put_archive("/tmp", tar_file("urls", downloads.encode())):
            raise ZooError("upstream_error", "Could not write snapshot download URLs", "Retry the restore.")
        helper_check(writer, DOWNLOAD_HOME)
    finally:
        writer.remove(force=True)


def remove_snapshot(snapshot_id: str):
    objects.delete_prefix(snapshot_prefix(snapshot_id))


def export_dir(container_id: str, path: str) -> bytes:
    return read_archive(container_id, path)


def read_archive(container_id: str, path: str) -> bytes:
    stream, _ = container(container_id).get_archive(path)
    return b"".join(stream)


def write_archive(container_id: str, path: str, data: bytes) -> bool:
    return container(container_id).put_archive(path, data)


def import_dir(container_id: str, parent: str, data: bytes):
    root_exec(container_id, f"mkdir -p {shlex.quote(parent)} && chown zoo:zoo {shlex.quote(parent)}")
    if not container(container_id).put_archive(parent, data):
        raise RuntimeError("import failed")
    root_exec(container_id, f"chown -R zoo:zoo {shlex.quote(parent)}")


# each profile app's process names (as /proc/<pid>/comm shows them, cut to 15 characters)
APP_PROCESSES = {
    "firefox": ("firefox", "firefox-esr", "firefox-bin"),
    "chromium": ("chromium", "chromium-browse"),
    "chrome": ("chrome", "google-chrome"),
    "vscode": ("code",),
}


def app_running(container_id: str, app: str) -> bool:
    """Whether the app is running in the container. Reads /proc, so it needs nothing installed in the image."""
    names = "|".join(APP_PROCESSES[app])
    script = f'for f in /proc/[0-9]*/comm; do read -r n < "$f" 2>/dev/null && case "$n" in {names}) exit 0;; esac; done; exit 1'
    result = container(container_id).exec_run(["sh", "-c", script], user="root")
    if result.exit_code not in (0, 1):
        output = result.output.decode(errors="replace").strip() if isinstance(result.output, bytes) else ""
        raise RuntimeError(output or f"exit code {result.exit_code}")
    return result.exit_code == 0


def write_secrets(container_id: str, values: dict[str, str]):
    """Writes a sandbox's secrets where its guest reads them for every command: at boot, and changes made while it
    runs."""
    import_dir(container_id, SECRETS_DIR, tar_file("env.json", json.dumps(values).encode()))


def tar_file(name: str, data: bytes, mode: int = 0o600) -> bytes:
    return tar_files({name: data}, mode)


def tar_files(files: dict[str, bytes], mode: int = 0o600) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def is_running(container_id: str) -> bool:
    try:
        return container(container_id).status == "running"
    except docker.errors.NotFound:
        return False


def root_exec(container_id: str, script: str):
    result = container(container_id).exec_run(["sh", "-c", script], user="root")
    if result.exit_code != 0:
        raise RuntimeError(result.output.decode(errors="replace"))
    return result.output.decode(errors="replace")


def guest_endpoint(container_id: str) -> tuple[str, int] | None:
    env = dict(e.split("=", 1) for e in container(container_id).attrs["Config"].get("Env") or [] if "=" in e)
    url = urllib.parse.urlsplit(env.get("ZOO_GUEST_URL", ""))
    if not url.hostname:
        return None
    return url.hostname, url.port or (443 if url.scheme == "wss" else 80)


def apply_network(container_id: str, default_action: str, allow_dns: bool, rules: list[tuple[str, str, str]]):
    """Hands the policy to the host's egress daemon, which enforces it outside the sandbox."""
    name, data = network_policy(container_id, default_action, allow_dns, rules)
    client = docker_client
    daemon = egress_daemon(client)
    prune_policies(client, daemon)
    if not daemon.put_archive(EGRESS_DIR, tar_file(name, data)):
        raise RuntimeError("could not write the sandbox's network policy")


def network_policy(
    container_id: str, default_action: str, allow_dns: bool, rules: list[tuple[str, str, str]]
) -> tuple[str, bytes]:
    """The egress daemon's file for the sandbox: its name and contents."""
    sandbox = container(container_id)
    networks = sandbox.attrs["NetworkSettings"]["Networks"].values()
    addrs = sorted({a for n in networks for a in (n.get("IPAddress"), n.get("GlobalIPv6Address")) if a})
    if not addrs:
        raise RuntimeError("the sandbox has no network address to apply the policy to")
    endpoint = guest_endpoint(container_id)
    resolved = egress.addresses(endpoint[0]) if endpoint else []
    if endpoint and not resolved:
        # a name only the sandbox's network knows (host.docker.internal on Docker Desktop)
        resolved = root_exec(
            container_id, f"getent ahostsv4 {shlex.quote(endpoint[0])} | awk '{{print $1}}' | sort -u"
        ).split()
    sandbox_id = sandbox.id or container_id
    data = egress.policy(sandbox_id, addrs, default_action, allow_dns, rules, egress.always(endpoint, resolved))
    return egress.file_name(sandbox_id), data


EGRESS_NAME = "zoo-egress"
EGRESS_DIR = "/run/zoo-egress"
EGRESS_VOLUME = "zoo-egress"


def egress_daemon(client: docker.DockerClient):
    """The deployment's host-network egress service, with only NET_ADMIN."""
    try:
        daemon = client.containers.get(EGRESS_NAME)
    except docker.errors.NotFound:
        raise ZooError("unavailable", "the egress service is missing", "Start the Docker host's zoo-egress service.")
    if daemon.status != "running":
        raise ZooError(
            "unavailable", "the egress service is not running", "Start the Docker host's zoo-egress service."
        )
    return daemon


def prune_policies(client: docker.DockerClient, daemon):
    """Drops the policies of containers that are gone, so a new container given the same address doesn't get one."""
    names = daemon.exec_run(["ls", EGRESS_DIR]).output.decode(errors="replace").split()
    live = {egress.file_name(c.id) for c in client.containers.list(filters={"label": "zoo.sandbox"}) if c.id}
    stale = [f"{EGRESS_DIR}/{n}" for n in names if n.endswith(".json") and n not in live]
    if stale:
        daemon.exec_run(["rm", "-f", *stale])


def forget_policy(client: docker.DockerClient, container_id: str):
    daemon = egress_daemon(client)
    daemon.exec_run(["rm", "-f", f"{EGRESS_DIR}/{egress.file_name(container_id)}"])


APPS_FILE = "/etc/zoo/apps.json"


def apply_apps(container_id: str, effects: dict[str, str]):
    """Writes the app policy where the sandbox's root policy service (zoo-guest -apps) enforces it, out of the
    zoo user's reach, and applies it right away for images without that service."""
    if not effects:
        return
    root_exec(container_id, f"mkdir -p -m 700 {os.path.dirname(APPS_FILE)}")
    if not container(container_id).put_archive(
        os.path.dirname(APPS_FILE), tar_file(os.path.basename(APPS_FILE), json.dumps(effects).encode())
    ):
        raise RuntimeError("could not write the sandbox's app policy")
    lines = []
    for binary, effect in effects.items():
        if effect == "allow":
            lines.append(f"p=$(command -v {shlex.quote(binary)}) && chmod 755 $(readlink -f $p) || true")
        else:
            lines.append(
                f"p=$(command -v {shlex.quote(binary)}) && chown root:root $(readlink -f $p) && chmod 700 $(readlink -f $p) || true"
            )
    root_exec(container_id, "\n".join(lines))


def export_home(container_id: str):
    stream, _ = container(container_id).get_archive(HOME)
    return stream


def import_home(container_id: str, data: bytes):
    if not container(container_id).put_archive("/home", data):
        raise RuntimeError("restore failed")
    root_exec(container_id, f"chown -R zoo:zoo {HOME}")
