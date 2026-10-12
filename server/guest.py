"""The API end of zoo-guest, the agent inside each Linux sandbox (see guest/).

The guest dials /guest/connect, proves which sandbox it is with a per-sandbox token, and then serves tool calls over
that one websocket. Tools ask the hub for a sandbox's guest and fall back to docker exec when it has none, so
sandboxes booted before the guest existed keep working."""

import asyncio
import hashlib
import hmac
import itertools
import json
import logging
import os
import struct
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlparse

import websockets
from fastapi import WebSocket

from db.connection import db_manager
from server import gateway

VERSION = 1
HELLO_TIMEOUT = 10
# what the guest dials: sandboxes on the API's own docker host use ZOO_GUEST_URL, sandboxes on remote servers can't
# resolve that name and use ZOO_GUEST_REMOTE_URL. Unset leaves those sandboxes on the fallback path.
# x11vnc's port inside a desktop sandbox, reached through a guest tunnel rather than a published port
VNC_PORT = 5900
# frames the guest pushes for an open stream rather than as a reply; the second set ends the stream
STREAM_OPS = {"pty_data", "pty_exit", "tunnel_data", "tunnel_close"}
STREAM_ENDS = {"pty_exit", "tunnel_close"}
LOCAL_URL = os.environ.get("ZOO_GUEST_URL", "")
REMOTE_URL = os.environ.get("ZOO_GUEST_REMOTE_URL", "")

logger = logging.getLogger(__name__)


class GuestError(RuntimeError):
    pass


def token(sandbox_id: str) -> str:
    # derived rather than stored, so guests reconnect after an API restart
    key = (os.environ.get("ZOO_SECRETS_KEY") or os.environ["JWT_SECRET"]).encode()
    return hmac.new(key, f"zoo-guest:{sandbox_id}".encode(), hashlib.sha256).hexdigest()


# access_url of a desktop whose VNC is reached through its guest, with no published port
TUNNEL_URL = "guest://vnc"


def guest_env(sandbox_id: str, remote: bool) -> dict[str, str]:
    url = REMOTE_URL if remote else LOCAL_URL
    if not url:
        return {}
    return {"ZOO_GUEST_URL": url, "ZOO_GUEST_TOKEN": token(sandbox_id), "ZOO_SANDBOX_ID": sandbox_id}


def guest_endpoint() -> tuple[str, int] | None:
    """The host and port VM guests dial, which a VM's network policy must always allow."""
    target = urlparse(REMOTE_URL)
    if not target.hostname:
        return None
    return target.hostname, target.port or (443 if target.scheme == "wss" else 80)


def pack(header: dict, payload: bytes = b"") -> bytes:
    """A frame: 4-byte big-endian header length, JSON header, raw payload (file data, screenshots, output)."""
    h = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack(">I", len(h)) + h + payload


def unpack(frame: bytes) -> tuple[dict, bytes]:
    if len(frame) < 4:
        raise ValueError("short frame")
    (n,) = struct.unpack(">I", frame[:4])
    if n > len(frame) - 4:
        raise ValueError("header overruns frame")
    return json.loads(frame[4 : 4 + n]), frame[4 + n :]


