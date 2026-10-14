"""Gateway-backed guest calls, terminals and tunnels. Python never accepts guest connections."""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from db.connection import db_manager
from server.errors import ZooError
from server.gateway_client import OwnershipChanged, client
from server.gatewaypb import gateway_pb2 as protocol

VERSION = 1
VNC_PORT = 5900
STREAM_ENDS = {"pty_exit", "tunnel_close"}
QUEUE_SIZE = 64
STATUS_TTL = 1
logger = logging.getLogger(__name__)


class GuestError(RuntimeError):
    pass


def token(sandbox_id: str) -> str:
    key = (os.environ.get("ZOO_SECRETS_KEY") or os.environ["JWT_SECRET"]).encode()
    return hmac.new(key, f"zoo-guest:{sandbox_id}".encode(), hashlib.sha256).hexdigest()


def remote_guest_url() -> str:
    try:
        target = urlsplit(os.environ.get("ZOO_API_URL", ""))
        if (
            target.scheme not in ("http", "https")
            or not target.hostname
            or target.username is not None
            or target.query
            or target.fragment
        ):
            raise ValueError("invalid public API URL")
        if target.port is not None and not 1 <= target.port <= 65535:
            raise ValueError("invalid public API port")
    except ValueError as error:
        raise ZooError(
            "unavailable", "ZOO_API_URL is required for VM guests", "Set a public HTTP(S) API URL."
        ) from error
    scheme = "wss" if target.scheme == "https" else "ws"
    return urlunsplit((scheme, target.netloc, target.path.rstrip("/") + "/guest/connect", "", ""))


def guest_env(sandbox_id: str, remote: bool) -> dict[str, str]:
    if remote:
        url = remote_guest_url()
    elif os.environ.get("ZOO_RUNTIME", "docker") == "docker":
        url = "ws://gateway:8001/guest/connect"
    elif os.environ.get("ZOO_RUNTIME") == "kube":
        from server import kube

        url = kube.guest_url()
    else:
        raise ZooError("bad_request", "ZOO_RUNTIME must be docker or kube")
    return {"ZOO_GUEST_URL": url, "ZOO_GUEST_TOKEN": token(sandbox_id), "ZOO_SANDBOX_ID": sandbox_id}


def guest_endpoint() -> tuple[str, int]:
    target = urlsplit(remote_guest_url())
    assert target.hostname is not None
    return target.hostname, target.port or (443 if target.scheme == "wss" else 80)


def pack(header: dict, payload: bytes = b"") -> bytes:
    encoded = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack(">I", len(encoded)) + encoded + payload


def unpack(frame: bytes) -> tuple[dict, bytes]:
    if len(frame) < 4:
        raise ValueError("short frame")
    length = struct.unpack(">I", frame[:4])[0]
    if length > len(frame) - 4:
        raise ValueError("header overruns frame")
    return json.loads(frame[4 : 4 + length]), frame[4 + length :]


def reply(header_json: bytes, payload: bytes) -> tuple[dict, bytes]:
    header = json.loads(header_json)
    if not isinstance(header, dict):
        raise GuestError("invalid guest reply")
    if not header.get("ok"):
        raise GuestError(header.get("error") or "guest error")
    return header.get("result") or {}, payload


class Guest:
    def __init__(self, sandbox_id: str, status: dict):
        self.sandbox_id = sandbox_id
        self.streams: dict[str, GuestStream] = {}
        self.update_status(status)

    @property
    def loop(self):
        return client.loop

    def update_status(self, status: dict):
        self.version = status.get("version")
        self.os = status.get("os", "")
        self.services = set(status.get("services") or [])
        self.metrics = status.get("metrics")
        self.seen = time.monotonic() - float(status.get("quiet") or 0)

    def has(self, service: str) -> bool:
        return service in self.services

    async def request(self, op: str, args: dict, payload: bytes, timeout: float) -> tuple[dict, bytes]:
        found = await client.guest_call(self.sandbox_id, op, args, payload, timeout)
        return reply(found.header_json, found.payload)

    def call(self, op: str, args: dict, payload: bytes = b"", timeout: float = 600) -> tuple[dict, bytes]:
        return client.sync(self.request(op, args, payload, timeout))

    async def call_async(self, op: str, args: dict, payload: bytes = b"", timeout: float = 30) -> tuple[dict, bytes]:
        return await self.request(op, args, payload, timeout)

    async def notify(self, op: str, args: dict, payload: bytes = b""):
        stream = self.streams.get(args.get("stream", ""))
        if stream is None:
            raise GuestError("guest stream is closed")
        await stream.send(op, args, payload)

    def run(
        self,
        argv: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        merge: bool = False,
        stdin: bytes = b"",
        timeout: float = 600,
    ) -> tuple[int, bytes, bytes]:
        args = {"argv": argv, "env": env or {}, "cwd": cwd or "", "merge": merge}
        result, out = self.call("exec", args, stdin, timeout)
        n = result["stdout_len"]
        return result["exit_code"], out[:n], out[n:]

    def read(self, path: str) -> bytes:
        return self.call("read", {"path": path})[1]

    def write(self, path: str, data: bytes):
        self.call("write", {"path": path}, data)

    def probe_tunnel(self, port: int) -> bool:
        async def probe():
            tunnel = Tunnel(self)
            try:
                await asyncio.wait_for(tunnel.open(port), 10)
                return True
            except (GuestError, ZooError, TimeoutError):
                return False
            finally:
                await tunnel.close()

        return client.sync(probe())


