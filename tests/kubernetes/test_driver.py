"""The Kubernetes driver (server/kube.py) against a real cluster: pods, home claims, exec, archives, the paused warm
pool, snapshots, and the requirements check.

Runs when ZOO_TEST_KUBERNETES names a namespace to use (it is created and removed), with the cluster from the
kubeconfig. The cluster must provide RuntimeClass kata and a CSI VolumeSnapshotClass configured through
ZOO_SNAPSHOT_CLASS. Snapshot tests also require exported ZOO_OBJECT_STORE settings and credentials reachable from
the cluster. ZOO_TEST_IMAGE names the image (zoo-kube-test:1, tests/kubernetes/Dockerfile)."""

import io
import json
import os
import tarfile
import time
import uuid

import pytest

from db.connection import db_manager
from server import docker, kube, objects, runtime
from server.sandbox_api import CreateSandboxRequest
from tests.conftest import member

NAMESPACE = os.environ.get("ZOO_TEST_KUBERNETES", "")
IMAGE = os.environ.get("ZOO_TEST_IMAGE", "zoo-kube-test:1")

pytestmark = pytest.mark.skipif(not NAMESPACE, reason="set ZOO_TEST_KUBERNETES to run against a cluster")


@pytest.fixture(scope="module", autouse=True)
def namespace():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(kube, "NAMESPACE", NAMESPACE)
    monkeypatch.setattr(runtime, "NAME", "kube")
    monkeypatch.setattr(runtime, "LINUX", kube)
    monkeypatch.setattr(kube, "EGRESS_NAMESPACE", NAMESPACE)
    monkeypatch.setattr(kube, "HELPER_IMAGE", IMAGE)
    monkeypatch.setattr(kube, "HOME_SIZE", "1Gi")
    monkeypatch.setattr(kube, "START_TIMEOUT", 300)
    with pytest.raises(kube.NotFound):
        kube.api.get(f"/api/v1/namespaces/{NAMESPACE}")
    kube.api.request("POST", "/api/v1/namespaces", {"metadata": {"name": NAMESPACE}})
    yield
    kube.api.request("DELETE", f"/api/v1/namespaces/{NAMESPACE}")
    monkeypatch.undo()


@pytest.fixture
def real(monkeypatch, fake):
    """Restore Docker functions so runtime helpers use their native implementations."""
    for name, original in fake.originals.items():
        monkeypatch.setattr(docker, name, original)


@pytest.fixture
def home(alice, zoo) -> str:
    with db_manager.session() as db:
        sandbox = zoo.sandbox_api.create(CreateSandboxRequest(kind="code"), member(), db)
        config = {**json.loads(sandbox.config), "image": IMAGE}
        db.update_sandbox(name=sandbox.name, resources=sandbox.resources, config=json.dumps(config), id=sandbox.id)
    return sandbox.id


