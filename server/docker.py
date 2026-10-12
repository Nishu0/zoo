import contextlib
import io
import json
import os
import shlex
import tarfile
import time
import urllib.parse
import urllib.request
from typing import Any

import docker
import docker.errors

from db.connection import db_manager
from server import egress, kube, nodes, objects, sizes

IMAGE = os.environ.get("ZOO_SANDBOX_IMAGE", "zoo-sandbox:latest")
CODE_IMAGE = os.environ.get("ZOO_CODE_IMAGE", "zoo-code:latest")
HOME = "/home/zoo"
# where a pooled sandbox's secrets go once it is claimed (guest/sys_unix.go)
SECRETS_DIR = "/run/zoo"
NETWORK = os.environ.get("ZOO_NETWORK")
RUNTIME = os.environ.get("ZOO_RUNTIME", "kata")


class LocalDocker:
    """The API host's own Docker, connected on first use: a Zoo on Kubernetes has none, and only the sandboxes'
    Kubernetes backend (server/kube.py) runs there."""

    def __init__(self):
        self.client: docker.DockerClient | None = None

    def __getattr__(self, name: str):
        if self.client is None:
            self.client = docker.from_env()
        return getattr(self.client, name)


docker_client: Any = LocalDocker()
remotes: dict[str, docker.DockerClient] = {}
remote_urls: dict[str, str] = {}


def connect(server_id: str, url: str) -> docker.DockerClient:
    """The server's Docker: through its zoo-node while one is connected, else at its URL (Docker over SSH)."""
    target = nodes.docker_url(server_id) or url
    if target.startswith("node://"):
        raise RuntimeError("the server's zoo-node is not connected")
    if server_id in remotes and remote_urls.get(server_id) != target:
        remotes.pop(server_id)
    if server_id not in remotes:
        remotes[server_id] = docker.DockerClient(
            base_url=target, use_ssh_client=target.startswith("ssh://"), timeout=30
        )
        remote_urls[server_id] = target
    return remotes[server_id]


def client_for(server) -> Any:
    """The server's Docker; for this machine, its own Docker or, on Kubernetes, the cluster (server/kube.py)."""
    if server is None:
        return kube.cluster if kube.enabled() else docker_client
    return connect(server.id, server.docker_url)


def host_of(container_id: str) -> Any:
    """The Docker a container runs on, from the sandbox (or pooled sandbox) the database records it for."""
    with db_manager.session() as db:
        sandbox = db.get_sandbox_by_runtime_id(runtime_id=container_id)
        pooled = None if sandbox is not None else db.get_pool_sandbox_by_runtime_id(runtime_id=container_id)
        if sandbox is None and pooled is None:
            raise docker.errors.NotFound(f"container {container_id} belongs to no sandbox")
        server_id = sandbox.server_id if sandbox is not None else pooled.server_id if pooled is not None else None
        server = db.get_server(id=server_id) if server_id else None
    return client_for(server)


def container(container_id: str, client: Any = None):
    """The container; on `client` when the caller knows its host (its row may already be gone), else on the host
    the database records for it."""
    if kube.owns(container_id):
        return kube.container(container_id)
    return (client or host_of(container_id)).containers.get(container_id)


def ensure_image(client: Any, image: str):
    # the API's own Docker built or pulled them; a cluster's kubelets pull what pods name
    if client is docker_client or isinstance(client, kube.Cluster):
        return
    try:
        client.images.get(image)
    except docker.errors.ImageNotFound:
        try:
            client.images.pull(image)
        except docker.errors.APIError:
            client.images.load(docker_client.images.get(image).save())


def prepull(server):
    """Pulls every sandbox image onto a server, so the first boot after a release or on a new server doesn't wait
    on a multi-GB pull."""
    client = connect(server.id, server.docker_url)
    for image in (IMAGE, CODE_IMAGE):
        ensure_image(client, image)


def runtime_for(client: docker.DockerClient) -> str:
    info = client.info()
    if RUNTIME in info.get("Runtimes", {}):
        return RUNTIME
    if "Docker Desktop" in (info.get("OperatingSystem") or ""):
        # on a Mac or Windows host, Docker Desktop already runs every container inside its own Linux VM
        return "runc"
    raise RuntimeError(
        f"docker runtime '{RUNTIME}' is not configured on this host; install Kata Containers and register it "
        f"in /etc/docker/daemon.json (see README), or set ZOO_RUNTIME=runc to run plain containers"
    )


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
    ):
        return existing
    existing.remove(force=True)
    return None


