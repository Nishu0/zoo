"""The synchronous client: `Zoo` for the workspace, `Sandbox` for one sandbox.

from zoo_sdk import Zoo

zoo = Zoo()  # ZOO_API_KEY, ZOO_URL
with zoo.sandbox(kind="desktop") as box:  # deleted on exit, also on error
    for chunk in box.exec("pip install requests", stream=True):
        print(chunk.data, end="")
"""

import base64
import json
import shlex
import time
from collections.abc import Iterator
from typing import Any, Literal, Self, overload

import httpx

from zoo_sdk import _transport as transport
from zoo_sdk.errors import APIConnectionError, APITimeoutError, NotFoundError, ZooError
from zoo_sdk.types import ExecChunk, ExecResult, SandboxData, SandboxKind

SETTLING = ("pending", "provisioning")


class Zoo:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        workspace: str | None = None,
        http_client: httpx.Client | None = None,
    ):
        """`api_key` defaults to ZOO_API_KEY and `base_url` to ZOO_URL (http://localhost:8000). Every call times out
        after `timeout` seconds (30) unless it passes its own, and is retried up to `max_retries` times (3) when that
        is safe: connection failures and 429 always, 5xx and timeouts for reads and for creates, which carry an
        Idempotency-Key."""
        self.config = transport.Config.load(api_key, base_url, timeout, max_retries, workspace)
        self.http = http_client or httpx.Client(headers=self.config.headers(), timeout=self.config.timeout)
        if http_client is not None:
            self.http.headers.update(self.config.headers())

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object):
        self.close()

    def close(self):
        self.http.close()

    # requests

    def request(
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
        """Sends a request to /v1`path`, retrying when safe, and raises a ZooError subclass for an error response.
        A `stream`ed response must be closed by the caller (or read to the end)."""
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
                response = self.http.send(request, stream=stream)
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
                        response.read()
                    transport.raise_for(response)
                response.close()
            time.sleep(transport.backoff(attempt, response))
            attempt += 1

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.request(method, path, **kwargs).json()

    def _sse(self, method: str, path: str, **kwargs: Any) -> Iterator[tuple[str, Any]]:
        response = self.request(method, path, stream=True, timeout=None, **kwargs)
        try:
            yield from transport.sse(response.iter_lines())
        finally:
            response.close()

    # sandboxes

    def sandboxes(self, labels: dict[str, str] | None = None) -> list["Sandbox"]:
        """The workspace's sandboxes, only those with all of `labels` when given."""
        return list(self.iter_sandboxes(labels))

    def iter_sandboxes(self, labels: dict[str, str] | None = None, page_size: int = 100) -> Iterator["Sandbox"]:
        """The workspace's sandboxes, newest first, a page at a time."""
        params: list[tuple[str, str | int]] = [("label", f"{k}={v}") for k, v in (labels or {}).items()]
        cursor = None
        while True:
            page = self._json(
                "GET", "/sandboxes", params=[*params, ("limit", page_size), *([("cursor", cursor)] if cursor else [])]
            )
            for data in page["data"]:
                yield Sandbox(self, data)
            cursor = page["next_cursor"]
            if not cursor:
                return

    def get(self, sandbox_id: str) -> "Sandbox":
        return Sandbox(self, self._json("GET", f"/sandboxes/{sandbox_id}"))

    @overload
    def sandbox(self, sandbox_id: str, /) -> "Sandbox": ...

    @overload
    def sandbox(
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
    ) -> "Sandbox": ...

    def sandbox(self, sandbox_id: str | None = None, /, **create: Any) -> "Sandbox":
        """`sandbox(id)` is an existing sandbox; `sandbox(kind=...)` creates one, which `with` deletes on exit:

        with zoo.sandbox(kind="desktop") as box:
            box.exec("echo hi")
        """
        return self.get(sandbox_id) if sandbox_id is not None else self.create(**create)

    def create(
        self,
        name: str | None = None,
        kind: SandboxKind = "desktop",
        server_id: str | None = None,
        profile_ids: list[str] | None = None,
        wait: bool = True,
        timeout: float = 180,
        labels: dict[str, str] | None = None,
    ) -> "Sandbox":
        """Creates a sandbox and, by default, waits until it runs. If it fails to start it is deleted, and the error
        raised."""
        body = {
            "name": name,
            "kind": kind,
            "server_id": server_id,
            "profile_ids": profile_ids or [],
            "labels": labels or {},
        }
        sandbox = Sandbox(self, self._json("POST", "/sandboxes", json=body), owned=True)
        if not wait:
            return sandbox
        try:
            return sandbox.wait(timeout)
        except BaseException:
            sandbox.delete(wait=False)
            raise

    # workspace

    def servers(self) -> list[dict]:
        return self._json("GET", "/servers")

    def add_server(self, name: str, docker_url: str, bind_address: str) -> dict:
        body = {"name": name, "docker_url": docker_url, "bind_address": bind_address}
        return self._json("POST", "/servers", json=body)

    def profiles(self) -> list[dict]:
        return self._json("GET", "/profiles")

    def tools(self) -> list[dict]:
        return self._json("GET", "/tools")

    def events(
        self, after: int | None = None, sandbox_id: str | None = None, type: str | None = None
    ) -> Iterator[dict]:
        """Follows the workspace's events (sandbox.state) as they happen, from after `after` (a seq) or from now.
        Pass the last event's seq back to resume after a disconnect."""
        params = {k: v for k, v in {"after": after, "sandbox_id": sandbox_id, "type": type}.items() if v is not None}
        for _, data in self._sse("GET", "/events/stream", params=params):
            yield data

    def usage(self, since: str, until: str, group_by: str = "sandbox", metric: str | None = None) -> list[dict]:
        """Metered usage between two ISO times, by sandbox, day or a label (group_by="label:end_user")."""
        params = {"from": since, "to": until, "group_by": group_by, "metric": metric}
        return self._json("GET", "/usage", params={k: v for k, v in params.items() if v})["rows"]


class Sandbox:
    """One sandbox. Tools without a method of their own are attributes: `box.click(x=10, y=20)` calls the click
    tool. Used with `with`, a sandbox this client created is deleted on exit."""

    def __init__(self, client: Zoo, data: SandboxData, owned: bool = False):
        self.client = client
        self.data = data
        self.owned = owned

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object):
        if self.owned:
            self.delete(wait=False)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **kwargs: self.tool(name, **kwargs)

    def __repr__(self):
        return f"Sandbox({self.id!r}, {self.name!r}, {self.status!r})"

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
    def server_id(self) -> str | None:
        return self.data["server_id"]

    @property
    def error_message(self) -> str | None:
        return self.data["error_message"]

    @property
    def labels(self) -> dict[str, str]:
        return self.data.get("labels") or {}

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.client._json(method, f"/sandboxes/{self.id}{path}", **kwargs)

    def refresh(self) -> "Sandbox":
        self.data = self._json("GET", "")
        return self

    def wait(self, timeout: float = 120) -> "Sandbox":
        """Waits until the sandbox runs; raises if it fails or takes longer than `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while self.refresh().status in SETTLING:
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} not ready after {timeout}s")
            time.sleep(1)
        if self.status != "running":
            raise ZooError(f"sandbox {self.id} is {self.status}: {self.error_message}")
        return self

    def tool(self, name: str, request_timeout: float | None = None, **args: Any) -> Any:
        """Calls a tool by name with its arguments; `request_timeout` (seconds) is for the HTTP call."""
        return self._json("POST", f"/tools/{name}", json=args, timeout=request_timeout)

    @overload
    def exec(self, command: str, timeout: int = 30, *, stream: Literal[False] = False) -> ExecResult: ...

    @overload
    def exec(self, command: str, timeout: int = 30, *, stream: Literal[True]) -> Iterator[ExecChunk]: ...

    def exec(self, command: str, timeout: int = 30, *, stream: bool = False) -> ExecResult | Iterator[ExecChunk]:
        """Runs a shell command (at most `timeout` seconds). Returns its exit code and output, or with stream=True
        yields its output as it arrives, ending with an `exit` chunk."""
        body = {"command": command, "timeout": timeout}
        if stream:
            return self._exec_stream(body)
        return self._json("POST", "/tools/execute_command", json=body, timeout=timeout + 30)

    def _exec_stream(self, body: dict) -> Iterator[ExecChunk]:
        for event, data in self.client._sse("POST", f"/sandboxes/{self.id}/exec/stream", json=body):
            if event == "exit":
                yield ExecChunk("exit", exit_code=data["exit_code"])
            elif event == "error":
                raise ZooError(data["message"])
            elif event in ("stdout", "stderr"):
                yield ExecChunk(event, data["data"])

    def claude(self, prompt: str, cwd: str = "~/work", timeout: int = 900, args: str = "") -> dict:
        command = (
            f"mkdir -p {cwd} && cd {cwd} && claude -p {shlex.quote(prompt)} "
            f"--output-format json --dangerously-skip-permissions {args}"
        )
        result = self.exec(command, timeout=timeout)
        if result["timed_out"]:
            raise ZooError(f"claude timed out after {timeout}s, see ~/.claude/debug in the sandbox")
        try:
            return json.loads(result["stdout"])
        except ValueError:
            raise ZooError(f"claude failed: {result['stderr'] or result['stdout']}")

    def screenshot(self) -> bytes:
        return base64.b64decode(self.tool("screenshot"))

    def viewer_link(self, mode: str = "view", expires_in: int = 60, end_user: str | None = None) -> dict:
        """A link to this sandbox's screen for someone without a Zoo account: "view" only watches, "control" can
        type and click. Its "url" opens in a browser or an iframe; expires_in is in
        minutes."""
        body = {"mode": mode, "expires_in_minutes": expires_in, "end_user": end_user}
        return self._json("POST", "/viewer-links", json=body)

    def set_labels(self, labels: dict[str, str]) -> "Sandbox":
        """Replaces the sandbox's labels."""
        self.data = self._json("PATCH", "", json={"labels": labels})
        return self

    def executions(self, limit: int = 50) -> list[dict]:
        """The latest tool calls on this sandbox, newest first."""
        return self._json("GET", "/executions", params={"limit": limit})["data"]

    def hotkey(self, *keys: str) -> dict:
        return self.tool("hotkey", keys=list(keys))

    def start(self, wait: bool = True) -> "Sandbox":
        self.data = self._json("POST", "/start")
        return self.wait() if wait else self

    def stop(self, wait: bool = True, timeout: float = 120) -> "Sandbox":
        """Stops the sandbox. Stopping runs as a job on the server, so by default this waits until it has stopped."""
        self.data = self._json("POST", "/stop")
        deadline = time.monotonic() + timeout
        while wait and (self.data.get("job") or self.status == "running"):
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} didn't stop within {timeout}s")
            time.sleep(1)
            self.refresh()
        return self

    def delete(self, wait: bool = True, timeout: float = 120):
        """Deletes the sandbox. Deleting runs as a job on the server, so by default this waits until it is gone."""
        try:
            self.client.request("DELETE", f"/sandboxes/{self.id}")
        except NotFoundError:
            return
        deadline = time.monotonic() + timeout
        while wait:
            try:
                self.refresh()
            except NotFoundError:
                return
            if time.monotonic() > deadline:
                raise ZooError(f"sandbox {self.id} wasn't deleted within {timeout}s")
            time.sleep(1)

    def set_secret(self, name: str, value: str) -> dict:
        return self._json("PUT", "/secrets", json={"name": name, "value": value})

    def set_permission(self, permission: str, action: str, effect: str) -> dict:
        body = {"permission": permission, "action": action, "effect": effect}
        return self._json("PUT", "/permissions", json=body)

    def set_network(self, default_action: str = "allow", allow_dns: bool = True) -> dict:
        body = {"default_action": default_action, "allow_dns": allow_dns}
        return self._json("PUT", "/network", json=body)

    def add_rule(self, rule_type: str, value: str, effect: str = "allow") -> dict:
        body = {"rule_type": rule_type, "value": value, "effect": effect}
        return self._json("POST", "/network/rules", json=body)

    def read_file(self, path: str, timeout: float | None = 300) -> bytes:
        """A file from the sandbox (absolute, or relative to the home folder)."""
        return self.client.request(
            "GET", f"/sandboxes/{self.id}/files/content", params={"path": path}, timeout=timeout
        ).content

    def write_file(self, path: str, data: bytes, timeout: float | None = 300) -> dict:
        """Writes a file into the sandbox, making its folder."""
        return self._json("PUT", "/files/content", params={"path": path}, content=data, timeout=timeout)

    def backup(self, path: str):
        """Exports the home folder as a tar file; snapshots (`snapshot()`) are the faster way to keep a copy."""
        response = self.client.request("GET", f"/sandboxes/{self.id}/backup", stream=True, timeout=None)
        try:
            with open(path, "wb") as f:
                f.writelines(response.iter_bytes(1 << 16))
        finally:
            response.close()

    def restore(self, path: str):
        with open(path, "rb") as f:
            self.client.request("POST", f"/sandboxes/{self.id}/restore", content=f.read(), timeout=None)

    def snapshots(self) -> list[dict]:
        return self._json("GET", "/snapshots")

    def snapshot(self, name: str = "", wait: bool = True, timeout: float = 1800) -> dict:
        """Snapshots the home disk on the sandbox's server (a VM must be stopped). Waits until it is ready."""
        snap = self._json("POST", "/snapshots", json={"name": name})
        deadline = time.monotonic() + timeout
        while wait and snap["state"] == "creating":
            if time.monotonic() > deadline:
                raise ZooError(f"snapshot {snap['id']} wasn't ready within {timeout}s")
            time.sleep(1)
            snap = next((s for s in self.snapshots() if s["id"] == snap["id"]), snap)
        if snap["state"] == "failed":
            raise ZooError(f"snapshot failed: {snap['error']}")
        return snap

    def restore_snapshot(self, snapshot_id: str, wait: bool = True, timeout: float = 1800) -> "Sandbox":
        """Replaces the home disk with a snapshot's. The sandbox must be stopped."""
        self.data = self._json("POST", f"/snapshots/{snapshot_id}/restore")
        deadline = time.monotonic() + timeout
        while wait and self.data.get("job"):
            if time.monotonic() > deadline:
                raise ZooError(f"restoring {snapshot_id} didn't finish within {timeout}s")
            time.sleep(1)
            self.refresh()
        if self.status == "failed":
            raise ZooError(self.error_message or "restore failed")
        return self

    def delete_snapshot(self, snapshot_id: str):
        self.client.request("DELETE", f"/sandboxes/{self.id}/snapshots/{snapshot_id}")

    def move(self, server_id: str | None) -> "Sandbox":
        self.data = self._json("POST", "/move", json={"server_id": server_id})
        return self

    def save_profile(self, name: str, app: str = "firefox", profile_id: str | None = None) -> dict:
        """Saves the app's profile from this sandbox. Saving under an existing name (or a profile_id) adds a version."""
        body = {"name": name, "app": app, "profile_id": profile_id}
        return self._json("POST", "/profiles", json=body)

    def apply_profile(self, profile_id: str, version: int | None = None) -> dict:
        """Loads a profile, the latest version unless one is given. The app must not be running (409)."""
        params = {"version": version} if version else None
        return self._json("POST", f"/profiles/{profile_id}", params=params)