class Guest:
    """One connected guest. Tools call it from worker threads; its requests run on the loop that serves it."""

    def __init__(self, websocket: WebSocket, hello: dict):
        self.websocket = websocket
        self.loop = asyncio.get_running_loop()
        self.version = hello.get("version")
        self.os = hello.get("os", "")
        self.services = set(hello.get("services") or [])
        self.metrics: dict[str, Any] | None = None
        # when it last sent anything; a guest with the metrics service sends every 10 seconds
        self.seen = time.monotonic()
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}
        # terminal stream id -> what receives its pty_data and pty_exit frames
        self.streams: dict[str, Callable[[dict, bytes], None]] = {}

    def has(self, service: str) -> bool:
        return service in self.services

    async def request(self, op: str, args: dict, payload: bytes, timeout: float) -> tuple[dict, bytes]:
        request_id = next(self.ids)
        future = self.loop.create_future()
        self.pending[request_id] = future
        try:
            await self.websocket.send_bytes(pack({"id": request_id, "op": op, "args": args}, payload))
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    def call(self, op: str, args: dict, payload: bytes = b"", timeout: float = 600) -> tuple[dict, bytes]:
        return asyncio.run_coroutine_threadsafe(self.request(op, args, payload, timeout), self.loop).result()

    async def call_async(self, op: str, args: dict, payload: bytes = b"", timeout: float = 30) -> tuple[dict, bytes]:
        """call() for coroutines, from any event loop."""
        return await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(self.request(op, args, payload, timeout), self.loop)
        )

    async def notify(self, op: str, args: dict, payload: bytes = b""):
        """Sends a frame the guest doesn't answer; the guest applies them in the order they're sent."""
        frame = pack({"op": op, "args": args}, payload)
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.websocket.send_bytes(frame), self.loop))

    def resolve(self, header: dict, payload: bytes):
        future = self.pending.get(header.get("id", 0))
        if future is None or future.done():
            return
        if header.get("ok"):
            future.set_result((header.get("result") or {}, payload))
        else:
            future.set_exception(GuestError(header.get("error") or "guest error"))

    def disconnected(self):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(GuestError("guest disconnected"))
        for deliver in list(self.streams.values()):
            deliver({"op": "pty_exit", "exit_code": None, "error": "guest disconnected"}, b"")
        self.streams.clear()

    def exec_run(
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

    def screenshot(self, display: str, format: str, scale: float, quality: int) -> bytes:
        args = {"display": display, "format": format, "scale": scale, "quality": quality}
        return self.call("screenshot", args, timeout=30)[1]

    def screen_diff(self, display: str, session: str, format: str, scale: float, quality: int) -> tuple[dict, bytes]:
        args = {"display": display, "session": session, "format": format, "scale": scale, "quality": quality}
        return self.call("screen_diff", args, timeout=30)

    def wait_until_stable(self, display: str, timeout: float, quiet_ms: int, threshold: float) -> dict:
        args = {"display": display, "timeout_ms": int(timeout * 1000), "quiet_ms": quiet_ms, "threshold": threshold}
        return self.call("wait_until_stable", args, timeout=timeout + 10)[0]

    def probe_tunnel(self, port: int) -> bool:
        """Whether a tunnel to the port opens, for waiting on a service in the sandbox to come up."""
        stream = uuid.uuid4().hex
        try:
            self.call("tunnel_open", {"stream": stream, "port": port}, timeout=10)
        except Exception:
            return False
        # whatever the port sends before the close arrives for no stream and is dropped
        frame = pack({"op": "tunnel_close", "args": {"stream": stream}})
        asyncio.run_coroutine_threadsafe(self.websocket.send_bytes(frame), self.loop).result()
        return True


class Terminal:
    """A shell on a pseudo-terminal in the sandbox. Output and the exit arrive on `events` on the loop that opened
    it, as ("data", bytes) and then ("exit", exit code or None)."""

    def __init__(self, guest: Guest):
        self.guest = guest
        self.stream = uuid.uuid4().hex
        self.loop = asyncio.get_running_loop()
        self.events: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def deliver(self, header: dict, payload: bytes):
        event = ("data", payload) if header.get("op") == "pty_data" else ("exit", header.get("exit_code"))
        self.loop.call_soon_threadsafe(self.events.put_nowait, event)

    async def open(self, cols: int, rows: int, argv: list[str] | None = None):
        # registered before asking, since output can arrive ahead of the reply
        self.guest.streams[self.stream] = self.deliver
        try:
            await self.guest.call_async(
                "pty_open", {"stream": self.stream, "argv": argv or [], "cols": cols, "rows": rows}
            )
        except BaseException:
            self.guest.streams.pop(self.stream, None)
            raise

    async def write(self, data: bytes):
        await self.guest.notify("pty_input", {"stream": self.stream}, data)

    async def resize(self, cols: int, rows: int):
        await self.guest.notify("pty_resize", {"stream": self.stream, "cols": cols, "rows": rows})

    async def close(self):
        if self.guest.streams.pop(self.stream, None) is not None:
            try:
                await self.guest.notify("pty_kill", {"stream": self.stream})
            except Exception:
                logger.debug("terminal already gone", exc_info=True)


class Tunnel:
    """A byte stream to a port on the sandbox's loopback. read() returns b"" once either end closes it."""

    def __init__(self, guest: Guest):
        self.guest = guest
        self.stream = uuid.uuid4().hex
        self.loop = asyncio.get_running_loop()
        self.received: asyncio.Queue[bytes] = asyncio.Queue()

    def deliver(self, header: dict, payload: bytes):
        data = payload if header.get("op") == "tunnel_data" else b""
        self.loop.call_soon_threadsafe(self.received.put_nowait, data)

    async def open(self, port: int):
        # registered before asking, since the port can speak first (an RFB banner) ahead of the reply
        self.guest.streams[self.stream] = self.deliver
        try:
            await self.guest.call_async("tunnel_open", {"stream": self.stream, "port": port})
        except BaseException:
            self.guest.streams.pop(self.stream, None)
            raise

    async def read(self) -> bytes:
        return await self.received.get()

    async def write(self, data: bytes):
        await self.guest.notify("tunnel_write", {"stream": self.stream}, data)

    async def close(self):
        if self.guest.streams.pop(self.stream, None) is not None:
            try:
                await self.guest.notify("tunnel_close", {"stream": self.stream})
            except Exception:
                logger.debug("tunnel already gone", exc_info=True)


class Hub:
    def __init__(self):
        self.guests: dict[str, Guest] = {}
        self.runtimes: dict[str, str] = {}

    def bind(self, runtime_id: str, sandbox_id: str):
        self.runtimes[runtime_id] = sandbox_id

    def for_runtime(self, runtime_id: str) -> Guest | None:
        """The guest serving a container, or None to use the fallback path."""
        if not self.guests and not gateway.enabled():
            return None
        sandbox_id = self.runtimes.get(runtime_id)
        if sandbox_id is None:
            with db_manager.session() as db:
                sandbox = db.get_sandbox_by_runtime_id(runtime_id=runtime_id)
            if sandbox is None:
                return None
            sandbox_id = self.runtimes[runtime_id] = sandbox.id
        guest = self.for_sandbox(sandbox_id)
        if guest is None:
            return None
        try:
            if asyncio.get_running_loop() is guest.loop:
                # a blocking call from the guest's own loop would deadlock it
                return None
        except RuntimeError:
            pass
        return guest

    def status(self, sandbox_id: str) -> dict:
        guest = self.guests.get(sandbox_id)
        if guest is None:
            return {"connected": False}
        return {
            "connected": True,
            "version": guest.version,
            "os": guest.os,
            "services": sorted(guest.services),
            "metrics": guest.metrics,
        }

    def heartbeat(self, sandbox_id: str, within: float = 45) -> bool:
        """Whether the sandbox's guest is connected and, when it reports metrics, has been heard from lately."""
        guest = self.guests.get(sandbox_id)
        return guest is not None and (not guest.has("metrics") or time.monotonic() - guest.seen < within)

    def for_sandbox(self, sandbox_id: str) -> Guest | None:
        return self.guests.get(sandbox_id)

    def wait_for_vnc(self, sandbox_id: str, timeout: float = 30) -> bool:
        """Waits for the sandbox's guest to connect and reach x11vnc, which is when its desktop can be viewed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            guest = self.for_sandbox(sandbox_id)
            if guest is not None and guest.has("tunnel") and guest.probe_tunnel(VNC_PORT):
                return True
            time.sleep(0.25)
        return False

    def wait_for_guest(self, sandbox_id: str, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.for_sandbox(sandbox_id) is not None:
                return True
            time.sleep(0.25)
        return False

    def drop(self, sandbox_id: str):
        guest = self.guests.get(sandbox_id)
        if guest is not None:
            asyncio.run_coroutine_threadsafe(guest.websocket.close(), guest.loop)

    async def serve(self, websocket: WebSocket):
        sandbox_id = websocket.headers.get("x-zoo-sandbox", "")
        auth = websocket.headers.get("authorization", "")
        if not sandbox_id or not hmac.compare_digest(auth.encode(), f"Bearer {token(sandbox_id)}".encode()):
            await websocket.close(code=1008, reason="unauthorized")
            return
        await websocket.accept()
        try:
            hello, _ = unpack(await asyncio.wait_for(websocket.receive_bytes(), HELLO_TIMEOUT))
        except Exception:
            await websocket.close(code=1002, reason="expected hello")
            return
        if hello.get("op") != "hello" or hello.get("version") != VERSION:
            await websocket.close(code=1008, reason=f"unsupported guest version; the API speaks {VERSION}")
            return
        guest = Guest(websocket, hello)
        previous = self.guests.get(sandbox_id)
        self.guests[sandbox_id] = guest
        if previous is not None:
            previous.disconnected()
        logger.info("guest connected", extra={"sandbox_id": sandbox_id, "services": sorted(guest.services)})
        try:
            while True:
                header, payload = unpack(await websocket.receive_bytes())
                guest.seen = time.monotonic()
                op = header.get("op")
                if op == "metrics":
                    guest.metrics = header.get("data")
                elif op in STREAM_OPS:
                    deliver = guest.streams.get(header.get("stream", ""))
                    if op in STREAM_ENDS:
                        guest.streams.pop(header.get("stream", ""), None)
                    if deliver is not None:
                        deliver(header, payload)
                else:
                    guest.resolve(header, payload)
        except Exception:
            logger.debug("guest connection ended", extra={"sandbox_id": sandbox_id}, exc_info=True)
        finally:
            guest.disconnected()
            if self.guests.get(sandbox_id) is guest:
                del self.guests[sandbox_id]
            logger.info("guest disconnected", extra={"sandbox_id": sandbox_id})


class RelaySocket:
    """A gateway relay (a websockets client connection) under the names a Guest sends with."""

    def __init__(self, connection):
        self.connection = connection

    async def send_bytes(self, data: bytes):
        await self.connection.send(data)

    async def close(self):
        await self.connection.close()


class RemoteHub(Hub):
    """The guests of a process that reaches them through the gateway (server/gateway.py). Each guest in use gets one
    relay, a websocket to the gateway that speaks the guest protocol; the Guest on this side works as if the guest
    were connected here. Relays run on a loop of their own, so callers on any loop or thread can use them."""

    def __init__(self):
        super().__init__()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.started = threading.Lock()
        self.status_of = gateway.Cached()

    def background(self) -> asyncio.AbstractEventLoop:
        with self.started:
            if self.loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, daemon=True, name="guest-relays").start()
                self.loop = loop
            return self.loop

    def for_sandbox(self, sandbox_id: str) -> Guest | None:
        guest = self.guests.get(sandbox_id)
        if guest is not None:
            return guest
        if not self.status_of(f"/internal/guests/{sandbox_id}").get("connected"):
            return None
        try:
            return asyncio.run_coroutine_threadsafe(self.open(sandbox_id), self.background()).result(15)
        except Exception as e:
            logger.warning("guest relay failed", extra={"sandbox_id": sandbox_id, "error": str(e)})
            return None

    async def open(self, sandbox_id: str) -> Guest:
        if sandbox_id in self.guests:
            return self.guests[sandbox_id]
        connection = await websockets.connect(
            gateway.ws_url(f"/internal/guests/{sandbox_id}/relay"),
            additional_headers=gateway.headers(),
            max_size=None,
            open_timeout=10,
        )
        try:
            first = await asyncio.wait_for(connection.recv(), HELLO_TIMEOUT)
            hello, _ = unpack(first if isinstance(first, bytes) else first.encode())
        except BaseException:
            await connection.close()
            raise
        guest = Guest(cast(Any, RelaySocket(connection)), hello)
        self.liveness(guest, hello)
        self.guests[sandbox_id] = guest
        asyncio.create_task(self.read(sandbox_id, guest, connection))
        return guest

    @staticmethod
    def liveness(guest: Guest, frame: dict):
        guest.metrics = frame.get("metrics")
        guest.seen = time.monotonic() - float(frame.get("quiet") or 0)

    async def read(self, sandbox_id: str, guest: Guest, connection):
        try:
            async for frame in connection:
                header, payload = unpack(frame if isinstance(frame, bytes) else frame.encode())
                op = header.get("op")
                if op == "metrics":
                    self.liveness(guest, header)
                elif op in STREAM_OPS:
                    deliver = guest.streams.get(header.get("stream", ""))
                    if op in STREAM_ENDS:
                        guest.streams.pop(header.get("stream", ""), None)
                    if deliver is not None:
                        deliver(header, payload)
                else:
                    guest.resolve(header, payload)
        except Exception:
            logger.debug("guest relay closed", extra={"sandbox_id": sandbox_id}, exc_info=True)
        finally:
            guest.disconnected()
            if self.guests.get(sandbox_id) is guest:
                del self.guests[sandbox_id]

    def status(self, sandbox_id: str) -> dict:
        found = self.status_of(f"/internal/guests/{sandbox_id}")
        if not found.get("connected"):
            return {"connected": False}
        return {k: v for k, v in found.items() if k != "heartbeat"}

    def heartbeat(self, sandbox_id: str, within: float = 45) -> bool:
        guest = self.guests.get(sandbox_id)
        if guest is not None:
            return super().heartbeat(sandbox_id, within)
        return bool(self.status_of(f"/internal/guests/{sandbox_id}").get("heartbeat"))

    def drop(self, sandbox_id: str):
        guest = self.guests.get(sandbox_id)
        if guest is not None:
            asyncio.run_coroutine_threadsafe(guest.websocket.close(), guest.loop)


hub = RemoteHub() if gateway.enabled() else Hub()
