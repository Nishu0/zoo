"""API conventions every endpoint follows: one error format, /v1-only routes, idempotency keys on POSTs, and
cursor pagination."""

from fastapi.testclient import TestClient

from server import errors


def test_errors_have_one_shape(client, alice):
    res = client.get("/v1/sandboxes/nope", headers=alice)
    assert res.status_code == 404
    error = res.json()["error"]
    assert error["code"] == "sandbox_not_found" and error["message"] == "sandbox not found"
    assert error["hint"] == errors.HINTS["sandbox_not_found"]
    assert error["docs_url"].endswith("#sandbox_not_found")
    assert error["request_id"] == res.headers["x-request-id"]

    res = client.post("/v1/sandboxes", json={"kind": "spaceship"}, headers=alice)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_failed" and "kind" in res.json()["error"]["message"]

    res = client.get("/v1/sandboxes")
    assert res.status_code == 401 and res.json()["error"]["code"] == "unauthenticated"

    assert client.get("/v1/no-such-route", headers=alice).json()["error"]["code"] == "not_found"


def test_a_500_hides_the_exception(zoo, alice, sandbox, monkeypatch):
    from server import sandbox_api

    def boom(*_, **__):
        raise RuntimeError("secret detail from deep inside")

    monkeypatch.setattr(sandbox_api, "to_response", boom)
    client = TestClient(zoo.app, raise_server_exceptions=False)
    res = client.get("/v1/sandboxes", headers=alice)
    assert res.status_code == 500
    error = res.json()["error"]
    assert error["code"] == "internal_error" and "secret detail" not in res.text
    assert error["request_id"] in error["message"]


def test_request_ids_are_echoed(client):
    res = client.get("/v1/sandboxes", headers={"X-Request-Id": "abc123"})
    assert res.headers["x-request-id"] == "abc123" and res.json()["error"]["request_id"] == "abc123"


def test_routes_are_v1_only(client, alice):
    assert client.get("/sandboxes", headers=alice).status_code == 404
    # not part of the versioned API
    assert client.get("/healthz").status_code == 200

    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/sandboxes" in paths and "/sandboxes" not in paths
    assert all(p.startswith("/v1/") for p in paths if not p.startswith("/embed"))


def test_idempotency_keys_make_creates_safe_to_retry(client, alice):
    headers = {**alice, "Idempotency-Key": "create-1"}
    first = client.post("/v1/sandboxes", json={"kind": "desktop", "name": "once"}, headers=headers)
    again = client.post("/v1/sandboxes", json={"kind": "desktop", "name": "once"}, headers=headers)
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == first.json()["id"] and again.headers["idempotent-replayed"] == "true"
    assert [s["name"] for s in client.get("/v1/sandboxes", headers=alice).json()["data"]] == ["once"]

    other = client.post("/v1/sandboxes", json={"kind": "desktop", "name": "twice"}, headers=headers)
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_key_reused"

    # keys belong to their caller
    bob = client.post("/auth/signup", json={"email": "bob@example.com", "password": "correct-horse", "name": "bob"})
    bob_headers = {"Authorization": f"Bearer {bob.json()['access_token']}", "Idempotency-Key": "create-1"}
    assert (
        client.post("/v1/sandboxes", json={"kind": "desktop", "name": "once"}, headers=bob_headers).json()["id"]
        != first.json()["id"]
    )


def test_errors_are_kept_but_server_errors_are_not(client, alice):
    headers = {**alice, "Idempotency-Key": "bad-1"}
    first = client.post("/v1/sandboxes", json={"kind": "spaceship"}, headers=headers)
    again = client.post("/v1/sandboxes", json={"kind": "spaceship"}, headers=headers)
    assert first.status_code == again.status_code == 422 and again.headers["idempotent-replayed"] == "true"


def test_pages_walk_every_row_once(client, alice, make_sandbox):
    made = {make_sandbox(name=f"box-{i}")["id"] for i in range(5)}
    seen, cursor, pages = [], None, 0
    while True:
        query = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = client.get("/v1/sandboxes", params=query, headers=alice).json()
        seen += [s["id"] for s in page["data"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == len(set(seen)) == 5 and set(seen) == made
    # newest first
    created = [s["created_at"] for s in client.get("/v1/sandboxes", headers=alice).json()["data"]]
    assert created == sorted(created, reverse=True)

    res = client.get("/v1/sandboxes", params={"cursor": "not-a-cursor"}, headers=alice)
    assert res.status_code == 422 and res.json()["error"]["code"] == "validation_failed"
    assert client.get("/v1/sandboxes", params={"limit": 500}, headers=alice).status_code == 422


def test_pages_respect_key_scopes(client, alice, make_sandbox):
    mine = make_sandbox(name="mine", labels={"end_user": "a"})
    make_sandbox(name="theirs", labels={"end_user": "b"})
    key = client.post("/v1/api-keys", json={"name": "a", "label_selector": {"end_user": "a"}}, headers=alice).json()[
        "key"
    ]
    page = client.get("/v1/sandboxes", params={"limit": 1}, headers={"Authorization": f"Bearer {key}"}).json()
    assert [s["id"] for s in page["data"]] == [mine["id"]] and page["next_cursor"] is None
