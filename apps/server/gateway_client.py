"""Async native gateway transport. Channels belong to the API loop, never to tool threads."""

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import socket
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine, Iterable
from typing import Any, TypeVar

import grpc

from apps.server.errors import ZooError
from apps.server.gatewaypb import gateway_pb2 as protocol
from apps.server.gatewaypb.gateway_pb2_grpc import GatewayStub
from db.connection import db_manager

REFRESH_SECONDS = 5
MAX_MESSAGE_BYTES = 128 << 20
Result = TypeVar("Result")
logger = logging.getLogger(__name__)


def normalize_peers(addresses: Iterable[str]) -> tuple[str, ...]:
    peers = set()
    for value in addresses:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address):
            if address.scope_id:
                raise ValueError("gateway peers cannot have an IPv6 zone")
            address = address.ipv4_mapped or address
        if address.is_unspecified or address.is_multicast:
            raise ValueError("gateway peers must be unicast IP addresses")
        peers.add(str(address))
    if not peers:
        raise ValueError("gateway DNS returned no peers")
    return tuple(sorted(peers))


def peer_owner(peers: Iterable[str], identity: str) -> str:
    if not identity:
        raise ZooError("bad_request", "gateway resource ID is required")
    return max(normalize_peers(peers), key=lambda peer: hashlib.sha256(f"{peer}\x00{identity}".encode()).digest())


def root_certificate() -> bytes:
    rows = db_manager.execute("SELECT certificate FROM node_authority WHERE id = 1")
    if not rows or not isinstance(rows[0][0], str) or not rows[0][0].strip():
        raise ZooError("unavailable", "gateway CA is missing", "Bootstrap the gateway CA before starting the API.")
    return rows[0][0].encode()


def ownership_error(error: grpc.aio.AioRpcError) -> str | None:
    if error.code() != grpc.StatusCode.FAILED_PRECONDITION or error.details() != "gateway ownership changed":
        return None
    owners = [value for key, value in error.trailing_metadata() or () if key == "zoo-owner"]
    if len(owners) != 1 or not isinstance(owners[0], str):
        return None
    try:
        return normalize_peers([owners[0]])[0]
    except ValueError:
        return None


class OwnershipChanged(ZooError):
    """Only before_dispatch permits reopening with initial_frame and the remaining input source."""

    def __init__(self, owner: str, before_dispatch: bool, initial_frame: Any = None):
        super().__init__("unavailable", "gateway ownership changed", "Refresh gateway membership before reopening.")
        self.owner = owner
        self.before_dispatch = before_dispatch
        self.initial_frame = initial_frame


def rpc_error(error: grpc.aio.AioRpcError, kind: str) -> ZooError:
    status = error.code()
    codes = {
        grpc.StatusCode.DEADLINE_EXCEEDED: "timeout",
        grpc.StatusCode.INVALID_ARGUMENT: "bad_request",
        grpc.StatusCode.UNAUTHENTICATED: "unauthenticated",
        grpc.StatusCode.PERMISSION_DENIED: "forbidden",
        grpc.StatusCode.NOT_FOUND: "not_found",
        grpc.StatusCode.FAILED_PRECONDITION: "conflict",
        grpc.StatusCode.UNIMPLEMENTED: "not_supported",
        grpc.StatusCode.INTERNAL: "upstream_error",
        grpc.StatusCode.DATA_LOSS: "upstream_error",
    }
    code = codes.get(status, "guest_unavailable" if kind == "guest" else "unavailable")
    hint = (
        "Check the sandbox guest and gateway connection."
        if kind == "guest"
        else "Check the host's zoo-node connection."
    )
    return ZooError(code, error.details() or f"{kind} gateway request failed", hint)


