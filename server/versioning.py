"""API versions. The API is served under /v1; the same routes without the prefix still work, deprecated, and answer
with `Deprecation: true` and a `Link` to their /v1 successor.

Policy (docs: reference/versioning): a version stays supported for 12 months after its successor ships. Changes
within a version only add (new routes, new optional fields, new error codes); anything else is a new version.
Removals are announced in the changelog and with Deprecation/Sunset headers first.

Routes that aren't part of the public API (health checks, the embed page, MCP, chat integrations, node and guest
callbacks) aren't versioned and aren't deprecated."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PREFIX = "/v1"
# first path segments that aren't the public API
UNVERSIONED = {
    "",
    "healthz",
    "readyz",
    "metrics",
    "internal",
    "embed",
    "mcp",
    "slack",
    "whatsapp",
    "discord",
    "integrations",
    "docs",
    "redoc",
    "openapi.json",
}


def versioned(path: str) -> bool:
    return path.strip("/").split("/", 1)[0] not in UNVERSIONED


class Versioning:
    """Routes /v1/... to the app's routes, and marks unprefixed API calls deprecated."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path: str = scope["path"]
        if path == PREFIX or path.startswith(PREFIX + "/"):
            inner = path[len(PREFIX) :] or "/"
            scope = {**scope, "path": inner, "raw_path": inner.encode()}
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket" or not versioned(path):
            return await self.app(scope, receive, send)

        async def deprecated(message: Message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"deprecation", b"true"))
                headers.append((b"link", f'<{PREFIX}{path}>; rel="successor-version"'.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, deprecated)


def register(app: FastAPI):
    app.add_middleware(Versioning)

    def openapi() -> dict:
        if app.openapi_schema is None:
            schema = get_openapi(title="Zoo API", version="1", routes=app.routes)
            schema["servers"] = [{"url": "/"}]
            schema["paths"] = {
                (PREFIX + path if versioned(path) else path): item for path, item in schema.get("paths", {}).items()
            }
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