class GuestStream:
    def __init__(self, guest: Guest, operation: str):
        self.guest = guest
        self.operation = operation
        self.stream = uuid.uuid4().hex
        self.outgoing: asyncio.Queue[protocol.Frame] = asyncio.Queue(QUEUE_SIZE)
        self.opened: asyncio.Future | None = None
        self.task: asyncio.Task | None = None
        self.closed = False

    async def start_stream(self, args: dict):
        if self.task is not None or self.closed:
            raise GuestError("guest stream is already opened or closed")
        self.opened = asyncio.get_running_loop().create_future()
        initial = protocol.Frame(
            sandbox_id=self.guest.sandbox_id,
            header_json=json.dumps({"id": 1, "op": self.operation, "args": {"stream": self.stream, **args}}).encode(),
        )
        self.guest.streams[self.stream] = self
        self.task = asyncio.create_task(self.run(initial), name=f"guest-{self.operation}-{self.stream}")
        try:
            await asyncio.wait_for(asyncio.shield(self.opened), 30)
        except BaseException:
            await self.close()
            raise

    async def frames(self, initial: protocol.Frame) -> AsyncIterator[protocol.Frame]:
        yield initial
        while True:
            yield await self.outgoing.get()

    async def run(self, initial: protocol.Frame):
        try:
            for attempt in range(2):
                try:
                    async with contextlib.aclosing(
                        client.guest_stream(self.guest.sandbox_id, self.frames(initial))
                    ) as stream:
                        async for frame in stream:
                            header = json.loads(frame.header_json)
                            if header.get("id") == 1:
                                reply(frame.header_json, frame.payload)
                                if self.opened is not None and not self.opened.done():
                                    self.opened.set_result(None)
                            else:
                                self.deliver(header, frame.payload)
                                if header.get("op") in STREAM_ENDS:
                                    return
                    return
                except OwnershipChanged as error:
                    if attempt or not error.before_dispatch:
                        raise
                    await client.refresh()
        except Exception as error:
            if self.opened is not None and not self.opened.done():
                self.opened.set_exception(error)
            logger.debug("guest stream ended", exc_info=True)
        finally:
            self.closed = True
            self.guest.streams.pop(self.stream, None)
            if self.opened is not None and not self.opened.done():
                self.opened.set_exception(GuestError("guest stream closed before opening"))
            self.ended()

    async def send(self, op: str, args: dict, payload: bytes):
        if self.closed:
            raise GuestError("guest stream is closed")
        frame = protocol.Frame(
            sandbox_id=self.guest.sandbox_id, header_json=json.dumps({"op": op, "args": args}).encode(), payload=payload
        )
        try:
            async with asyncio.timeout(5):
                await self.outgoing.put(frame)
        except TimeoutError as error:
            await self.close()
            raise ZooError("unavailable", "guest stream writer is backpressured") from error

    async def close(self):
        self.closed = True
        self.guest.streams.pop(self.stream, None)
        if self.task is not None and self.task is not asyncio.current_task():
            self.task.cancel()
            try:
                async with asyncio.timeout(5):
                    await self.task
            except (asyncio.CancelledError, TimeoutError):
                pass
        self.ended()
        if self.opened is not None and self.opened.done() and not self.opened.cancelled():
            self.opened.exception()

    def deliver(self, header: dict, payload: bytes):
        raise NotImplementedError

    def ended(self):
        raise NotImplementedError


class Terminal(GuestStream):
    def __init__(self, guest: Guest):
        super().__init__(guest, "pty_open")
        self.events: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(QUEUE_SIZE)
        self.exited = False

    async def open(self, cols: int, rows: int, argv: list[str] | None = None):
        await super().start_stream({"argv": argv or [], "cols": cols, "rows": rows})

    def deliver(self, header: dict, payload: bytes):
        if header.get("op") == "pty_data":
            try:
                self.events.put_nowait(("data", payload))
            except asyncio.QueueFull as error:
                raise ZooError("unavailable", "terminal reader is backpressured") from error
        elif header.get("op") == "pty_exit":
            self.ended(header.get("exit_code"))

    def ended(self, code: int | None = None):
        if not self.exited:
            self.exited = True
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait(("exit", code))

    async def write(self, data: bytes):
        await self.send("pty_input", {"stream": self.stream}, data)

    async def resize(self, cols: int, rows: int):
        await self.send("pty_resize", {"stream": self.stream, "cols": cols, "rows": rows}, b"")