TUNNEL_LABEL = "zoo.guest.tunnel"


SECURITY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "security")
APPARMOR_PROFILE = "zoo-sandbox"
# per Docker client: whether zoo-sandbox is loaded on that host
apparmor_loaded: dict[int, bool] = {}


def security_options(client: docker.DockerClient, runtime: str) -> list[str]:
    """runc shares the host's kernel, unlike Kata's VM, so its sandboxes get a tighter seccomp filter and, where
    the host runs AppArmor, the zoo-sandbox profile (deploy/security)."""
    options = ["no-new-privileges"]
    if runtime != "runc":
        return options
    with open(os.path.join(SECURITY_DIR, "seccomp.json")) as f:
        options.append("seccomp=" + f.read())
    if load_apparmor(client):
        options.append(f"apparmor={APPARMOR_PROFILE}")
    return options


def load_apparmor(client: docker.DockerClient) -> bool:
    """Loads the AppArmor profile into the host's kernel once, from a short-lived privileged container."""
    key = id(client)
    if key not in apparmor_loaded:
        apparmor_loaded[key] = False
        if any("name=apparmor" in o for o in client.info().get("SecurityOptions") or []):
            with open(os.path.join(SECURITY_DIR, "apparmor")) as f:
                profile = f.read()
            ensure_image(client, IMAGE)
            try:
                client.containers.run(
                    IMAGE,
                    ["sh", "-c", 'printf %s "$PROFILE" | apparmor_parser -r'],
                    environment={"PROFILE": profile},
                    runtime="runc",
                    privileged=True,
                    network_mode="none",
                    volumes={"/sys/kernel/security": {"bind": "/sys/kernel/security", "mode": "rw"}},
                    remove=True,
                )
                apparmor_loaded[key] = True
            except docker.errors.DockerException:
                # without it the sandbox gets Docker's own docker-default profile
                pass
    return apparmor_loaded[key]


def default_image(kind: str) -> str:
    return CODE_IMAGE if kind == "code" else IMAGE


def run_container(
    name: str,
    image: str,
    sandbox_id: str,
    env: dict[str, str],
    server=None,
    desktop: bool = True,
    size: sizes.Size = sizes.DEFAULT,
) -> tuple[str, str | None, int | None]:
    """Starts (or adopts) the sandbox's container, at its size (server/sizes.py). Returns its id and, for a desktop,
    where its VNC websocket is: a host and port, or no port when the desktop is reached through the guest's
    tunnel."""
    client = client_for(server)
    if isinstance(client, kube.Cluster):
        return kube.run_pod(name, image, sandbox_id, env, desktop, size)
    runtime = runtime_for(client)
    ensure_image(client, image)
    local = server is None
    bind = "127.0.0.1" if local else server.bind_address
    # images labelled zoo.guest.tunnel serve their desktop through the guest, so 6080 stays unpublished
    tunnel = desktop and "ZOO_GUEST_TOKEN" in env and TUNNEL_LABEL in (client.images.get(image).labels or {})
    if desktop and not local and not tunnel:
        raise RuntimeError(
            "remote servers don't publish the desktop port: set ZOO_GUEST_REMOTE_URL so the sandbox's guest can "
            "tunnel it, and use a sandbox image with the guest"
        )
    created = adoptable(client, name, image, sandbox_id)
    # a random host port on loopback; docker-py takes None for the port, though its types don't say so
    ports: Any = {"6080/tcp": ("127.0.0.1", None)} if desktop and not tunnel and not NETWORK else None
    if created is None:
        created = client.containers.run(
            image,
            name=name,
            detach=True,
            runtime=runtime,
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
            security_opt=security_options(client, runtime),
            volumes={volume_name(sandbox_id): {"bind": HOME, "mode": "rw"}},
            labels={"zoo.sandbox": sandbox_id},
            network=NETWORK if local else None,
            ports=ports,
        )
    container_id = created.id
    if container_id is None:
        raise RuntimeError("docker returned a container without an id")
    if not desktop:
        return container_id, None, None
    if tunnel:
        # no port: the desktop is reached through the guest
        return container_id, name if local and NETWORK else bind, None
    if NETWORK:
        return container_id, name, 6080
    created.reload()
    port_info = created.attrs["NetworkSettings"]["Ports"]["6080/tcp"]
    return container_id, "127.0.0.1", int(port_info[0]["HostPort"])


