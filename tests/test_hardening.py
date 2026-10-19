import os

import pytest
from starlette.websockets import WebSocketDisconnect

from apps.server import limits, security
from db.connection import db_manager
from tests.conftest import PASSWORD, runtime_of, signup, sql


def ticket(client, headers, sandbox_id) -> str:
    res = client.post(f"/sandboxes/{sandbox_id}/vnc-ticket", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["expires_in"] == 30
    return res.json()["ticket"]


def rejected(client, url: str) -> bool:
    try:
        with client.websocket_connect(url) as ws:
            ws.receive_bytes()
    except WebSocketDisconnect as e:
        return e.code == 1008
    return False


def test_vnc_password_is_kept_across_restarts_and_stored_encrypted(client, alice, sandbox, fake):
    sid = sandbox["id"]
    first = fake.containers[runtime_of(sid)].env["ZOO_VNC_PASSWORD"]
    client.post(f"/sandboxes/{sid}/stop", headers=alice)
    client.post(f"/sandboxes/{sid}/start", headers=alice)
    assert fake.containers[runtime_of(sid)].env["ZOO_VNC_PASSWORD"] == first
    with db_manager.session() as db:
        stored = db.get_sandbox(id=sid)
    assert first not in stored.config and security.vnc_password(stored) == first


def test_code_sandboxes_get_no_vnc_password(make_sandbox, fake):
    code = make_sandbox("code")
    assert "ZOO_VNC_PASSWORD" not in fake.containers[runtime_of(code["id"])].env


def test_tickets_are_single_use_and_bound_to_one_sandbox(client, alice, make_sandbox, monkeypatch):
    a, b = make_sandbox(), make_sandbox()
    spent = ticket(client, alice, a["id"])
    assert not rejected(client, f"/sandboxes/{a['id']}/ws?ticket={spent}")
    assert rejected(client, f"/sandboxes/{a['id']}/ws?ticket={spent}")
    assert rejected(client, f"/sandboxes/{b['id']}/ws?ticket={ticket(client, alice, a['id'])}")


def test_viewer_rejects_session_tokens_and_expired_tickets(client, alice, sandbox):
    token = alice["Authorization"].removeprefix("Bearer ")
    assert rejected(client, f"/sandboxes/{sandbox['id']}/ws?token={token}")
    assert rejected(client, f"/sandboxes/{sandbox['id']}/ws?ticket={token}")

    expired = ticket(client, alice, sandbox["id"])
    sql("UPDATE tickets SET expires_at = now() - interval '1 second'")
    assert rejected(client, f"/sandboxes/{sandbox['id']}/ws?ticket={expired}")


def test_only_the_owner_gets_a_ticket(client, alice, bob, sandbox):
    assert client.post(f"/sandboxes/{sandbox['id']}/vnc-ticket", headers=bob).status_code == 404
    assert client.post(f"/sandboxes/{sandbox['id']}/vnc-ticket").status_code in (401, 403)


def test_login_is_rate_limited_per_email(client, alice):
    wrong = {"email": "alice@example.com", "password": "wrong-password"}
    for _ in range(limits.LOGIN_PER_EMAIL.count):
        assert client.post("/auth/login", json=wrong).status_code == 401
    res = client.post("/auth/login", json={"email": "ALICE@example.com", "password": PASSWORD})
    assert res.status_code == 429
    assert int(res.headers["Retry-After"]) > 0


def test_login_is_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(limits.LOGIN_PER_IP, "count", 3)
    for i in range(3):
        assert client.post("/auth/login", json={"email": f"u{i}@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"email": "u9@example.com", "password": PASSWORD}).status_code == 429


def test_signup_is_rate_limited_per_ip(client):
    for i in range(limits.SIGNUP_PER_IP.count):
        signup(client, f"user{i}@example.com")
    res = client.post("/auth/signup", json={"email": "one-more@example.com", "password": PASSWORD})
    assert res.status_code == 429


def test_api_keys_are_rate_limited(client, alice, monkeypatch):
    monkeypatch.setattr(limits.API_KEY, "count", 2)
    key = {"Authorization": f"Bearer {client.post('/api-keys', json={'name': 'ci'}, headers=alice).json()['key']}"}
    assert client.get("/sandboxes", headers=key).status_code == 200
    assert client.get("/sandboxes", headers=key).status_code == 200
    assert client.get("/sandboxes", headers=key).status_code == 429
    # sessions aren't counted against the key
    assert client.get("/sandboxes", headers=alice).status_code == 200


def test_a_secrets_key_is_required(monkeypatch):
    monkeypatch.delenv("ZOO_SECRETS_KEY", raising=False)
    monkeypatch.delenv("ZOO_KMS", raising=False)
    with pytest.raises(RuntimeError, match="ZOO_SECRETS_KEY"):
        security.require_secrets_key()
    monkeypatch.setenv("ZOO_SECRETS_KEY", "a-separate-key")
    security.require_secrets_key()


def test_rotation_rewraps_vnc_passwords(sandbox, fake, monkeypatch):
    password = fake.containers[runtime_of(sandbox["id"])].env["ZOO_VNC_PASSWORD"]
    monkeypatch.setenv("ZOO_SECRETS_KEY_PREVIOUS", os.environ["ZOO_SECRETS_KEY"])
    monkeypatch.setenv("ZOO_SECRETS_KEY", "new-key")
    with db_manager.session() as db:
        assert security.rotate(db)["data_keys"] >= 1
    monkeypatch.delenv("ZOO_SECRETS_KEY_PREVIOUS")
    monkeypatch.setenv("JWT_SECRET", "jwt-secret-no-longer-decrypts-anything")
    security._data_keys.clear()
    with db_manager.session() as db:
        assert security.vnc_password(db.get_sandbox(id=sandbox["id"])) == password
