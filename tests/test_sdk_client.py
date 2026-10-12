"""The Python SDK's transport: retries only when safe, idempotency keys on creates, typed errors, and sandboxes made
with `with` always cleaned up. Against a fake HTTP server (httpx.MockTransport), so no Zoo runs."""

import json

import httpx
import pytest
from zoo_sdk import ConflictError, NotFoundError, RateLimitError, ServerError, Zoo
from zoo_sdk import _transport as transport

SANDBOX = {
    "id": "sb-1",
    "name": "one",
    "kind": "desktop",
    "status": "running",
    "server_id": None,
    "error_message": None,
    "created_at": "2026-10-12T00:00:00Z",
    "started_at": None,
}


def error(status: int, code: str, **headers: str) -> httpx.Response:
    body = {"error": {"code": code, "message": code.replace("_", " "), "hint": "do x", "docs_url": "u#" + code}}
    return httpx.Response(status, json=body, headers=headers)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(transport, "backoff", lambda attempt, response: 0)
    monkeypatch.setattr("time.sleep", lambda _: None)


def make(handler) -> tuple[Zoo, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    http = httpx.Client(transport=httpx.MockTransport(record))
    return Zoo(api_key="zoo_test", base_url="http://zoo.test", http_client=http), seen


def test_reads_are_retried_on_server_errors():
    zoo, seen = make(lambda req, n: error(503, "unavailable") if n < 3 else httpx.Response(200, json=SANDBOX))
    assert zoo.get("sb-1").id == "sb-1"
    assert len(seen) == 3 and all(r.url.path == "/v1/sandboxes/sb-1" for r in seen)
    assert seen[0].headers["authorization"] == "Bearer zoo_test"


def test_retries_give_up_with_a_typed_error():
    zoo, seen = make(lambda req, n: error(503, "unavailable"))
    with pytest.raises(ServerError) as raised:
        zoo.get("sb-1")
    assert len(seen) == 1 + zoo.config.max_retries
    assert raised.value.code == "unavailable" and raised.value.hint == "do x" and raised.value.status == 503


def test_creates_carry_one_idempotency_key_across_retries():
    def handler(req, n):
        if req.method == "POST":
            return error(502, "upstream_error") if n == 1 else httpx.Response(201, json=SANDBOX)
        return httpx.Response(200, json=SANDBOX)

    zoo, seen = make(handler)
    zoo.create(kind="desktop")
    posts = [r for r in seen if r.method == "POST"]
    assert len(posts) == 2
    assert posts[0].headers["idempotency-key"] == posts[1].headers["idempotency-key"]


def test_other_posts_are_not_retried_on_server_errors():
    zoo, seen = make(lambda req, n: error(500, "internal_error"))
    with pytest.raises(ServerError):
        zoo.request("POST", "/sandboxes/sb-1/tools/click", json={"x": 1, "y": 1})
    assert len(seen) == 1 and "idempotency-key" not in seen[0].headers


def test_rate_limits_are_retried_even_for_posts():
    zoo, seen = make(
        lambda req, n: error(429, "rate_limited", **{"Retry-After": "1"}) if n == 1 else httpx.Response(200, json={})
    )
    zoo.request("POST", "/sandboxes/sb-1/tools/click", json={"x": 1, "y": 1})
    assert len(seen) == 2


def test_errors_map_to_classes():
    for status, code, cls in [(404, "sandbox_not_found", NotFoundError), (409, "quota_exceeded", ConflictError)]:
        zoo, _ = make(lambda req, n, status=status, code=code: error(status, code))
        with pytest.raises(cls) as raised:
            zoo.get("sb-1")
        assert raised.value.code == code
    zoo, _ = make(lambda req, n: error(429, "rate_limited"))
    with pytest.raises(RateLimitError):
        zoo.get("sb-1")


def test_with_deletes_the_sandbox_even_on_error():
    def handler(req, n):
        if req.method == "DELETE":
            return httpx.Response(200, json={"id": "sb-1"})
        return httpx.Response(201 if req.method == "POST" else 200, json=SANDBOX)

    zoo, seen = make(handler)
    with pytest.raises(ValueError), zoo.sandbox(kind="desktop"):
        raise ValueError("the agent crashed")
    assert [r.method for r in seen if r.method == "DELETE"] == ["DELETE"]

    # an existing sandbox isn't deleted
    seen.clear()
    with zoo.sandbox("sb-1"):
        pass
    assert all(r.method != "DELETE" for r in seen)


def test_a_sandbox_that_fails_to_start_is_deleted():
    failed = {**SANDBOX, "status": "failed", "error_message": "no room"}

    def handler(req, n):
        if req.method == "POST":
            return httpx.Response(201, json={**SANDBOX, "status": "pending"})
        if req.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=failed)

    zoo, seen = make(handler)
    with pytest.raises(Exception, match="no room"):
        zoo.create(kind="desktop")
    assert any(r.method == "DELETE" for r in seen)


def test_exec_streams_output():
    body = (
        'event: stdout\ndata: {"data": "hel"}\n\n'
        'event: stdout\ndata: {"data": "lo\\n"}\n\n'
        'event: stderr\ndata: {"data": "warn"}\n\n'
        'event: exit\ndata: {"exit_code": 3, "timed_out": false}\n\n'
    )

    def handler(req, n):
        if req.url.path.endswith("/exec/stream"):
            assert json.loads(req.content) == {"command": "make", "timeout": 30}
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=SANDBOX)

    zoo, _ = make(handler)
    chunks = list(zoo.get("sb-1").exec("make", stream=True))
    assert [(c.stream, c.data) for c in chunks[:-1]] == [("stdout", "hel"), ("stdout", "lo\n"), ("stderr", "warn")]
    assert chunks[-1].stream == "exit" and chunks[-1].exit_code == 3


def test_pages_are_followed():
    def handler(req, n):
        cursor = req.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(200, json={"data": [SANDBOX], "next_cursor": "c2"})
        return httpx.Response(200, json={"data": [{**SANDBOX, "id": "sb-2"}], "next_cursor": None})

    zoo, seen = make(handler)
    assert [b.id for b in zoo.sandboxes()] == ["sb-1", "sb-2"] and len(seen) == 2


def test_tool_arguments_named_timeout_reach_the_tool():
    zoo, seen = make(lambda req, n: httpx.Response(200, json=SANDBOX if req.method == "GET" else {"stable": True}))
    zoo.get("sb-1").tool("wait_until_stable", timeout=5)
    assert json.loads(seen[-1].content) == {"timeout": 5}