def wait_for_vnc(host: str, port: int, timeout: int = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://{host}:{port}/vnc.html", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def remove_container(container_id: str, client: Any = None):
    """Removes the container; pass `client` when its sandbox's row may already be gone (see container)."""
    try:
        if kube.owns(container_id):
            # a sandbox pod forgets its own policy as it goes
            kube.container(container_id).remove(force=True)
            return
        client = client or host_of(container_id)
        found = client.containers.get(container_id)
    except docker.errors.NotFound:
        return
    found.remove(force=True)
    forget_policy(client, found.id or container_id)


def remove_volume(sandbox_id: str, server=None):
    try:
        client_for(server).volumes.get(volume_name(sandbox_id)).remove(force=True)
    except docker.errors.NotFound:
        pass


def helper(client: Any, volumes: dict[str, str], image: str = IMAGE):
    """A one-shot root container with the given volumes (name: path) mounted, for work on a sandbox's disk."""
    ensure_image(client, image)
    return client.containers.create(
        image,
        entrypoint=["sleep", "21600"],
        user="root",
        volumes={name: {"bind": path, "mode": "rw"} for name, path in volumes.items()},
        labels={"zoo.helper": "1"},
    )


def helper_check(found, script: str, environment: dict[str, str] | None = None) -> str:
    result = found.exec_run(["bash", "-c", "set -euo pipefail\n" + script], user="root", environment=environment)
    output = result.output.decode(errors="replace") if isinstance(result.output, bytes) else ""
    if result.exit_code != 0:
        raise RuntimeError(output.strip()[-500:] or f"exit code {result.exit_code}")
    return output


MOVE_PART_BYTES = 1 << 30

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


def copy_volume(sandbox_id: str, source, target, image: str = IMAGE):
    """Moves a stopped sandbox's home volume to another host through object storage: the hosts upload and download
    directly, the API only hands out presigned URLs."""
    move_volume(sandbox_id, source, target, image)
    remove_volume(sandbox_id, source)


def move_volume(sandbox_id: str, source, target, image: str = IMAGE):
    src, dst = client_for(source), client_for(target)
    name = volume_name(sandbox_id)
    reader = helper(src, {name: "/from"}, image)
    keys: list[str] = []
    try:
        reader.start()
        size = int(helper_check(reader, "du -sb /from | cut -f1").strip() or 0)
        # gzip can't grow data by more than a little, so this many parts always fit
        keys = [f"moves/{sandbox_id}/home.{n:04d}" for n in range(size // MOVE_PART_BYTES + 2)]
        urls = "\n".join(objects.presign("PUT", k) for k in keys) + "\n"
        reader.put_archive("/tmp", tar_file("urls", urls.encode()))
        used = helper_check(reader, UPLOAD_HOME, {"PART": str(MOVE_PART_BYTES)}).split()
        writer = helper(dst, {name: "/to"}, image)
        try:
            writer.start()
            downloads = "\n".join(objects.presign("GET", keys[int(f[1:])]) for f in used) + "\n"
            writer.put_archive("/tmp", tar_file("urls", downloads.encode()))
            helper_check(writer, DOWNLOAD_HOME)
        finally:
            writer.remove(force=True)
    finally:
        reader.remove(force=True)
        for key in keys:
            with contextlib.suppress(Exception):
                objects.delete(key)


def csi(server) -> bool:
    return server is None and kube.enabled() and bool(kube.SNAPSHOT_CLASS)


def snapshot_name(snapshot_id: str) -> str:
    return f"zoo-snap-{snapshot_id}"


def snapshot_prefix(snapshot_id: str) -> str:
    return f"snapshots/{snapshot_id}/"


def snapshot(sandbox_id: str, snapshot_id: str, server=None) -> int:
    """Packs the home volume into object storage, in parts, as a move does (the host uploads, the API only presigns);
    returns its size. Safe while the sandbox runs (like pulling the plug: files being written may be cut short). On
    Kubernetes with a VolumeSnapshotClass, a CSI snapshot of the home claim instead."""
    if csi(server):
        return kube.csi_snapshot(sandbox_id, snapshot_id, snapshot_name(snapshot_id))
    reader = helper(client_for(server), {volume_name(sandbox_id): "/from"})
    try:
        reader.start()
        size = int(helper_check(reader, "du -sb /from | cut -f1").strip() or 0)
        # gzip can't grow data by more than a little, so this many parts always fit
        keys = [f"{snapshot_prefix(snapshot_id)}home.{n:04d}" for n in range(size // MOVE_PART_BYTES + 2)]
        urls = "\n".join(objects.presign("PUT", k) for k in keys) + "\n"
        reader.put_archive("/tmp", tar_file("urls", urls.encode()))
        helper_check(reader, UPLOAD_HOME, {"PART": str(MOVE_PART_BYTES)})
        return size
    except Exception:
        remove_snapshot(snapshot_id, server)
        raise
    finally:
        reader.remove(force=True)


def restore_snapshot(sandbox_id: str, snapshot_id: str, server=None):
    """Replaces the home volume's contents with the snapshot's, from object storage, on whichever host the sandbox
    is on now. The sandbox must be stopped."""
    if csi(server):
        return kube.restore_csi_snapshot(sandbox_id, snapshot_name(snapshot_id))
    keys = sorted(objects.keys(snapshot_prefix(snapshot_id)))
    if not keys:
        raise RuntimeError("the snapshot is gone from object storage")
    writer = helper(client_for(server), {volume_name(sandbox_id): "/to"})
    try:
        writer.start()
        downloads = "\n".join(objects.presign("GET", k) for k in keys) + "\n"
        writer.put_archive("/tmp", tar_file("urls", downloads.encode()))
        helper_check(writer, DOWNLOAD_HOME)
    finally:
        writer.remove(force=True)


def remove_snapshot(snapshot_id: str, server=None):
    if csi(server):
        return kube.remove_csi_snapshot(snapshot_name(snapshot_id))
    objects.delete_prefix(snapshot_prefix(snapshot_id))


def export_dir(container_id: str, path: str) -> bytes:
    stream, _ = container(container_id).get_archive(path)
    return b"".join(stream)


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
    if kube.owns(container_id):
        return kube.apply_network(container_id, default_action, allow_dns, rules)
    name, data = network_policy(container_id, default_action, allow_dns, rules)
    client = host_of(container_id)
    daemon = egress_daemon(client)
    if daemon is None:
        raise RuntimeError("the egress daemon is not running")
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


def egress_daemon(client: docker.DockerClient, create: bool = True):
    """The host's egress daemon (guest/egress.go): in the host's network namespace, with NET_ADMIN for nftables
    and nothing else. Its rules stay in the kernel when it stops, sending filtered sandboxes' traffic nowhere, so
    the policy fails closed. Recreated when the sandbox image (which carries it) changes."""
    try:
        daemon = client.containers.get(EGRESS_NAME)
    except docker.errors.NotFound:
        daemon = None
    if not create:
        return daemon if daemon is not None and daemon.status == "running" else None
    if daemon is not None:
        if daemon.status == "running" and daemon.attrs["Image"] == client.images.get(IMAGE).id:
            return daemon
        daemon.remove(force=True)
    ensure_image(client, IMAGE)
    return client.containers.run(
        IMAGE,
        ["/usr/local/bin/zoo-guest", "-egress", EGRESS_DIR],
        name=EGRESS_NAME,
        detach=True,
        runtime="runc",
        network_mode="host",
        cap_drop=["ALL"],
        cap_add=["NET_ADMIN"],
        security_opt=["no-new-privileges"],
        restart_policy={"Name": "always"},
        volumes={EGRESS_VOLUME: {"bind": EGRESS_DIR, "mode": "rw"}},
        labels={"zoo.egress": "1"},
    )


def prune_policies(client: docker.DockerClient, daemon):
    """Drops the policies of containers that are gone, so a new container given the same address doesn't get one."""
    names = daemon.exec_run(["ls", EGRESS_DIR]).output.decode(errors="replace").split()
    live = {egress.file_name(c.id) for c in client.containers.list(filters={"label": "zoo.sandbox"}) if c.id}
    stale = [f"{EGRESS_DIR}/{n}" for n in names if n.endswith(".json") and n not in live]
    if stale:
        daemon.exec_run(["rm", "-f", *stale])


def forget_policy(client: docker.DockerClient, container_id: str):
    daemon = egress_daemon(client, create=False)
    if daemon is not None:
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