def tar(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


def untar(chunks) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as archive:
        return {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}  # type: ignore[union-attr]


def test_requirements_check():
    checks = {c["name"]: c for c in kube.check(probe=True)}
    for name in ("api", "namespace", "runtime class", "nodes", "storage"):
        assert checks[name]["ok"], checks[name]
    probes = [c for n, c in checks.items() if n.startswith("node ")]
    assert probes and all(c["ok"] for c in probes), probes
    # nothing of the probe is left behind
    assert not kube.api.get(kube.pods_path(NAMESPACE), labelSelector="app.kubernetes.io/component=probe")["items"]


def test_a_sandbox_pod_executes_and_transfers_archives(real, home):
    sandbox_id = home
    rid, host = kube.run_container(
        kube.pod_name(sandbox_id), IMAGE, sandbox_id, {"ZOO_SANDBOX_ID": sandbox_id}, desktop=False
    )
    try:
        assert rid == f"k8s:{NAMESPACE}/zoo-sandbox-{sandbox_id}" and host is None
        assert kube.is_running(rid)
        pod = kube.container(rid)
        # the image's home was copied into the new claim, owned by the sandbox user
        result = pod.execute(["sh", "-c", "test -f ~/.bashrc && echo seeded; whoami; stat -c %U ~"], user="zoo")
        assert result == (0, b"seeded\nzoo\nzoo\n", b""), result
        result = pod.execute(
            ["sh", "-c", 'echo "$A $PWD"; echo err >&2; exit 3'], environment={"A": "1"}, workdir="/tmp"
        )
        assert result == (3, b"1 /tmp\n", b"err\n")
        assert pod.execute("whoami", user="root") == (0, b"root\n", b"")

        # archives, both ways, binary-safe and bigger than one websocket frame
        blob = os.urandom(300_000)
        kube.import_dir(rid, "/home/zoo/.config/app", tar({"state.bin": blob, "a.txt": b"hello"}))
        files = untar(pod.read_archive("/home/zoo/.config/app"))
        assert files["app/state.bin"] == blob and files["app/a.txt"] == b"hello"
        pod.write_archive("/home/zoo/.config/app", tar({"a.txt": b"updated"}))
        assert pod.execute(["cat", "/home/zoo/.config/app/a.txt"]) == (0, b"updated", b"")
        assert pod.execute(["stat", "-c", "%U", "/home/zoo/.config/app/state.bin"])[1] == b"zoo\n"
        kube.write_secrets(rid, {"TOKEN": "s3cret"})
        assert pod.execute(["cat", "/run/zoo/env.json"])[1] == b'{"TOKEN": "s3cret"}'

        # a retried boot adopts the running pod rather than starting another
        uid = pod.obj["metadata"]["uid"]
        again, _ = kube.run_container(kube.pod_name(sandbox_id), IMAGE, sandbox_id, {}, desktop=False)
        assert again == rid and kube.container(rid).obj["metadata"]["uid"] == uid

        # the warm pool's pause: every process but init stops, and wakes again
        pod.execute(["sh", "-c", "nohup sleep 300 >/dev/null 2>&1 &"])
        state = "for p in /proc/[0-9]*; do [ $(cat $p/comm) = sleep ] && [ ${p#/proc/} != 1 ] && cut -d' ' -f3 $p/stat; done"
        kube.pause(rid)
        assert b"T" in pod.execute(["sh", "-c", state])[1]
        kube.resume(rid)
        assert b"T" not in pod.execute(["sh", "-c", state])[1]

        assert kube.free_memory(ttl=0) is not None
        assert kube.info()["ContainersRunning"] >= 1
    finally:
        kube.remove_container(rid)
        assert not kube.is_running(rid)
        kube.remove_volume(sandbox_id)
    kube.gone(kube.claims_path(NAMESPACE, kube.claim_name(sandbox_id)), 60)


def test_snapshots_restore_a_stopped_sandbox(real, home, real_object_store):
    sandbox_id, snapshot_id = home, str(uuid.uuid4())
    rid, _ = kube.run_container(kube.pod_name(sandbox_id), IMAGE, sandbox_id, {}, desktop=False)
    try:
        pod = kube.container(rid)
        pod.execute(["sh", "-c", "echo before > /home/zoo/note"], user="zoo")
        # taken while the sandbox runs, on its node
        assert kube.snapshot(sandbox_id, snapshot_id) > 0
        assert objects.keys(kube.snapshot_prefix(snapshot_id))
        pod.execute(["sh", "-c", "echo after > /home/zoo/note"], user="zoo")
        kube.remove_container(rid)
        kube.restore_snapshot(sandbox_id, snapshot_id)
        rid, _ = kube.run_container(kube.pod_name(sandbox_id), IMAGE, sandbox_id, {}, desktop=False)
        assert kube.container(rid).execute(["cat", "/home/zoo/note"])[1] == b"before\n"
        kube.remove_snapshot(snapshot_id)
        assert objects.keys(kube.snapshot_prefix(snapshot_id)) == []
        kube.gone(kube.snapshots_path(NAMESPACE, kube.snapshot_name(snapshot_id)), 60)
        wait_helpers_gone()
    finally:
        kube.remove_container(rid)
        kube.remove_volume(sandbox_id)
        kube.remove_snapshot(snapshot_id)


def wait_helpers_gone():
    deadline = time.monotonic() + 60
    while kube.api.get(kube.pods_path(NAMESPACE), labelSelector="app.kubernetes.io/component=helper")["items"]:
        assert time.monotonic() < deadline, "helper pods left behind"
        time.sleep(1)