class Tunnel(GuestStream):
    def __init__(self, guest: Guest):
        super().__init__(guest, "tunnel_open")
        self.received: asyncio.Queue[bytes] = asyncio.Queue(QUEUE_SIZE)
        self.exited = False

    async def open(self, port: int):
        await super().start_stream({"port": port})

    def deliver(self, header: dict, payload: bytes):
        if header.get("op") == "tunnel_data":
            try:
                self.received.put_nowait(payload)
            except asyncio.QueueFull as error:
                raise ZooError("unavailable", "tunnel reader is backpressured") from error
        elif header.get("op") == "tunnel_close":
            self.ended()

    def ended(self):
        if not self.exited:
            self.exited = True
            if self.received.full():
                self.received.get_nowait()
            self.received.put_nowait(b"")

    async def read(self) -> bytes:
        if self.exited and self.received.empty():
            return b""
        return await self.received.get()

    async def write(self, data: bytes):
        await self.send("tunnel_write", {"stream": self.stream}, data)


class Hub:
    def __init__(self):
        self.guests: dict[str, Guest] = {}
        self.runtimes: dict[str, str] = {}
        self.statuses: dict[str, tuple[float, dict]] = {}

    def bind(self, runtime_id: str, sandbox_id: str):
        self.runtimes[runtime_id] = sandbox_id

    async def status_async(self, sandbox_id: str) -> dict:
        client.owner(sandbox_id)
        cached = self.statuses.get(sandbox_id)
        if cached is not None and time.monotonic() - cached[0] < STATUS_TTL:
            return cached[1]
        status = await client.guest_status(sandbox_id)
        if not status.connected:
            await self.drop_async(sandbox_id)
            found = {"connected": False}
        else:
            found = {
                "connected": True,
                "version": status.version,
                "os": status.os,
                "services": list(status.services),
                "metrics": json.loads(status.metrics_json) if status.metrics_json else None,
                "quiet": status.quiet_seconds,
                "heartbeat": status.heartbeat,
            }
        self.statuses[sandbox_id] = (time.monotonic(), found)
        return found

    def status(self, sandbox_id: str) -> dict:
        return client.sync(self.status_async(sandbox_id))

    async def for_sandbox_async(self, sandbox_id: str) -> Guest | None:
        status = await self.status_async(sandbox_id)
        if not status["connected"]:
            return None
        guest = self.guests.get(sandbox_id)
        if guest is None:
            guest = self.guests[sandbox_id] = Guest(sandbox_id, status)
        else:
            guest.update_status(status)
        return guest

    def for_sandbox(self, sandbox_id: str) -> Guest | None:
        return client.sync(self.for_sandbox_async(sandbox_id))

    def sandbox_of(self, runtime_id: str) -> str | None:
        sandbox_id = self.runtimes.get(runtime_id)
        if sandbox_id is None:
            with db_manager.session() as db:
                sandbox = db.get_sandbox_by_runtime_id(runtime_id=runtime_id)
            if sandbox is None:
                return None
            sandbox_id = self.runtimes[runtime_id] = sandbox.id
        return sandbox_id

    def for_runtime(self, runtime_id: str) -> Guest | None:
        sandbox_id = self.sandbox_of(runtime_id)
        return None if sandbox_id is None else self.for_sandbox(sandbox_id)

    def heartbeat(self, sandbox_id: str, within: float = 45) -> bool:
        status = self.status(sandbox_id)
        return bool(status.get("connected")) and (
            "metrics" not in status.get("services", []) or status["quiet"] < within
        )

    def wait_for_guest(self, sandbox_id: str, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.for_sandbox(sandbox_id) is not None:
                return True
            time.sleep(0.25)
        return False

    def wait_for_vnc(self, sandbox_id: str, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            guest = self.for_sandbox(sandbox_id)
            if guest is not None and guest.has("tunnel") and guest.probe_tunnel(VNC_PORT):
                return True
            time.sleep(0.25)
        return False

    def wait_for_desktop(self, sandbox_id: str, timeout: float = 30) -> bool:
        """Whether the sandbox's desktop came up in time: its VNC server answers and a window manager runs."""
        deadline = time.monotonic() + timeout
        if not self.wait_for_vnc(sandbox_id, timeout):
            return False
        guest = self.for_sandbox(sandbox_id)
        if guest is None:
            return False
        left = max(0.0, deadline - time.monotonic())
        result, _ = guest.call("desktop_ready", {"display": ":1", "timeout_ms": int(left * 1000)}, timeout=left + 5)
        return bool(result.get("ready"))

    async def drop_async(self, sandbox_id: str):
        self.statuses.pop(sandbox_id, None)
        guest = self.guests.pop(sandbox_id, None)
        if guest is not None:
            await asyncio.gather(*(stream.close() for stream in list(guest.streams.values())))

    def drop(self, sandbox_id: str):
        client.sync(self.drop_async(sandbox_id))

    async def shutdown(self):
        await asyncio.gather(*(self.drop_async(identity) for identity in list(self.guests)))
        self.runtimes.clear()
        self.statuses.clear()


hub = Hub()
