"""Errors the SDK raises. Every API error carries the server's `code` (stable, documented), `message`, `hint` and
`docs_url`; the class says what kind of failure it was."""

from typing import Any


class ZooError(Exception):
    """An error from the Zoo API, or the SDK giving up on a request."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str = "error",
        hint: str | None = None,
        docs_url: str | None = None,
        request_id: str | None = None,
    ):
        super().__init__(f"{status}: {message}" if status else message)
        self.message = message
        self.status = status
        self.code = code
        self.hint = hint
        self.docs_url = docs_url
        self.request_id = request_id


class AuthenticationError(ZooError):
    """401: no or invalid API key."""


class PermissionDeniedError(ZooError):
    """403: the key's role or scope, or a sandbox policy, doesn't allow it."""


class NotFoundError(ZooError):
    """404: no such sandbox (or other object) in this workspace."""


class ConflictError(ZooError):
    """409: the object's state doesn't allow it, a quota is used up, or an idempotency key clashed."""


class ValidationError(ZooError):
    """422: a field didn't match the schema."""


class RateLimitError(ZooError):
    """429: too many requests; retried automatically before this is raised."""


class ServerError(ZooError):
    """5xx: Zoo, or a sandbox it talks to, failed."""


class APIConnectionError(ZooError):
    """The server couldn't be reached."""


class APITimeoutError(ZooError):
    """The server didn't answer within the timeout."""


BY_STATUS: dict[int, type[ZooError]] = {
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def from_response(status: int, body: Any, text: str = "") -> ZooError:
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        # a proxy in front of Zoo, or an older server
        detail = body.get("detail") if isinstance(body, dict) else None
        error = {"message": str(detail or text or f"request failed ({status})")}
    cls = BY_STATUS.get(status) or (ServerError if status >= 500 else ZooError)
    return cls(
        str(error.get("message") or f"request failed ({status})"),
        status=status,
        code=str(error.get("code") or "error"),
        hint=error.get("hint"),
        docs_url=error.get("docs_url"),
        request_id=error.get("request_id"),
    )