class GatewayClient:
    def __init__(self, peer_name: str | None = None, port: int | None = None):
        self.peer_name = peer_name
        self.port = port
        self.loop: asyncio.AbstractEventLoop | None = None
        self.channels: dict[str, grpc.aio.Channel] = {}
        self.stubs: dict[str, Any] = {}
        self.members: tuple[str, ...] = ()
        self.failure: ZooError | None = None
        self.credentials: grpc.ChannelCredentials | None = None
        self.metadata: tuple[tuple[str, str], ...] = ()
        self.monitor: asyncio.Task | None = None
        self.refresh_lock: asyncio.Lock | None = None
        self.started = False

    def require_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if loop is not self.loop or not self.started:
            raise ZooError("unavailable", "gateway client is not started on this event loop")
        return loop

    async def start(self):
        loop = asyncio.get_running_loop()
        if self.started:
            self.require_loop()
            return
        if self.loop is not None:
            raise ZooError("conflict", "gateway client startup is already in progress")
        self.loop = loop
        try:
            self.peer_name = (
                self.peer_name if self.peer_name is not None else os.environ.get("ZOO_GATEWAY_PEERS", "gateway")
            )
            if not self.peer_name or self.peer_name.strip() != self.peer_name:
                raise ZooError("bad_request", "ZOO_GATEWAY_PEERS must be a DNS name or IP address")
            try:
                self.port = self.port if self.port is not None else int(os.environ.get("ZOO_GATEWAY_GRPC_PORT", "7444"))
            except ValueError as error:
                raise ZooError("bad_request", "ZOO_GATEWAY_GRPC_PORT must be a port") from error
            if not 1 <= self.port <= 65535:
                raise ZooError("bad_request", "ZOO_GATEWAY_GRPC_PORT must be between 1 and 65535")
            key = os.environ.get("ZOO_SECRETS_KEY") or os.environ.get("JWT_SECRET")
            if not key:
                raise ZooError("unavailable", "gateway authentication key is missing")
            token = hmac.new(key.encode(), b"zoo-gateway", hashlib.sha256).hexdigest()
            self.metadata = (("authorization", f"Bearer {token}"),)
            certificate = await asyncio.to_thread(root_certificate)
            self.credentials = grpc.ssl_channel_credentials(root_certificates=certificate)
            self.refresh_lock = asyncio.Lock()
            self.failure = ZooError("unavailable", "gateway DNS membership has not been loaded")
            self.started = True
            await self.refresh()
            self.monitor = asyncio.create_task(self.watch_membership(), name="gateway-membership")
        except BaseException:
            await self.shutdown()
            raise

    async def refresh(self):
        loop = self.require_loop()
        assert self.refresh_lock is not None
        assert self.peer_name is not None
        async with self.refresh_lock:
            try:
                async with asyncio.timeout(REFRESH_SECONDS):
                    addresses = await loop.getaddrinfo(self.peer_name, self.port, type=socket.SOCK_STREAM)
                    peers = normalize_peers(str(address[4][0]) for address in addresses)
            except (OSError, ValueError, TimeoutError, asyncio.CancelledError) as error:
                self.failure = ZooError(
                    "unavailable", "gateway DNS membership is unavailable", "Check ZOO_GATEWAY_PEERS and DNS."
                )
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise self.failure from error
            self.members, self.failure = peers, None

    async def watch_membership(self):
        loop = self.require_loop()
        next_refresh = loop.time() + REFRESH_SECONDS
        while True:
            await asyncio.sleep(max(0, next_refresh - loop.time()))
            try:
                await self.refresh()
            except ZooError:
                logger.warning("gateway DNS membership refresh failed")
            next_refresh = max(next_refresh + REFRESH_SECONDS, loop.time())

    def owner(self, identity: str) -> str:
        if not self.started:
            raise ZooError("unavailable", "gateway client is not started")
        if self.failure is not None:
            raise self.failure
        return peer_owner(self.members, identity)

    def stub(self, identity: str) -> Any:
        self.require_loop()
        owner = self.owner(identity)
        return self.peer_stub(owner)

    def peer_stub(self, owner: str) -> Any:
        self.require_loop()
        if self.failure is not None:
            raise self.failure
        if owner not in self.members:
            raise ZooError("unavailable", "gateway owner is outside the current membership")
        if owner not in self.channels:
            assert self.credentials is not None
            target = f"[{owner}]:{self.port}" if ":" in owner else f"{owner}:{self.port}"
            channel = grpc.aio.secure_channel(
                target,
                self.credentials,
                options=(
                    ("grpc.enable_retries", 0),
                    ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
                    ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
                ),
            )
            self.channels[owner] = channel
            self.stubs[owner] = GatewayStub(channel)
        return self.stubs[owner]

    async def readiness(self, timeout: float = 5):
        self.require_loop()
        if self.failure is not None:
            raise self.failure
        for member in self.members:
            self.peer_stub(member)
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*(self.channels[member].channel_ready() for member in self.members))
                await self.guest_status("readiness-probe", timeout=timeout)
        except TimeoutError as error:
            raise ZooError(
                "unavailable", "gateway replicas are not ready", "Check gateway TLS and replica health."
            ) from error

    async def unary(self, method: str, identity: str, request: Any, timeout: float, kind: str) -> Any:
        loop = self.require_loop()
        if not math.isfinite(timeout) or timeout <= 0:
            raise ZooError("bad_request", "gateway timeout must be positive")
        deadline = loop.time() + timeout
        try:
            async with asyncio.timeout_at(deadline):
                for attempt in range(2):
                    call = getattr(self.stub(identity), method)
                    try:
                        return await call(request, metadata=self.metadata, timeout=deadline - loop.time())
                    except grpc.aio.AioRpcError as error:
                        owner = ownership_error(error)
                        if owner is None:
                            raise rpc_error(error, kind) from error
                        if attempt:
                            raise OwnershipChanged(owner, True) from error
                        await self.refresh()
        except TimeoutError as error:
            raise ZooError("timeout", "gateway request timed out") from error

    async def guest_call(
        self, sandbox_id: str, op: str, args: dict | None = None, payload: bytes = b"", timeout: float = 60
    ) -> protocol.GuestCallReply:
        if not op:
            raise ZooError("bad_request", "guest operation is required")
        request = protocol.GuestCallRequest(
            sandbox_id=sandbox_id,
            op=op,
            header_json=json.dumps({"args": args if args is not None else {}}, allow_nan=False).encode(),
            payload=payload,
        )
        return await self.unary("GuestCall", sandbox_id, request, timeout, "guest")

    async def guest_status(self, sandbox_id: str, timeout: float = 5) -> protocol.GuestStatusReply:
        return await self.unary(
            "GuestStatus", sandbox_id, protocol.GuestStatusRequest(sandbox_id=sandbox_id), timeout, "guest"
        )

    async def screen(self, method: str, request: protocol.ScreenRequest, timeout: float = 60) -> protocol.ScreenReply:
        """One of the sandbox VNC session's Screen*, Pointer, Key and Type calls."""
        return await self.unary(method, request.sandbox_id, request, timeout, "guest")

    async def node_status(self, server_id: str, timeout: float = 5) -> protocol.NodeStatusReply:
        return await self.unary(
            "NodeStatus", server_id, protocol.NodeStatusRequest(server_id=server_id), timeout, "node"
        )

    async def stream(
        self, method: str, identity: str, requests: AsyncIterator, kind: str, metadata: tuple[tuple[str, str], ...] = ()
    ) -> AsyncGenerator[Any, None]:
        self.require_loop()
        rpc = getattr(self.stub(identity), method)
        acknowledged = asyncio.Event()
        opening: list[Any] = []
        input_failures: list[Exception] = []

        async def outgoing():
            try:
                first = await anext(requests, None)
                if first is None:
                    return
                opening.append(first)
                yield first
                await acknowledged.wait()
                async for frame in requests:
                    yield frame
            except Exception as error:
                input_failures.append(error)
                raise

        call = rpc(outgoing(), metadata=self.metadata + metadata)
        received = False
        try:
            async for response in call:
                received = True
                acknowledged.set()
                yield response
        except grpc.aio.AioRpcError as error:
            if input_failures:
                raise input_failures[0] from error
            owner = ownership_error(error)
            if owner is not None:
                raise OwnershipChanged(owner, not received, opening[0] if opening else None) from error
            raise rpc_error(error, kind) from error
        except asyncio.CancelledError:
            if input_failures:
                raise input_failures[0]
            raise
        finally:
            call.cancel()

    async def guest_stream(
        self, sandbox_id: str, frames: AsyncIterator[protocol.Frame]
    ) -> AsyncGenerator[protocol.Frame, None]:
        async def requests():
            async for frame in frames:
                if frame.sandbox_id and frame.sandbox_id != sandbox_id:
                    raise ZooError("bad_request", "guest stream cannot change sandbox")
                yield protocol.Frame(sandbox_id=sandbox_id, header_json=frame.header_json, payload=frame.payload)

        async with contextlib.aclosing(self.stream("GuestStream", sandbox_id, requests(), "guest")) as stream:
            async for frame in stream:
                yield frame

    async def node_stream(self, server_id: str, target: str, data: AsyncIterator[bytes]) -> AsyncGenerator[bytes, None]:
        if target != "ssh":
            raise ZooError("not_supported", "node tunnels only support the ssh target")

        async def requests():
            async for chunk in data:
                yield protocol.Bytes(data=chunk)

        async with contextlib.aclosing(
            self.stream("NodeTunnel", server_id, requests(), "node", (("server_id", server_id), ("target", target)))
        ) as stream:
            async for frame in stream:
                yield frame.data

    async def viewer_stream(
        self, sandbox_id: str, view_only: bool, data: AsyncIterator[bytes]
    ) -> AsyncGenerator[bytes, None]:
        """A viewer's RFB bytes to the sandbox's VNC session and back; the gateway plays the VNC server."""

        async def requests():
            yield protocol.ViewerFrame(sandbox_id=sandbox_id, view_only=view_only)
            async for chunk in data:
                yield protocol.ViewerFrame(sandbox_id=sandbox_id, data=chunk)

        async with contextlib.aclosing(self.stream("ViewerStream", sandbox_id, requests(), "guest")) as stream:
            async for frame in stream:
                yield frame.data

    def sync(self, coroutine: Coroutine[Any, Any, Result]) -> Result:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self.loop is not None and running is self.loop:
            coroutine.close()
            raise RuntimeError("gateway sync() cannot run on the API event loop; await the operation instead")
        if not self.started or self.loop is None or not self.loop.is_running():
            coroutine.close()
            raise ZooError("unavailable", "gateway client event loop is not running")
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        except RuntimeError as error:
            coroutine.close()
            raise ZooError("unavailable", "gateway client event loop stopped") from error
        try:
            return future.result()
        except BaseException:
            future.cancel()
            raise

    async def shutdown(self):
        if self.loop is not None and asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("gateway shutdown must run on the API event loop")
        self.started = False
        if self.monitor is not None:
            self.monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor
            self.monitor = None
        channels, self.channels = self.channels, {}
        self.stubs.clear()
        await asyncio.gather(*(channel.close(grace=5) for channel in channels.values()))
        self.members, self.failure = (), None
        self.credentials = None
        self.refresh_lock = None
        self.loop = None


client = GatewayClient()


def ready():
    client.sync(client.readiness())
