import io
import json
import tarfile
from unittest.mock import Mock

import pytest

from db.connection import db_manager
from db.generated.query import CreatePoolSandboxParams
from server import kube, pool, runtime
from tests.conftest import present, sql


@pytest.fixture
def warm(zoo, monkeypatch):
    """A pool that boots in the calling thread, with guests that connect at once."""
    monkeypatch.setattr(pool, "guest_ready", lambda server: True)
    monkeypatch.setattr(pool.hub, "wait_for_guest", lambda sandbox_id, timeout=30: True)
    monkeypatch.setattr(runtime, "refill_volumes", Mock())
    zoo.sandbox_api.pool.inline = True
    zoo.sandbox_api.pool.claimed.clear()
    yield zoo.sandbox_api.pool
    zoo.sandbox_api.pool.inline = False


def rows():
    with db_manager.session() as db:
        return list(db.list_pool_sandboxes())


def test_only_admins_size_the_local_pool(client, alice, admin):
    assert client.put("/pool", json={"kind": "desktop", "size": 1}, headers=alice).status_code == 403
    assert client.get("/pool", headers=alice).json() == []
    res = client.put("/pool", json={"kind": "desktop", "size": 2}, headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["size"] == 2 and res.json()["server_name"] == "This installation"
    assert client.put("/pool", json={"kind": "desktop", "size": 99}, headers=admin).status_code == 422


def test_create_claims_a_warm_sandbox(client, admin, alice, warm, fake):
    client.put("/pool", json={"kind": "desktop", "size": 1}, headers=admin)
    warm.fill()
    [pooled] = rows()
    assert pooled.status == "idle"
    assert json.loads(pooled.config)["home_claim"]
    container = fake.containers[pooled.runtime_id]
    assert "ZOO_VNC_PASSWORD" in container.env and container.sandbox_id == pooled.id

    [secret] = client.post("/vault/secrets", json={"name": "API_KEY", "value": "sk-warm-123456"}, headers=alice).json()
    res = client.post("/sandboxes", json={"kind": "desktop", "secret_ids": [secret["id"]]}, headers=alice)
    sandbox = client.get(f"/sandboxes/{res.json()['id']}", headers=alice).json()
    assert sandbox["id"] == pooled.id and sandbox["status"] == "running"
    with db_manager.session() as db:
        stored = present(db.get_sandbox(id=pooled.id))
    assert stored.runtime_id == pooled.runtime_id
    assert json.loads(stored.config)["home_claim"] == json.loads(pooled.config)["home_claim"]
    # the pooled container's VNC password carries over, and the secrets reach the guest's file, not the env
    assert json.loads(stored.config)["vnc_password"] == json.loads(pooled.config)["vnc_password"]
    assert "API_KEY" not in container.env
    with tarfile.open(fileobj=io.BytesIO(container.dirs["/run/zoo"])) as tar:
        member = tar.getmember("env.json")
        assert member.mode == 0o600
        assert json.load(present(tar.extractfile(member))) == {"API_KEY": "sk-warm-123456"}
    assert client.get("/pool", headers=admin).json()[0]["claimed"] == 1

    # the next fill boots a replacement
    assert rows() == []
    warm.fill()
    assert [r.status for r in rows()] == ["idle"]


def test_cold_boot_without_a_pool(client, alice, warm, fake):
    sandbox = client.post("/sandboxes", json={"kind": "code"}, headers=alice).json()
    with db_manager.session() as db:
        config = json.loads(present(db.get_sandbox(id=sandbox["id"])).config)
    assert "pooled" not in config
    assert config["home_claim"] == f"zoo-home-{sandbox['id']}"


def test_claim_preserves_the_pooled_spare_home_and_node(client, admin, alice, warm):
    assert client.put("/pool", json={"kind": "code", "size": 1}, headers=admin).status_code == 200
    warm.fill()
    [pooled] = rows()
    config = {**json.loads(pooled.config), "home_claim": "zoo-spare-home-a", "node_name": "node-a"}
    sql("UPDATE pool_sandboxes SET config = ? WHERE id = ?", json.dumps(config), pooled.id)

    response = client.post("/sandboxes", json={"kind": "code"}, headers=alice)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == pooled.id
    with db_manager.session() as db:
        stored = present(db.get_sandbox(id=pooled.id))
        assert db.get_pool_sandbox_for_update(id=pooled.id) is None
    claimed = json.loads(stored.config)
    assert claimed["home_claim"] == "zoo-spare-home-a"
    assert claimed["node_name"] == "node-a"
    assert claimed["pooled"] is True
    assert stored.runtime_id == pooled.runtime_id


def test_fill_refills_volumes_before_starting_pooled_sandboxes(client, admin, warm, monkeypatch):
    assert client.put("/pool", json={"kind": "code", "size": 1}, headers=admin).status_code == 200
    events = []
    refill = Mock(side_effect=lambda: events.append("refill"))
    start = Mock(side_effect=lambda kind, server: events.append("boot"))
    monkeypatch.setattr(runtime, "refill_volumes", refill)
    monkeypatch.setattr(warm, "start", start)
    warm.fill()
    assert events == ["refill", "boot"]
    refill.assert_called_once_with()
    start.assert_called_once_with("code", None)


@pytest.mark.parametrize("previous_home", [None, "zoo-previous-home-a"])
def test_discard_cleans_persisted_claims_after_deleting_the_pool_row(monkeypatch, previous_home):
    config = {"home_claim": "zoo-spare-home-a", "node_name": "node-a"}
    if previous_home is not None:
        config["previous_home_claim"] = previous_home
    with db_manager.session() as db:
        row = present(
            db.create_pool_sandbox(
                CreatePoolSandboxParams(
                    id="discarded", kind="code", server_id=None, image="img:1", config=json.dumps(config)
                )
            )
        )
    monkeypatch.setattr(runtime, "NAME", "kube")
    monkeypatch.setattr(runtime, "LINUX", kube)
    remove_container = Mock()
    lookup = Mock(side_effect=AssertionError("cleanup must use saved config after deleting the row"))
    deleted = []

    def delete_claim(namespace, name, *, wait):
        with db_manager.session() as db:
            assert db.get_pool_sandbox_for_update(id=row.id) is None
        deleted.append((namespace, name, wait))

    monkeypatch.setattr(runtime, "remove_named_container", remove_container)
    monkeypatch.setattr(kube, "home_config", lookup)
    monkeypatch.setattr(kube, "delete_claim", delete_claim)
    manager = pool.Pool()
    manager.discard(row, None)
    claims = [previous_home, config["home_claim"]] if previous_home is not None else [config["home_claim"]]
    assert deleted == [(kube.NAMESPACE, name, True) for name in claims]
    remove_container.assert_called_once_with(f"zoo-sandbox-{row.id}")
    lookup.assert_not_called()
    manager.discard(row, None)
    assert len(deleted) == len(claims)
    remove_container.assert_called_once()


@pytest.mark.parametrize("backend", ["docker", "kube"])
def test_volume_refill_only_calls_the_kubernetes_backend(monkeypatch, backend):
    refill = Mock()
    monkeypatch.setattr(runtime, "NAME", backend)
    monkeypatch.setattr(kube, "refill_spare_volumes", refill)
    runtime.refill_volumes()
    assert refill.call_count == (1 if backend == "kube" else 0)


def test_a_lost_pooled_container_is_discarded_and_the_create_boots_cold(client, admin, alice, warm, fake):
    client.put("/pool", json={"kind": "code", "size": 1}, headers=admin)
    warm.fill()
    [pooled] = rows()
    fake.containers[pooled.runtime_id].running = False
    created = client.post("/sandboxes", json={"kind": "code"}, headers=alice).json()
    assert created["id"] != pooled.id and created["status"] == "provisioning"
    assert client.get(f"/sandboxes/{created['id']}", headers=alice).json()["status"] == "running"
    assert pooled.runtime_id not in fake.containers
    with db_manager.session() as db:
        assert db.get_sandbox(id=pooled.id) is None


def test_shrinking_and_stale_images_drain_the_pool(client, admin, warm, fake, monkeypatch):
    client.put("/pool", json={"kind": "code", "size": 2}, headers=admin)
    warm.fill()
    assert len(rows()) == 2
    client.put("/pool", json={"kind": "code", "size": 1}, headers=admin)
    warm.fill()
    assert len(rows()) == 1
    monkeypatch.setattr("server.runtime.CODE_IMAGE", "zoo-code:next")
    warm.fill()
    [fresh] = rows()
    assert fresh.image == "zoo-code:next"
    assert len(fake.containers) == 1


def test_a_failed_boot_holds_the_pool_back(client, admin, warm, fake):
    fake.fail_boot = "image pull failed"
    client.put("/pool", json={"kind": "code", "size": 2}, headers=admin)
    warm.fill()
    assert [r.status for r in rows()] == ["failed", "failed"]
    fake.fail_boot = None
    warm.fill()
    assert [r.status for r in rows()] == ["failed", "failed"]
    entry = next(e for e in client.get("/pool", headers=admin).json() if e["kind"] == "code")
    assert entry["error"] == "image pull failed" and entry["idle"] == 0


def test_a_failed_hand_off_discards_that_sandbox_and_claims_another(client, admin, alice, warm, fake, zoo, monkeypatch):
    client.put("/pool", json={"kind": "desktop", "size": 2}, headers=admin)
    warm.fill()
    pooled = {r.id: r.runtime_id for r in rows()}
    assert len(pooled) == 2
    hand_off = zoo.sandbox_api.hand_off
    tried = []

    async def flaky(sandbox):
        tried.append(sandbox.id)
        if len(tried) == 1:
            raise RuntimeError("hand-off broke")
        await hand_off(sandbox)

    monkeypatch.setattr(zoo.sandbox_api, "hand_off", flaky)
    res = client.post("/sandboxes", json={"kind": "desktop"}, headers=alice)
    assert res.status_code == 201, res.text
    failed, served = tried
    assert res.json()["id"] == served and res.json()["status"] == "running"
    assert client.get(f"/sandboxes/{failed}", headers=alice).status_code == 404
    assert pooled[failed] not in fake.containers and pooled[served] in fake.containers
