"""The asyncio client, `AsyncZoo` and `AsyncSandbox`, with the same methods as the sync ones, awaited:

from zoo_sdk import AsyncZoo

async with AsyncZoo() as zoo:
    async with await zoo.sandbox(kind="desktop") as box:  # deleted on exit, also on error
        async for chunk in box.exec("make test", stream=True):
            print(chunk.data, end="")
"""

import asyncio
import base64
import time
from collections.abc import AsyncIterator
from typing import Any, Literal, Self, overload

import httpx

from zoo_sdk import _transport as transport
from zoo_sdk.errors import APIConnectionError, APITimeoutError, NotFoundError, ZooError
from zoo_sdk.types import ExecChunk, ExecResult, SandboxData, SandboxKind

SETTLING = ("pending", "provisioning")


class AsyncZoo:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        workspace: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        """See `Zoo`: the same configuration, timeouts and retries."""
        self.config = transport.Config.load(api_key, base_url, timeout, max_retries, workspace)
        self.http = http_client or httpx.AsyncClient(headers=self.config.headers(), timeout=self.config.timeout)
        if http_client is not None:
            self.http.headers.update(self.config.headers())

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object):
        await self.close()

    async def close(self):
        await self.http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        headers = transport.prepare(method, path, headers)
        request = self.http.build_request(
            method,
            self.config.url(path),
            json=json,
            params=params,
            content=content,
            headers=headers,
            timeout=self.config.timeout if timeout is None else timeout,
        )
        attempt = 0
        while True:
            response: httpx.Response | None = None
            try:
                response = await self.http.send(request, stream=stream)
            except httpx.TimeoutException as e:
                if attempt >= self.config.max_retries or not transport.retryable(method, headers, None, e):
                    raise APITimeoutError(f"{method} {path} timed out", code="timeout") from e
            except httpx.TransportError as e:
                if attempt >= self.config.max_retries or not transport.retryable(method, headers, None, e):
                    raise APIConnectionError(
                        f"couldn't reach {self.config.base_url}: {e}", code="connection_error"
                    ) from e
            else:
                if response.is_success:
                    return response
                if attempt >= self.config.max_retries or not transport.retryable(
                    method, headers, response.status_code, None
                ):
                    if stream:
                        await response.aread()
                    transport.raise_for(response)
                await response.aclose()
            await asyncio.sleep(transport.backoff(attempt, response))
            attempt += 1

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return (await self.request(method, path, **kwargs)).json()

    async def _sse(self, method: str, path: str, **kwargs: Any) -> AsyncIterator[tuple[str, Any]]:
        response = await self.request(method, path, stream=True, timeout=None, **kwargs)
        try:
            lines = []
            async for line in response.aiter_lines():
                lines.append(line)
                if not line:
                    for item in transport.sse(lines):
                        yield item
                    lines = []
            for item in transport.sse(lines):
                yield item
        finally:
            await response.aclose()

    async def sandboxes(self, labels: dict[str, str] | None = None) -> list["AsyncSandbox"]:
        return [box async for box in self.iter_sandboxes(labels)]

    async def iter_sandboxes(
        self, labels: dict[str, str] | None = None, page_size: int = 100
    ) -> AsyncIterator["AsyncSandbox"]:
        params: list[tuple[str, str | int]] = [("label", f"{k}={v}") for k, v in (labels or {}).items()]
        cursor = None
        while True:
            page = await self._json(
                "GET", "/sandboxes", params=[*params, ("limit", page_size), *([("cursor", cursor)] if cursor else [])]
            )
            for data in page["data"]:
                yield AsyncSandbox(self, data)
            cursor = page["next_cursor"]
            if not cursor:
                return

    async def get(self, sandbox_id: str) -> "AsyncSandbox":
        return AsyncSandbox(self, await self._json("GET", f"/sandboxes/{sandbox_id}"))

    @overload
    async def sandbox(self, sandbox_id: str, /) -> "AsyncSandbox": ...

    @overload
    async def sandbox(
        self,
        sandbox_id: None = None,
        /,
        *,
        kind: SandboxKind = "desktop",
        name: str | None = None,
        labels: dict[str, str] | None = None,
        server_id: str | None = None,
        profile_ids: list[str] | None = None,
        wait: bool = True,
        timeout: float = 180,
    ) -> "AsyncSandbox": ...

    async def sandbox(self, sandbox_id: str | None = None, /, **create: Any) -> "AsyncSandbox":
        return await self.get(sandbox_id) if sandbox_id is not None else await self.create(**create)

    async def create(
        self,
        name: str | None = None,
        kind: SandboxKind = "desktop",
        server_id: str | None = None,
        profile_ids: list[str] | None = None,
        wait: bool = True,
        timeout: float = 180,
        labels: dict[str, str] | None = None,
    ) -> "AsyncSandbox":
        body = {
            "name": name,
            "kind": kind,
            "server_id": server_id,
            "profile_ids": profile_ids or [],
            "labels": labels or {},
        }
        sandbox = AsyncSandbox(self, await self._json("POST", "/sandboxes", json=body), owned=True)
        if not wait:
            return sandbox
        try:
            return await sandbox.wait(timeout)
        except BaseException:
            await asyncio.shield(sandbox.delete(wait=False))
            raise

    async def events(
        self, after: int | None = None, sandbox_id: str | None = None, type: str | None = None
    ) -> AsyncIterator[dict]:
        params = {k: v for k, v in {"after": after, "sandbox_id": sandbox_id, "type": type}.items() if v is not None}
        async for _, data in self._sse("GET", "/events/stream", params=params):
            yield data

    async def tools(self) -> list[dict]:
        return await self._json("GET", "/tools")


