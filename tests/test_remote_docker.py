"""Remote Linux placement and lifecycle use the enrolled host throughout an operation."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from db.connection import db_manager
from server import docker, remote_docker, remote_snapshots, runtime
from server.errors import ZooError
from tests.conftest import GATEWAY, present, runtime_of, sql
from tests.test_nodes import fake_session, join, new_token
from tests.test_sandboxes import add_server


@pytest.fixture
def remote_hosts(monkeypatch, fake):
    """Keep real remote dispatch and Docker context management; replace only the engine connection."""
    hosts = {}
    prepared = []

    def host(server_id):
        return hosts.setdefault(
            server_id,
            remote_docker.Host(SimpleNamespace(name=server_id), f"zoo-{server_id}", "runc", threading.RLock()),
        )

    def prepare(server_id, image, egress=False):
        prepared.append((server_id, image, egress))
        return host(server_id)

    monkeypatch.setattr(remote_snapshots, "snapshot", fake.snapshot)
    monkeypatch.setattr(remote_snapshots, "restore_snapshot", fake.restore_snapshot)
    monkeypatch.setattr(remote_docker, "host", host)
    monkeypatch.setattr(remote_docker, "prepare", prepare)
    monkeypatch.setenv("ZOO_API_URL", "https://zoo.example.com")
    return hosts, prepared


def ready(server_id):
    fake_session(server_id, 16 << 30)
    sql(
        "UPDATE nodes SET drivers = ? WHERE server_id = ?",
        json.dumps([{"name": "docker", "available": True, "detail": "Linux Docker engine"}]),
        server_id,
    )


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
@pytest.mark.parametrize("kind", ["desktop", "browser", "code"])
def test_all_host_platforms_run_linux_workloads(client, alice, make_sandbox, fake, remote_hosts, platform, kind):
    server_id = add_server("alice@example.com", platform)
    ready(server_id)
    sandbox = make_sandbox(kind, server_id=server_id)
    assert sandbox["status"] == "running", sandbox
    assert sandbox["server_id"] == server_id
    placed, raw_id = remote_docker.parse(runtime_of(sandbox["id"]))
    assert placed == server_id
    container = fake.containers[raw_id]
    assert container.image == runtime.default_image(kind)
    assert container.desktop == (kind != "code")
    assert container.env["ZOO_GUEST_URL"] == "wss://zoo.example.com/guest/connect"
    with db_manager.session() as db:
        stored = present(db.get_sandbox(id=sandbox["id"]))
    assert json.loads(stored.config)["remote_server_id"] == server_id
    assert remote_hosts[1][0] == (server_id, runtime.default_image(kind), True)


@pytest.mark.parametrize("control_runtime", ["docker", "kube"])
def test_remote_pause_resume_snapshot_restore_and_delete_stay_on_host(
    client, alice, make_sandbox, fake, monkeypatch, remote_hosts, control_runtime
):
    server_id = add_server("alice@example.com", "linux")
    ready(server_id)
    sandbox = make_sandbox("desktop", server_id=server_id)
    assert sandbox["status"] == "running", sandbox
    sid, rid = sandbox["id"], runtime_of(sandbox["id"])
    _, raw_id = remote_docker.parse(rid)
    # A control plane on Kubernetes must not send remote Docker containers to Kubernetes vm-control.
    monkeypatch.setattr(runtime, "NAME", control_runtime)
    snap = client.post(f"/sandboxes/{sid}/snapshots", headers=alice)
    assert snap.status_code == 202, snap.text
    assert snap.json()["state"] == "ready"
    assert client.post(f"/sandboxes/{sid}/stop", headers=alice).status_code == 200
    assert fake.containers[raw_id].paused
    assert client.post(f"/sandboxes/{sid}/start", headers=alice).status_code == 200
    assert runtime_of(sid) == rid
    assert not fake.containers[raw_id].paused
    assert client.post(f"/sandboxes/{sid}/stop", headers=alice).status_code == 200
    restored = client.post(f"/sandboxes/{sid}/snapshots/{snap.json()['id']}/restore", headers=alice)
    assert restored.status_code == 200, restored.text
    assert ("restore_snapshot", sid, {"snapshot_id": snap.json()["id"]}) in fake.calls
    assert client.delete(f"/sandboxes/{sid}", headers=alice).status_code == 200
    assert sid not in fake.volumes
    assert not fake.containers
    assert all(server == server_id for server, _, _ in remote_hosts[1])


def test_placement_does_not_expose_another_workspaces_host(client, alice, bob, remote_hosts):
    foreign = add_server("bob@example.com", "linux")
    ready(foreign)
    res = client.post("/sandboxes", json={"kind": "code", "server_id": foreign}, headers=alice)
    assert res.status_code == 404
    assert not remote_hosts[1]


@pytest.mark.parametrize("kind", ["macos", "windows"])
def test_linux_host_cannot_run_native_other_os(client, alice, kind):
    server_id = add_server("alice@example.com", "linux")
    ready(server_id)
    res = client.post("/sandboxes", json={"kind": kind, "server_id": server_id}, headers=alice)
    assert res.status_code == 400
    assert "add a" in res.json()["error"]["message"]


@pytest.mark.parametrize("condition", ["disconnected", "stale", "docker_missing", "windows_engine"])
def test_remote_linux_requires_a_connected_fresh_linux_docker_report(client, alice, condition):
    server_id = add_server("alice@example.com", "linux")
    ready(server_id)
    if condition == "disconnected":
        GATEWAY.nodes.clear()
    elif condition == "stale":
        sql("UPDATE nodes SET seen_at = now() - interval '10 minutes' WHERE server_id = ?", server_id)
    else:
        sql(
            "UPDATE nodes SET drivers = ? WHERE server_id = ?",
            json.dumps([{"name": "docker", "available": False, "detail": condition}]),
            server_id,
        )
    res = client.post("/sandboxes", json={"kind": "code", "server_id": server_id}, headers=alice)
    assert res.status_code == 503, res.text
    assert res.json()["error"]["code"] == "unavailable"
    server = client.get("/servers", headers=alice).json()[0]
    assert "linux" not in server["capabilities"]


def test_runtime_dispatch_keeps_host_context_isolated_between_threads(monkeypatch):
    barrier = threading.Barrier(2)
    local = docker.LocalDocker()
    monkeypatch.setattr(local, "client", SimpleNamespace(name="control-plane"))
    hosts = {
        name: remote_docker.Host(SimpleNamespace(name=name), f"net-{name}", "runc", threading.RLock())
        for name in ("first", "second")
    }
    monkeypatch.setattr(remote_docker, "host", hosts.__getitem__)

    def operation(raw_id):
        barrier.wait(timeout=3)
        return raw_id, local.name, docker.network_name()

    monkeypatch.setattr(docker, "container", operation)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(runtime.container, ("linux:first:a", "linux:second:b")))
    assert results == [("a", "first", "net-first"), ("b", "second", "net-second")]
    assert local.name == "control-plane"
    assert docker.network_name() == docker.NETWORK


def test_host_context_restores_after_an_operation_fails(monkeypatch):
    host = remote_docker.Host(SimpleNamespace(name="remote"), "remote-net", "runc", threading.RLock())
    monkeypatch.setattr(remote_docker, "host", lambda _: host)
    monkeypatch.setattr(docker, "pause", Mock(side_effect=ConnectionError("connection lost")))
    with pytest.raises(ConnectionError):
        runtime.pause("linux:remote:container")
    assert docker._host.get() is None


@pytest.mark.parametrize("rid", ["linux:x", "linux:x:y:z", "linux:../x:y", "linux:x:", "linux:x:a/b"])
def test_invalid_runtime_ids_cannot_select_a_host(rid):
    with pytest.raises(ZooError, match="invalid remote Linux runtime ID"):
        remote_docker.Runtime(rid)


def test_adapter_cannot_switch_hosts_mid_operation(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(remote_docker, "host", connect)
    adapter = remote_docker.Runtime("linux:first:container")
    with pytest.raises(ZooError, match="cannot switch remote host"):
        adapter.pause("linux:second:container")
    connect.assert_not_called()


def test_volume_cleanup_remembers_remote_host_when_the_sandbox_row_is_gone(monkeypatch):
    operation = Mock()
    monkeypatch.setattr(remote_docker, "volume_operation", operation)
    monkeypatch.setattr(runtime, "NAME", "kube")
    runtime.remove_volume_id("sandbox", {"remote_server_id": "host"})
    operation.assert_called_once_with("host", "remove_volume", "sandbox")


@pytest.mark.parametrize("os_name,arch", [("linux", "amd64"), ("linux", "arm64"), ("darwin", "amd64")])
def test_node_enrollment_supports_linux_and_intel_mac(client, alice, os_name, arch):
    platform = "macos" if os_name == "darwin" else "linux"
    made = new_token(client, alice, platform=platform)
    _, response = join(client, made["token"], os_name, arch=arch)
    assert response.status_code == 200, response.text
    with db_manager.session() as db:
        server = present(db.get_server(id=response.json()["server_id"]))
        node = present(db.get_node_by_server(server_id=server.id))
    assert server.capabilities == "linux"
    assert (node.os, node.arch) == (os_name, arch)


def test_local_runtime_reconciliation_does_not_remove_healthy_remote_sandboxes(
    alice, make_sandbox, fake, remote_hosts, zoo
):
    server_id = add_server("alice@example.com", "linux")
    ready(server_id)
    sandbox = make_sandbox("desktop", server_id=server_id)
    rid = runtime_of(sandbox["id"])
    _, raw_id = remote_docker.parse(rid)
    zoo.sandbox_api.runtime_event({"type": "reconcile", "running_ids": []})
    with db_manager.session() as db:
        stored = present(db.get_sandbox(id=sandbox["id"]))
    assert stored.status == "running"
    assert stored.runtime_id == rid
    assert raw_id in fake.containers


def test_auto_placement_balances_only_connected_docker_hosts(alice, make_sandbox, remote_hosts):
    first = add_server("alice@example.com", "linux", "first")
    second = add_server("alice@example.com", "windows", "second")
    add_server("alice@example.com", "macos", "offline")
    ready(first)
    ready(second)
    one = make_sandbox("code", server_id="auto")
    two = make_sandbox("code", server_id="auto")
    assert one["status"] == two["status"] == "running"
    assert {one["server_id"], two["server_id"]} == {first, second}


def test_remote_host_rejects_a_windows_container_engine(alice, monkeypatch):
    server_id = add_server("alice@example.com", "windows")
    engine = Mock()
    engine.info.return_value = {"OSType": "windows", "Runtimes": {}}
    monkeypatch.setattr(remote_docker, "new_client", lambda _: engine)
    monkeypatch.setattr(remote_docker, "_hosts", {})
    with pytest.raises(ZooError, match="must run Linux containers"):
        remote_docker.host(server_id)
    engine.close.assert_called_once_with()


def test_required_kata_isolation_cannot_silently_fall_back_on_remote_host(alice, monkeypatch):
    server_id = add_server("alice@example.com", "linux")
    engine = Mock()
    engine.info.return_value = {"OSType": "linux", "Runtimes": {"runc": {}}}
    monkeypatch.setattr(remote_docker, "new_client", lambda _: engine)
    monkeypatch.setattr(remote_docker, "_hosts", {})
    monkeypatch.setattr(docker, "ISOLATION", "kata")
    with pytest.raises(ZooError, match="no Kata runtime"):
        remote_docker.host(server_id)
    engine.close.assert_called_once_with()
