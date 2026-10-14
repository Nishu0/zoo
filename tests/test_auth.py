from server.writer import writer
from tests.conftest import PASSWORD, signup


def test_signup_login_and_me(client):
    headers = signup(client, "Carol@Example.com")
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "carol@example.com"

    res = client.post("/auth/login", json={"email": "carol@example.com", "password": PASSWORD})
    assert res.status_code == 200
    assert res.json()["user_id"] == me.json()["id"]


def test_duplicate_signup_is_rejected(client, alice):
    res = client.post("/auth/signup", json={"email": "alice@example.com", "password": PASSWORD})
    assert res.status_code == 400


def test_signup_validates_input(client):
    assert client.post("/auth/signup", json={"email": "not-an-email", "password": PASSWORD}).status_code == 422
    assert client.post("/auth/signup", json={"email": "d@example.com", "password": "short"}).status_code == 422


def test_wrong_password_and_unknown_user(client, alice):
    assert (
        client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong-password"}).status_code == 401
    )
    assert client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}).status_code == 401


def test_requests_without_valid_token_are_rejected(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/sandboxes", headers={"Authorization": "Bearer zoo_unknown"}).status_code == 401


def test_api_key_lifecycle(client, alice):
    created = client.post("/api-keys", json={"name": "ci"}, headers=alice)
    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith("zoo_")
    assert created.json()["key_prefix"] == key[:12]

    as_key = {"Authorization": f"Bearer {key}"}
    assert client.get("/auth/me", headers=as_key).json()["email"] == "alice@example.com"

    # last use is written in the background, batched
    writer.flush_sync()
    listed = client.get("/api-keys", headers=alice).json()
    assert [k["name"] for k in listed] == ["ci"]
    assert "key" not in listed[0]
    assert listed[0]["last_used_at"] is not None

    assert client.delete(f"/api-keys/{created.json()['id']}", headers=alice).status_code == 200
    assert client.get("/auth/me", headers=as_key).status_code == 401


def test_api_keys_are_private(client, alice, bob):
    key_id = client.post("/api-keys", json={"name": "mine"}, headers=alice).json()["id"]
    assert client.get("/api-keys", headers=bob).json() == []
    assert client.delete(f"/api-keys/{key_id}", headers=bob).status_code == 404