class AsyncSandbox:
    def __init__(self, client: AsyncZoo, data: SandboxData, owned: bool = False):
        self.client = client
        self.data = data
        self.owned = owned

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object):
        if self.owned:
            await asyncio.shield(self.delete(wait=False))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(**kwargs: Any) -> Any:
            return await self.tool(name, **kwargs)

        return call

    def __repr__(self):
        return f"AsyncSandbox({self.id!r}, {self.name!r}, {self.status!r})"

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def kind(self) -> str:
        return self.data["kind"]

    @property
    def status(self) -> str:
        return self.data["status"]

    @property
    def error_message(self) -> str | None:
        return self.data["error_message"]

    @property
    def labels(self) -> dict[str, str]:
        return self.data.get("labels") or {}

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.client._json(method, f"/sandboxes/{self.id}{path}", **kwargs)

    async def refresh(self) -> "AsyncSandbox":
        self.data = await self._json("GET", "")
        return self

    async def wait(self, timeout: float = 120) -> "AsyncSandbox":
        deadline = time.monotonic() + timeout
        while (await self.refresh()).status in SETTLING:
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} not ready after {timeout}s")
            await asyncio.sleep(1)
        if self.status != "running":
            raise ZooError(f"sandbox {self.id} is {self.status}: {self.error_message}")
        return self

    async def tool(self, name: str, request_timeout: float | None = None, **args: Any) -> Any:
        """Calls a tool by name with its arguments; `request_timeout` (seconds) is for the HTTP call."""
        return await self._json("POST", f"/tools/{name}", json=args, timeout=request_timeout)

    @overload
    def exec(
        self, command: str, timeout: int = 30, *, stream: Literal[False] = False
    ) -> "asyncio.Future[ExecResult]": ...

    @overload
    def exec(self, command: str, timeout: int = 30, *, stream: Literal[True]) -> AsyncIterator[ExecChunk]: ...

    def exec(
        self, command: str, timeout: int = 30, *, stream: bool = False
    ) -> "asyncio.Future[ExecResult] | AsyncIterator[ExecChunk]":
        """`await box.exec(cmd)` for the result, or `async for chunk in box.exec(cmd, stream=True)`."""
        body = {"command": command, "timeout": timeout}
        if stream:
            return self._exec_stream(body)
        return asyncio.ensure_future(self._json("POST", "/tools/execute_command", json=body, timeout=timeout + 30))

    async def _exec_stream(self, body: dict) -> AsyncIterator[ExecChunk]:
        async for event, data in self.client._sse("POST", f"/sandboxes/{self.id}/exec/stream", json=body):
            if event == "exit":
                yield ExecChunk("exit", exit_code=data["exit_code"])
            elif event == "error":
                raise ZooError(data["message"])
            elif event in ("stdout", "stderr"):
                yield ExecChunk(event, data["data"])

    async def screenshot(self) -> bytes:
        return base64.b64decode(await self.tool("screenshot"))

    async def ask(self, message: str, model: str | None = None, provider: str | None = None) -> AsyncIterator[dict]:
        body = {"message": message, "model": model, "provider": provider, "stream": True}
        async for _, data in self.client._sse("POST", f"/sandboxes/{self.id}/agent", json=body):
            yield data

    async def stop_agent(self):
        await self.client.request("POST", f"/sandboxes/{self.id}/agent/stop")

    async def takeover(self) -> dict:
        return await self._json("POST", "/agent/takeover")

    async def release(self) -> dict:
        return await self._json("POST", "/agent/release")

    async def viewer_link(self, mode: str = "view", expires_in: int = 60, end_user: str | None = None) -> dict:
        body = {"mode": mode, "expires_in_minutes": expires_in, "end_user": end_user}
        return await self._json("POST", "/viewer-links", json=body)

    async def set_labels(self, labels: dict[str, str]) -> "AsyncSandbox":
        self.data = await self._json("PATCH", "", json={"labels": labels})
        return self

    async def read_file(self, path: str, timeout: float | None = 300) -> bytes:
        response = await self.client.request(
            "GET", f"/sandboxes/{self.id}/files/content", params={"path": path}, timeout=timeout
        )
        return response.content

    async def write_file(self, path: str, data: bytes, timeout: float | None = 300) -> dict:
        return await self._json("PUT", "/files/content", params={"path": path}, content=data, timeout=timeout)

    async def start(self, wait: bool = True) -> "AsyncSandbox":
        self.data = await self._json("POST", "/start")
        return await self.wait() if wait else self

    async def stop(self, wait: bool = True, timeout: float = 120) -> "AsyncSandbox":
        self.data = await self._json("POST", "/stop")
        deadline = time.monotonic() + timeout
        while wait and (self.data.get("job") or self.status == "running"):
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} didn't stop within {timeout}s")
            await asyncio.sleep(1)
            await self.refresh()
        return self

    async def delete(self, wait: bool = True, timeout: float = 120):
        try:
            await self.client.request("DELETE", f"/sandboxes/{self.id}")
        except NotFoundError:
            return
        deadline = time.monotonic() + timeout
        while wait:
            try:
                await self.refresh()
            except NotFoundError:
                return
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} wasn't deleted within {timeout}s")
            await asyncio.sleep(1)
