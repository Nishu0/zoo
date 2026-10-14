"""Home disk snapshots: taken and restored on the sandbox's host through the job queue, in place of tar backups
(which stay, as an export)."""

from db.connection import db_manager
from server import runtime, sandbox_api
from tests.conftest import sql
from tests.test_sandboxes import add_server


def snapshots(client, alice, sandbox_id) -> list[dict]:
    res = client.get(f"/sandboxes/{sandbox_id}/snapshots", headers=alice)
    assert res.status_code == 200, res.text
    return res.json()


def test_a_running_linux_sandbox_snapshots_and_restores_once_stopped(client, alice, sandbox, fake):
    res = client.post(f"/sandboxes/{sandbox['id']}/snapshots", json={"name": "before upgrade"}, headers=alice)
    assert res.status_code == 202, res.text
    snap = res.json()
    # the job ran inline: the snapshot is ready, with the size the host reported
    assert snap["name"] == "before upgrade" and snap["state"] == "ready" and snap["size_bytes"] == 4096
    assert fake.snapshots == {snap["id"]: sandbox["id"]}
    unnamed = client.post(f"/sandboxes/{sandbox['id']}/snapshots", headers=alice).json()
    assert unnamed["name"] == "Snapshot 2"
    assert [s["id"] for s in snapshots(client, alice, sandbox["id"])] == [unnamed["id"], snap["id"]]

    # restoring replaces the disk, so the sandbox has to be stopped
    restore = f"/sandboxes/{sandbox['id']}/snapshots/{snap['id']}/restore"
    assert client.post(restore, headers=alice).status_code == 409
    assert client.post(f"/sandboxes/{sandbox['id']}/stop", headers=alice).status_code in (200, 202)
    res = client.post(restore, headers=alice)
    assert res.status_code == 200, res.text
    assert ("restore_snapshot", sandbox["id"], {"snapshot_id": snap["id"]}) in fake.calls
    assert client.get(f"/sandboxes/{sandbox['id']}", headers=alice).json()["status"] == "stopped"

    assert client.delete(f"/sandboxes/{sandbox['id']}/snapshots/{snap['id']}", headers=alice).status_code == 204
    assert snap["id"] not in fake.snapshots
    assert [s["id"] for s in snapshots(client, alice, sandbox["id"])] == [unnamed["id"]]


def test_snapshots_are_private_and_limited(client, alice, bob, sandbox, monkeypatch):
    monkeypatch.setattr(sandbox_api, "MAX_SNAPSHOTS", 2)
    for _ in range(2):
        assert client.post(f"/sandboxes/{sandbox['id']}/snapshots", headers=alice).status_code == 202
    res = client.post(f"/sandboxes/{sandbox['id']}/snapshots", headers=alice)
    assert res.status_code == 409 and "at most 2" in res.json()["error"]["message"]
    assert client.get(f"/sandboxes/{sandbox['id']}/snapshots", headers=bob).status_code == 404
    snap_id = snapshots(client, alice, sandbox["id"])[0]["id"]
    assert client.delete(f"/sandboxes/{sandbox['id']}/snapshots/{snap_id}", headers=bob).status_code == 404
    other = client.post("/sandboxes", json={"kind": "code"}, headers=alice).json()
    # a snapshot only restores into its own sandbox
    assert client.post(f"/sandboxes/{other['id']}/snapshots/{snap_id}/restore", headers=alice).status_code == 404


def test_a_failed_snapshot_is_kept_with_its_error(client, alice, sandbox, fake):
    fake.flaky["snapshot"] = 1
    snap = client.post(f"/sandboxes/{sandbox['id']}/snapshots", headers=alice).json()
    assert snap["state"] == "failed" and "connection reset" in snap["error"]
    # the sandbox itself is fine, and a failed snapshot can't be restored
    assert client.get(f"/sandboxes/{sandbox['id']}", headers=alice).json()["status"] == "running"
    client.post(f"/sandboxes/{sandbox['id']}/stop", headers=alice)
    res = client.post(f"/sandboxes/{sandbox['id']}/snapshots/{snap['id']}/restore", headers=alice)
    assert res.status_code == 409 and "failed" in res.json()["error"]["message"]


def test_vm_snapshots_need_the_vm_stopped(client, alice, sandbox):
    # a Windows VM, as far as the API can tell
    sql(
        "UPDATE sandboxes SET kind = 'windows', server_id = ? WHERE id = ?",
        add_server("alice@example.com", "windows"),
        sandbox["id"],
    )
    res = client.post(f"/sandboxes/{sandbox['id']}/snapshots", headers=alice)
    assert res.status_code == 409 and "stop the sandbox" in res.json()["error"]["message"]


def test_linux_snapshots_are_removed_with_the_sandbox(client, alice, sandbox, fake):
    snap = client.post(f"/sandboxes/{sandbox['id']}/snapshots", headers=alice).json()
    client.post(f"/sandboxes/{sandbox['id']}/stop", headers=alice)
    assert client.delete(f"/sandboxes/{sandbox['id']}", headers=alice).status_code in (200, 202, 204)
    assert fake.snapshots == {} and ("remove_snapshot", snap["id"], {}) in fake.calls
    with db_manager.session() as db:
        assert db.get_snapshot(id=snap["id"]) is None


def test_docker_sandboxes_cannot_move(client, alice, sandbox):
    client.post(f"/v1/sandboxes/{sandbox['id']}/stop", headers=alice)
    response = client.post(f"/v1/sandboxes/{sandbox['id']}/move", json={"node_name": "node-b"}, headers=alice)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_supported"


def test_kubernetes_moves_keep_existing_snapshots(client, alice, sandbox, fake, monkeypatch):
    snapshot = client.post(f"/v1/sandboxes/{sandbox['id']}/snapshots", headers=alice).json()
    client.post(f"/v1/sandboxes/{sandbox['id']}/stop", headers=alice)
    monkeypatch.setattr(runtime, "NAME", "kube")
    moved = []
    monkeypatch.setattr(runtime, "move_volume", lambda sandbox_id, node: moved.append((sandbox_id, node)))
    response = client.post(f"/v1/sandboxes/{sandbox['id']}/move", json={"node_name": "node-b"}, headers=alice)
    assert response.status_code == 200
    assert moved == [(sandbox["id"], "node-b")]
    assert snapshot["id"] in fake.snapshots
    assert client.get(f"/v1/sandboxes/{sandbox['id']}/snapshots", headers=alice).json()[0]["id"] == snapshot["id"]


def test_kubernetes_moves_require_a_node(client, alice, sandbox, monkeypatch):
    client.post(f"/v1/sandboxes/{sandbox['id']}/stop", headers=alice)
    monkeypatch.setattr(runtime, "NAME", "kube")
    for payload in ({}, {"server_id": "registered-server"}, {"server_id": "registered-server", "node_name": "node-b"}):
        response = client.post(f"/v1/sandboxes/{sandbox['id']}/move", json=payload, headers=alice)
        assert response.status_code == 400 and response.json()["error"]["code"] == "bad_request"
