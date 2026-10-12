"""Idempotency keys: a POST with an `Idempotency-Key` header runs once; repeats within 24 h get the first response
again (with `Idempotent-Replayed: true`), so a client can retry a create after a timeout without making two.

- the same key with a different body (or path) → 409 idempotency_key_reused
- the same key while the first request still runs → 409 idempotency_key_in_progress (one abandoned for 10 minutes
  is forgotten and runs again)
- 5xx responses, streams and binary responses aren't kept, so a retry runs the request again

Keys belong to the caller: they are kept per hash of the request's credentials and workspace header."""

import asyncio
import hashlib
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from db.connection import db_manager
from db.generated.query import ClaimIdempotencyKeyParams, FinishIdempotencyKeyParams
from server import errors
from server.jobs import stamp

HEADER = b"idempotency-key"
MAX_KEY = 255
# responses larger than this aren't kept
MAX_RESPONSE = 1 << 20
ABANDONED_SECONDS = 600
RETENTION_SECONDS = 24 * 3600


def claim(owner: str, key: str, method: str, path: str, request_hash: str):
    with db_manager.session() as db:
        params = ClaimIdempotencyKeyParams(owner=owner, key=key, method=method, path=path, request_hash=request_hash)
        row = db.claim_idempotency_key(params)
        assert row is not None
        if not row.inserted and row.status is None and time.time() - row.created_at.timestamp() > ABANDONED_SECONDS:
            db.release_idempotency_key(owner=owner, key=key)
            row = db.claim_idempotency_key(params)
            assert row is not None
        return row


def finish(owner: str, key: str, status: int, content_type: str | None, body: bytes):
    # only text responses are kept (JSON, in practice)
    with db_manager.session() as db:
        db.finish_idempotency_key(
            FinishIdempotencyKeyParams(
                status=status, content_type=content_type, response=body.decode(), owner=owner, key=key
            )
        )


def release(owner: str, key: str):
    with db_manager.session() as db:
        db.release_idempotency_key(owner=owner, key=key)


def purge():
    with db_manager.session() as db:
        db.purge_idempotency_keys(created_at=stamp(-RETENTION_SECONDS))


class Idempotency:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        key = headers.get(HEADER, b"").decode(errors="replace").strip()
        if not key:
            return await self.app(scope, receive, send)

        async def refuse(code: str, message: str):
            rid = headers.get(b"x-request-id", b"").decode() or hashlib.sha256(key.encode()).hexdigest()[:16]
            status = errors.CODES[code][0]
            await JSONResponse(errors.body(code, message, None, rid), status_code=status)(scope, receive, send)

        if len(key) > MAX_KEY:
            return await refuse("validation_failed", f"Idempotency-Key is at most {MAX_KEY} characters")

        chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        path = scope["path"]
        owner = hashlib.sha256(
            b"|".join(
                [headers.get(b"authorization", b""), headers.get(b"x-zoo-workspace", b""), headers.get(b"cookie", b"")]
            )
        ).hexdigest()
        request_hash = hashlib.sha256(b"POST " + path.encode() + b"\n" + body).hexdigest()

        row = await asyncio.to_thread(claim, owner, key, "POST", path, request_hash)
        if not row.inserted:
            if row.request_hash != request_hash:
                return await refuse("idempotency_key_reused", "this Idempotency-Key was used for a different request")
            if row.status is None:
                return await refuse("idempotency_key_in_progress", "a request with this Idempotency-Key is running")
            replay = [(b"idempotent-replayed", b"true")]
            if row.content_type:
                replay.append((b"content-type", row.content_type.encode()))
            await send({"type": "http.response.start", "status": row.status, "headers": replay})
            await send({"type": "http.response.body", "body": (row.response or "").encode()})
            return

        sent = False

        async def replayed() -> Message:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        status = 500
        content_type: str | None = None
        kept: list[bytes] = []
        size = 0

        async def capture(message: Message):
            nonlocal status, content_type, size
            if message["type"] == "http.response.start":
                status = message["status"]
                content_type = dict(message.get("headers", [])).get(b"content-type", b"").decode() or None
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                size += len(chunk)
                if size <= MAX_RESPONSE:
                    kept.append(chunk)
            await send(message)

        keep = False
        try:
            await self.app(scope, replayed, capture)
            keep = status < 500 and size <= MAX_RESPONSE and not (content_type or "").startswith("text/event-stream")
            if keep:
                try:
                    b"".join(kept).decode()
                except UnicodeDecodeError:
                    keep = False
        finally:
            if keep:
                await asyncio.to_thread(finish, owner, key, status, content_type, b"".join(kept))
            else:
                await asyncio.to_thread(release, owner, key)


def register(app: FastAPI):
    app.add_middleware(Idempotency)
