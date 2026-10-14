"""Selects the Linux runtime once and routes sandbox operations to its backend."""

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator

from server import docker, kube, macos, windows
from server.errors import ZooError

NAME = os.environ.get("ZOO_RUNTIME", "docker")
LINUX = {"docker": docker, "kube": kube}.get(NAME)
RUNTIME_ERRORS = (docker.docker.errors.DockerException, kube.KubeError, ZooError)
NOT_FOUND_ERRORS = (docker.docker.errors.NotFound, kube.NotFound)
IMAGE = docker.IMAGE
CODE_IMAGE = docker.CODE_IMAGE
HOME = "/home/zoo"
SECRETS_DIR = "/run/zoo"

VMS = {"macos": macos, "windows": windows}


def linux():
    if LINUX is None:
        raise ZooError(
            "unavailable", "ZOO_RUNTIME must be docker or kube", "Set ZOO_RUNTIME=docker or ZOO_RUNTIME=kube."
        )
    return LINUX


def backend(runtime_id: str):
    if macos.is_vm(runtime_id):
        return macos
    if windows.is_vm(runtime_id):
        return windows
    return linux()


def is_kube() -> bool:
    return NAME == "kube"


def refill_volumes():
    if is_kube():
        kube.refill_spare_volumes()


def default_image(kind: str) -> str:
    return CODE_IMAGE if kind == "code" else IMAGE


def run_container(name, image, sandbox_id, env, server=None, desktop=True, size=None) -> tuple[str, str | None]:
    from server.sizes import DEFAULT

    if server is not None:
        raise ZooError("not_supported", "Linux sandboxes use the configured local runtime.")
    return linux().run_container(name, image, sandbox_id, env, desktop=desktop, size=size or DEFAULT)


def container(runtime_id: str):
    return linux().container(runtime_id)


def exec_command(runtime_id: str, argv: list[str], **options):
    return linux().exec_command(runtime_id, argv, **options)


def pause(runtime_id: str):
    linux().pause(runtime_id)


def stop(runtime_id: str):
    backend(runtime_id).stop(runtime_id)


def resume(runtime_id: str):
    linux().resume(runtime_id)


def write_secrets(runtime_id: str, values: dict[str, str]):
    if is_vm(runtime_id):
        VMS["macos" if macos.is_vm(runtime_id) else "windows"].write_env(runtime_id, values)
    else:
        linux().write_secrets(runtime_id, values)


def tar_file(name: str, data: bytes, mode: int = 0o600) -> bytes:
    return docker.tar_file(name, data, mode)


def tar_files(files: dict[str, bytes], mode: int = 0o600) -> bytes:
    return docker.tar_files(files, mode)


def local_info() -> dict:
    return linux().info()


def sandbox_images():
    for image in (IMAGE, CODE_IMAGE):
        docker.docker_client.images.get(image)


async def events() -> AsyncIterator[dict]:
    loop = asyncio.get_running_loop()
    stopped = threading.Event()
    queue: asyncio.Queue[dict | BaseException | None] = asyncio.Queue()

    def follow():
        try:
            for event in linux().events(stopped):
                if stopped.is_set():
                    break
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as error:
            if not stopped.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, error)
        finally:
            if not stopped.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, None)

    thread = threading.Thread(target=follow, name="runtime-events", daemon=True)
    thread.start()
    try:
        while True:
            event = await queue.get()
            if event is None:
                return
            if isinstance(event, BaseException):
                raise event
            yield event
    finally:
        stopped.set()
        await asyncio.to_thread(thread.join, 5)


def local_stats(runtime_id: str) -> dict | None:
    return linux().stats(runtime_id)


def free_memory() -> int | None:
    if is_kube():
        return kube.free_memory()
    try:
        with open("/proc/meminfo") as source:
            for line in source:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def pressured_nodes(share: float) -> set[str]:
    """The Linux nodes with less than `share` of their memory free."""
    return linux().pressured_nodes(share)


def node_of(sandbox) -> str:
    """The Linux node a sandbox runs on: its placed node on Kubernetes, the one host on Docker."""
    return json.loads(sandbox.config or "{}").get("node_name") or docker.NODE if is_kube() else docker.NODE


def kubernetes_check(probe: bool = False) -> list[dict]:
    return kube.check(probe=probe)


def kubernetes_settings() -> dict[str, str]:
    return {"namespace": kube.NAMESPACE, "runtime_class": "kata"}


def csi(server=None) -> bool:
    return server is None and is_kube()


def move(sandbox, source, target):
    if sandbox.kind in VMS:
        return VMS[sandbox.kind].move(sandbox.id, source, target)
    raise ZooError("not_supported", "Linux moves require the Kubernetes snapshot transfer path.")


