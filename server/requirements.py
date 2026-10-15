"""What the API needs to serve. Each check raises with what's wrong; main.py runs them all before serving and exits
with one line per failure, and /readyz reports each by name."""

import os

from db.connection import db_manager
from server import docker, objects, runtime
from server.security import require_secrets_key


def database():
    if db_manager._pool is None:
        db_manager.init_db()
    with db_manager.session() as db:
        list(db.list_all_servers())


def object_store():
    objects.require()
    objects.listing("readyz/")


def jwt_secret():
    if not os.environ.get("JWT_SECRET"):
        raise RuntimeError("JWT_SECRET is not set")


def api_url():
    if not os.environ.get("ZOO_API_URL"):
        raise RuntimeError("ZOO_API_URL is not set; set it to the URL sandboxes and hosts reach the API on")


def runtime_setting():
    if os.environ.get("ZOO_RUNTIME", "docker") not in {"docker", "kube"}:
        raise RuntimeError("ZOO_RUNTIME must be docker or kube")


def linux_api():
    runtime.ping(None)


def gateway():
    from server.gateway_client import client

    client.sync(client.readiness())


def isolation():
    """ZOO_ISOLATION=kata insists on Kata; auto falls back to plain containers where there is no KVM."""
    if docker.ISOLATION not in docker.ISOLATIONS:
        raise RuntimeError("ZOO_ISOLATION must be auto, kata or runc")
    if docker.ISOLATION == "kata" and docker.KATA not in runtime.local_info().get("Runtimes", {}):
        raise RuntimeError("Docker has no io.containerd.kata.v2 runtime; install and register Kata Containers")


def cluster_requirement(name: str):
    checks = runtime.kubernetes_check()
    found = next((item for item in checks if item["name"] == name), None)
    if found is None:
        raise RuntimeError(f"Kubernetes did not report its {name} requirement")
    if not found["ok"]:
        raise RuntimeError(found["detail"])


def cluster_kvm():
    cluster_requirement("kvm")


def runtime_class():
    cluster_requirement("runtime class")


def storage_class():
    cluster_requirement("storage")


def snapshot_class():
    cluster_requirement("snapshots")


CHECKS = {
    "database": database,
    "object_store": object_store,
    "jwt_secret": jwt_secret,
    "secrets_key": require_secrets_key,
    "api_url": api_url,
    "runtime": runtime_setting,
    "gateway": gateway,
    runtime.NAME: linux_api,
}

if runtime.is_kube():
    CHECKS.update(
        kvm=cluster_kvm, runtime_class=runtime_class, storage_class=storage_class, snapshot_class=snapshot_class
    )
else:
    CHECKS.update(isolation=isolation, sandbox_images=runtime.sandbox_images)


def failures() -> list[str]:
    failed = []
    for name, check in CHECKS.items():
        try:
            check()
        except Exception as e:
            failed.append(f"{name}: {str(e) or e.__class__.__name__}")
    try:
        with db_manager.session() as db:
            servers = list(db.list_all_servers())
    except Exception as error:
        failed.append(f"vm_hosts: {str(error) or error.__class__.__name__}")
        return failed
    for server in servers:
        try:
            runtime.host_requirements(server)
        except Exception as error:
            failed.append(f"server:{server.name}: {str(error) or error.__class__.__name__}")
    return failed
