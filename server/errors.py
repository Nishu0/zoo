"""One error format for the whole API:

    {"error": {"code": "sandbox_not_found", "message": "sandbox not found", "hint": "...", "docs_url": "...",
               "request_id": "..."}}

`code` is stable, snake_case and documented (CODES, rendered into the errors reference page); `message` is for
people and may change. Route code raises ZooError for a specific code, or HTTPException as before, whose code comes from
its message ("<thing> not found" -> <thing>_not_found, "...quota..." -> quota_exceeded) or else its status. A 500
never carries exception text: the traceback is logged under the request id, which the response and the
X-Request-Id header carry."""

import os
import re
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from logger.logger import logger

DOCS_URL = os.environ.get(
    "ZOO_DOCS_URL", "https://github.com/chann44/zoo/blob/main/site/src/content/docs/docs/reference/errors.md"
)

# code -> (status, what it means and the fix); the errors reference page is generated from this
CODES: dict[str, tuple[int, str]] = {
    "bad_request": (400, "The request was malformed. Check the body against the endpoint's schema."),
    "unauthenticated": (401, "No or invalid credentials. Send `Authorization: Bearer <key>`."),
    "forbidden": (403, "Your role, API key scope or a sandbox policy doesn't allow this."),
    "not_found": (404, "No such route or object."),
    "sandbox_not_found": (404, "No sandbox with that id in this workspace. `GET /v1/sandboxes` lists them."),
    "method_not_allowed": (405, "The route exists but not with this method."),
    "create_failed": (503, "No warm sandbox could be handed over in time. Retry."),
    "conflict": (409, "The object's current state doesn't allow this, e.g. the sandbox isn't running."),
    "quota_exceeded": (409, "A workspace, member or label quota is used up."),
    "idempotency_key_reused": (409, "This Idempotency-Key was used with a different request body."),
    "idempotency_key_in_progress": (409, "A request with this Idempotency-Key is still running."),
    "gone": (410, "The object existed but has expired or been revoked."),
    "too_large": (413, "The request body is too large."),
    "validation_failed": (422, "A field didn't match the schema; the message names it."),
    "rate_limited": (429, "Too many requests. Wait as long as the Retry-After header says."),
    "internal_error": (500, "Zoo failed. Retry; if it keeps failing, report the request id."),
    "not_supported": (501, "This runtime or deployment doesn't support it."),
    "upstream_error": (502, "A sandbox, node or provider Zoo talks to failed."),
    "unavailable": (503, "Zoo or a dependency is starting or overloaded. Retry with backoff."),
    "guest_unavailable": (503, "The sandbox's guest agent isn't connected, and every tool goes through it."),
    "timeout": (504, "A sandbox, node or provider didn't answer in time."),
}

HINTS = {
    "unauthenticated": "Create an API key in Settings → API keys and send it as `Authorization: Bearer <key>`.",
    "sandbox_not_found": "`GET /v1/sandboxes` lists this workspace's sandboxes; ids are case-sensitive.",
    "quota_exceeded": "Stop or delete sandboxes, or ask a workspace admin to raise the quota.",
    "idempotency_key_reused": "Use a new Idempotency-Key for a different request.",
    "idempotency_key_in_progress": "Wait for the first request to finish, then retry.",
    "create_failed": "Retry the create; a cold create starts once no warm sandbox is left.",
    "rate_limited": "Retry after the number of seconds in the Retry-After header.",
    "internal_error": "Retry. If it keeps failing, report the request id to your Zoo operator.",
    "unavailable": "Retry with exponential backoff.",
    "guest_unavailable": "the sandbox's guest agent is not connected; restart the sandbox",
    "timeout": "Retry; long commands can pass a larger `timeout`.",
}

BY_STATUS = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "too_large",
    422: "validation_failed",
    429: "rate_limited",
    500: "internal_error",
    501: "not_supported",
    502: "upstream_error",
    503: "unavailable",
    504: "timeout",
}

NOT_FOUND = re.compile(r"^(\w+) not found$")


class ZooError(HTTPException):
    """An error with a specific code (one of CODES) and, optionally, a hint."""

    def __init__(self, code: str, message: str, hint: str | None = None, headers: dict[str, str] | None = None):
        super().__init__(status_code=CODES[code][0], detail=message, headers=headers)
        self.code = code
        self.hint = hint


def code_for(status: int, message: str) -> str:
    match = NOT_FOUND.match(message)
    if status == 404 and match and f"{match.group(1)}_not_found" in CODES:
        return f"{match.group(1)}_not_found"
    if "quota" in message.lower() and status in (403, 409, 429):
        return "quota_exceeded"
    return BY_STATUS.get(status, "bad_request" if status < 500 else "internal_error")


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or uuid.uuid4().hex


def body(code: str, message: str, hint: str | None, rid: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "hint": hint if hint is not None else HINTS.get(code),
            "docs_url": f"{DOCS_URL}#{code}",
            "request_id": rid,
        }
    }


def respond(request: Request, status: int, code: str, message: str, hint: str | None = None, headers=None):
    rid = request_id(request)
    return JSONResponse(
        body(code, message, hint, rid), status_code=status, headers={**(headers or {}), "X-Request-Id": rid}
    )


def register(app: FastAPI):
    @app.middleware("http")
    async def tag(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers.setdefault("X-Request-Id", request.state.request_id)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if isinstance(exc, ZooError):
            return respond(request, exc.status_code, exc.code, str(exc.detail), exc.hint, exc.headers)
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return respond(request, exc.status_code, code_for(exc.status_code, message), message, None, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        problems = []
        for error in exc.errors():
            where = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            problems.append(f"{where}: {error.get('msg', 'invalid')}" if where else str(error.get("msg", "invalid")))
        return respond(request, 422, "validation_failed", "; ".join(problems) or "invalid request")

    @app.exception_handler(Exception)
    async def crashed(request: Request, exc: Exception):
        rid = request_id(request)
        logger.exception("unhandled error", extra={"request_id": rid, "path": request.url.path})
        return respond(request, 500, "internal_error", f"internal error (request id {rid})")
