import pytest

from apps.server import admin_api, backups
from tests.conftest import STORE


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    monkeypatch.setattr(admin_api, "resolve", lambda hostname: ["203.0.113.7"])


def test_admin_routes_need_an_admin(client, alice):
    for method, path in [
        ("get", "/admin/users"),
        ("get", "/admin/sandboxes"),
        ("get", "/admin/domains"),
        ("post", "/admin/backups"),
        ("get", "/admin/backups"),
    ]:
        assert client.request(method, path, headers=alice).status_code == 403, path
    assert client.post("/admin/domains", json={"hostname": "zoo.example.com"}, headers=alice).status_code == 403


def test_admin_sees_all_users_and_sandboxes(client, admin, alice, make_sandbox):
    make_sandbox()
    assert {u["email"] for u in client.get("/admin/users", headers=admin).json()} == {
        "admin@example.com",
        "alice@example.com",
    }
    assert len(client.get("/admin/sandboxes", headers=admin).json()) == 1


def test_domains(client, admin):
    res = client.post("/admin/domains", json={"hostname": "zoo.example.com"}, headers=admin)
    assert res.status_code == 201
    domain = res.json()
    assert domain["url"] == "https://zoo.example.com"
    assert domain["addresses"] == ["203.0.113.7"]
    assert client.post("/admin/domains", json={"hostname": "zoo.example.com"}, headers=admin).status_code == 409
    assert client.post("/admin/domains", json={"hostname": "Not A Host"}, headers=admin).status_code == 422

    assert client.get("/domains/check", params={"domain": "zoo.example.com"}).json() == {"ok": True}
    assert client.get("/domains/check", params={"domain": "evil.example.com"}).status_code == 404

    assert client.delete(f"/admin/domains/{domain['id']}", headers=admin).status_code == 204
    assert client.get("/admin/domains", headers=admin).json() == []
    assert client.get("/domains/check", params={"domain": "zoo.example.com"}).status_code == 404


def test_backups(client, admin, monkeypatch):
    # pg_dump isn't run here: the dump is a stand-in in the object store
    def dump():
        STORE["backups/db/20261009T120000Z.dump"] = b"dump"
        return "backups/db/20261009T120000Z.dump"

    monkeypatch.setattr(backups, "dump_database", dump)
    res = client.post("/admin/backups", headers=admin)
    assert res.status_code == 201, res.text
    assert res.json() == {"key": "backups/db/20261009T120000Z.dump", "size": 4, "created_at": "2026-10-09 12:00:00"}
    assert [b["key"] for b in client.get("/admin/backups", headers=admin).json()] == [res.json()["key"]]
