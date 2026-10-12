"""What the sync and async clients share: configuration, which requests may be retried, backoff, and turning
responses into errors."""

import json
import os
import random
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from zoo_sdk.errors import from_response

API_PREFIX = "/v1"
DEFAULT_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
VERSION = "0.2.0"

# POSTs that create something; the SDK gives each an Idempotency-Key, so retrying one never creates two
CREATES = re.compile(r"^/(sandboxes|sandboxes/[^/]+/snapshots|sandboxes/[^/]+/viewer-links|webhooks|api-keys)$")
IDEMPOTENT = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}


@dataclass
class Config:
    base_url: str
    api_key: str
    timeout: float
    max_retries: int
    workspace: str | None

    @classmethod
    def load(
        cls,
        api_key: str | None,
        base_url: str | None,
        timeout: float | None,
        max_retries: int | None,
        workspace: str | None,
    ) -> "Config":
        key = api_key or os.environ.get("ZOO_API_KEY")
        if not key:
            raise ValueError("pass api_key= or set ZOO_API_KEY (create a key in Settings → API keys)")
        url = (base_url or os.environ.get("ZOO_URL") or DEFAULT_URL).rstrip("/")
        return cls(
            base_url=url.removesuffix(API_PREFIX),
            api_key=key,
            timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
            max_retries=DEFAULT_RETRIES if max_retries is None else max_retries,
            workspace=workspace or os.environ.get("ZOO_WORKSPACE"),
        )

    def headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": f"zoo-sdk-python/{VERSION}"}
        if self.workspace:
            headers["X-Zoo-Workspace"] = self.workspace
        return headers

    def url(self, path: str) -> str:
        return f"{self.base_url}{API_PREFIX}{path}"


def prepare(method: str, path: str, headers: dict[str, str] | None) -> dict[str, str]:
    headers = dict(headers or {})
    if method == "POST" and CREATES.match(path) and "Idempotency-Key" not in headers:
        headers["Idempotency-Key"] = uuid.uuid4().hex
    return headers


def retryable(method: str, headers: dict[str, str], status: int | None, exc: Exception | None) -> bool:
    """Whether a failed attempt may run again: always when it never reached the server or was rate limited;
    for server errors and timeouts only when repeating it can't do something twice."""
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return True
    if status == 429:
        return True
    safe = method in IDEMPOTENT or "Idempotency-Key" in headers
    if exc is not None:
        return safe and isinstance(exc, httpx.TimeoutException | httpx.RemoteProtocolError)
    return safe and status in (500, 502, 503, 504)


def backoff(attempt: int, response: httpx.Response | None) -> float:
    if response is not None:
        after = response.headers.get("Retry-After")
        if after and after.isdigit():
            return min(float(after), 60.0)
    return min(0.5 * 2**attempt, 8.0) * (0.5 + random.random() / 2)


def raise_for(response: httpx.Response):
    if response.is_success:
        return
    try:
        body = response.json()
    except (ValueError, httpx.ResponseNotRead):
        body = None
    raise from_response(response.status_code, body, "" if body is not None else safe_text(response))


def safe_text(response: httpx.Response) -> str:
    try:
        return response.text[:500]
    except httpx.ResponseNotRead:
        return ""


def sse(lines: Iterable[str]) -> Iterable[tuple[str, Any]]:
    """(event, data) of each server-sent event in `lines`."""
    event, data = "message", []
    for line in lines:
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, json.loads("\n".join(data))