def move_volume(sandbox_id: str, target_node: str):
    if not is_kube():
        raise ZooError("not_supported", "Docker sandboxes run on one host and cannot be moved.")
    kube.move_volume(sandbox_id, target_node)


def is_vm(runtime_id: str | None) -> bool:
    return macos.is_vm(runtime_id) or windows.is_vm(runtime_id)


def remove_container(runtime_id: str):
    if is_vm(runtime_id):
        backend(runtime_id).stop(runtime_id)
    else:
        linux().remove_container(runtime_id)


def remove_volume(sandbox, server):
    if sandbox.kind in VMS:
        if server is not None:
            VMS[sandbox.kind].delete(sandbox.id, server)
    else:
        linux().remove_volume(sandbox.id)


def remove_volume_id(sandbox_id: str, config: dict):
    if is_kube():
        kube.remove_home(config)
    else:
        linux().remove_volume(sandbox_id)


def remove_named_container(name: str):
    linux().remove_named_container(name)


def read_archive(runtime_id: str, path: str) -> bytes:
    return linux().read_archive(runtime_id, path)


def write_archive(runtime_id: str, path: str, data: bytes) -> bool:
    return linux().write_archive(runtime_id, path, data)


def snapshot(sandbox, snapshot_id: str, server) -> int:
    """Snapshots the sandbox's home disk on its host; returns the snapshot's size in bytes."""
    if sandbox.kind in VMS:
        return VMS[sandbox.kind].snapshot(sandbox.id, snapshot_id, server)
    return linux().snapshot(sandbox.id, snapshot_id)


def restore_snapshot(sandbox, snapshot_id: str, server):
    if sandbox.kind in VMS:
        VMS[sandbox.kind].restore_snapshot(sandbox.id, snapshot_id, server)
    else:
        linux().restore_snapshot(sandbox.id, snapshot_id)


def remove_snapshot(sandbox, snapshot_id: str, server):
    if sandbox.kind in VMS:
        VMS[sandbox.kind].remove_snapshot(snapshot_id, server)
    else:
        linux().remove_snapshot(snapshot_id)


def is_running(runtime_id: str) -> bool:
    return backend(runtime_id).is_running(runtime_id)


def export_dir(runtime_id: str, path: str) -> bytes:
    return backend(runtime_id).export_dir(runtime_id, path)


def import_dir(runtime_id: str, parent: str, data: bytes):
    backend(runtime_id).import_dir(runtime_id, parent, data)


def app_running(runtime_id: str, app: str) -> bool:
    """Whether a profile app (PROFILE_APPS) is running in the sandbox."""
    return backend(runtime_id).app_running(runtime_id, app)


def export_home(runtime_id: str):
    return backend(runtime_id).export_home(runtime_id)


def import_home(runtime_id: str, data: bytes):
    backend(runtime_id).import_home(runtime_id, data)


def apply_network(runtime_id: str, default_action: str, allow_dns: bool, rules: list[tuple[str, str, str]]):
    backend(runtime_id).apply_network(runtime_id, default_action, allow_dns, rules)


def apply_apps(runtime_id: str, effects: dict[str, str]):
    backend(runtime_id).apply_apps(runtime_id, effects)


def connect(server):
    if server.platform in VMS:
        return VMS[server.platform].connect(server.id, server.docker_url)
    raise ZooError("not_supported", "Linux capacity is managed by the configured runtime.")


def ping(server):
    """Checks the configured Linux runtime or a registered VM host."""
    if server is None:
        linux().ping()
    else:
        connect(server)


def host_requirements(server):
    from db.connection import db_manager
    from server import nodes

    if server.platform not in VMS:
        raise ZooError("not_supported", "registered hosts must be macOS or Windows")
    with db_manager.session() as db:
        node = db.get_node_by_server(server_id=server.id)
    if node is None or not nodes.hub.connected(server.id):
        raise ZooError("unavailable", "the host's zoo-node is not connected", "Join or restart zoo-node on the host.")
    if server.platform == "macos" and node.arch != "arm64":
        raise ZooError("not_supported", "macOS hosts require Apple Silicon")
    report = VMS[server.platform].probe(server.id, server.docker_url)
    if not report.get("online") or not report.get("microvm"):
        raise ZooError("unavailable", report.get("error") or "the host's VM runtime is unavailable")
    if not VMS[server.platform].base_status(server.id).get("ready"):
        raise ZooError(
            "unavailable", "the host's base VM is not ready", "Finish base VM and guest setup under Servers."
        )
