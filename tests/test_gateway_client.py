import asyncio
import hashlib
import hmac
import json
import socket
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import grpc
import pytest

from apps.server import gateway_client
from apps.server.errors import ZooError
from apps.server.gatewaypb import gateway_pb2 as protocol


def addresses(*peers):
    return [
        (socket.AF_INET6 if ":" in peer else socket.AF_INET, socket.SOCK_STREAM, 6, "", (peer, 7444)) for peer in peers
    ]


def rpc_failure(
    code=grpc.StatusCode.FAILED_PRECONDITION, owner: str | None = "10.0.0.2", detail="gateway ownership changed"
):
    return grpc.aio.AioRpcError(
        code,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(("zoo-owner", owner)) if owner is not None else grpc.aio.Metadata(),
        details=detail,
    )


class FakeStream:
    def __init__(self, requests, responses):
        self.requests = requests
        self.responses = deque(responses)
        self.opening = []
        self.metadata: tuple[tuple[str, str], ...] = ()
        self.cancel = Mock(return_value=True)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.opening:
            self.opening.append(await anext(self.requests))
        if not self.responses:
            raise StopAsyncIteration
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(
        lookup=AsyncMock(return_value=addresses("10.0.0.1")),
        certificate=Mock(return_value=b"public CA certificate"),
        credentials=Mock(return_value=object()),
        channels={},
        outcomes=deque(),
        calls=[],
        stream_responses=[],
        streams=[],
    )
    monkeypatch.setenv("ZOO_SECRETS_KEY", "gateway-test-secret")
    monkeypatch.setenv("JWT_SECRET", "different-jwt-secret")
    monkeypatch.setattr(gateway_client, "root_certificate", state.certificate)
    monkeypatch.setattr(grpc, "ssl_channel_credentials", state.credentials)
    monkeypatch.setattr(
        asyncio.BaseEventLoop, "getaddrinfo", lambda loop, *args, **kwargs: state.lookup(*args, **kwargs)
    )

    def channel(target, credentials, **kwargs):
        async def guest_call(request, **options):
            state.calls.append((target, request, options))
            result = (
                state.outcomes.popleft() if state.outcomes else protocol.GuestCallReply(header_json=b'{"result":{}}')
            )
            if isinstance(result, Exception):
                raise result
            return result

        def stream(requests, **options):
            opened = FakeStream(requests, state.stream_responses)
            opened.metadata = options["metadata"]
            state.streams.append(opened)
            return opened

        stub = SimpleNamespace(
            GuestCall=AsyncMock(side_effect=guest_call),
            GuestStatus=AsyncMock(return_value=protocol.GuestStatusReply(connected=True, version=1)),
            NodeStatus=AsyncMock(return_value=protocol.NodeStatusReply(online=True, report_json=b"{}")),
            GuestStream=Mock(side_effect=stream),
            NodeTunnel=Mock(side_effect=stream),
        )
        created = SimpleNamespace(
            stub=stub, close=AsyncMock(), channel_ready=AsyncMock(), credentials=credentials, options=kwargs
        )
        assert target not in state.channels
        state.channels[target] = created
        return created

    monkeypatch.setattr(grpc.aio, "secure_channel", channel)
    monkeypatch.setattr(gateway_client, "GatewayStub", lambda channel: channel.stub)
    return state


def test_peer_normalization_and_go_rendezvous_contract():
    peers = ["2001:0db8::1", "::ffff:10.0.0.2", "10.0.0.2", "10.0.0.1"]
    assert gateway_client.normalize_peers(peers) == ("10.0.0.1", "10.0.0.2", "2001:db8::1")
    scores = {
        peer: hashlib.sha256((peer + "\x00sandbox").encode()).digest() for peer in gateway_client.normalize_peers(peers)
    }
    assert gateway_client.peer_owner(peers, "sandbox") == max(scores, key=lambda peer: scores[peer])
    assert gateway_client.peer_owner(reversed(peers), "sandbox") == gateway_client.peer_owner(peers, "sandbox")


@pytest.mark.parametrize("peers", [[], ["0.0.0.0"], ["::"], ["224.0.0.1"], ["ff02::1"], ["fe80::1%en0"], ["hostname"]])
def test_invalid_peer_membership_is_rejected(peers):
    with pytest.raises(ValueError):
        gateway_client.normalize_peers(peers)


def test_ca_lookup_reads_only_existing_public_certificate(monkeypatch):
    query = Mock(return_value=[("public certificate",)])
    monkeypatch.setattr(gateway_client.db_manager, "execute", query)
    assert gateway_client.root_certificate() == b"public certificate"
    query.assert_called_once_with("SELECT certificate FROM node_authority WHERE id = 1")
    query.return_value = []
    with pytest.raises(ZooError) as error:
        gateway_client.root_certificate()
    assert error.value.code == "unavailable"


def test_native_replies_tls_auth_channel_reuse_and_shutdown(transport):
    async def scenario():
        client = gateway_client.GatewayClient("gateway.test")
        await client.start()
        try:
            reply = await client.guest_call("sandbox", "exec", {"command": "true"}, b"payload", 3)
            assert isinstance(reply, protocol.GuestCallReply)
            assert (await client.guest_status("sandbox")).connected
            assert (await client.node_status("host")).online
            target, request, options = transport.calls[0]
            assert target == "10.0.0.1:7444"
            assert request.sandbox_id == "sandbox" and request.op == "exec" and request.payload == b"payload"
            assert json.loads(request.header_json) == {"args": {"command": "true"}}
            token = hmac.new(b"gateway-test-secret", b"zoo-gateway", hashlib.sha256).hexdigest()
            assert options["metadata"] == (("authorization", "Bearer " + token),)
            assert 0 < options["timeout"] <= 3
            assert len(transport.channels) == 1
            assert ("grpc.enable_retries", 0) in transport.channels[target].options["options"]
            transport.credentials.assert_called_once_with(root_certificates=b"public CA certificate")
        finally:
            await client.shutdown()
        assert client.monitor is None and not client.channels
        transport.channels["10.0.0.1:7444"].close.assert_awaited_once_with(grace=5)

    asyncio.run(scenario())


def test_jwt_authentication_matches_python_when_secrets_key_unset(transport, monkeypatch):
    monkeypatch.delenv("ZOO_SECRETS_KEY")

    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        try:
            token = hmac.new(b"different-jwt-secret", b"zoo-gateway", hashlib.sha256).hexdigest()
            assert client.metadata == (("authorization", "Bearer " + token),)
        finally:
            await client.shutdown()

    asyncio.run(scenario())


def test_readiness_checks_every_tls_peer_and_authenticated_status(transport):
    async def scenario():
        transport.lookup.return_value = addresses("10.0.0.1", "10.0.0.2")
        client = gateway_client.GatewayClient()
        await client.start()
        try:
            await client.readiness()
            assert set(transport.channels) == {"10.0.0.1:7444", "10.0.0.2:7444"}
            for channel in transport.channels.values():
                channel.channel_ready.assert_awaited_once()
            owner = transport.channels[client.owner("readiness-probe") + ":7444"]
            owner.stub.GuestStatus.assert_awaited_once()
            request = owner.stub.GuestStatus.await_args.args[0]
            assert request.sandbox_id == "readiness-probe"
            assert owner.stub.GuestStatus.await_args.kwargs["metadata"] == client.metadata
        finally:
            await client.shutdown()

    asyncio.run(scenario())


def test_exact_unary_ownership_error_refreshes_and_retries_once(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        transport.lookup.return_value = addresses("10.0.0.2")
        transport.outcomes.extend([rpc_failure(), protocol.GuestCallReply(payload=b"ok")])
        try:
            assert (await client.guest_call("sandbox", "exec")).payload == b"ok"
            assert [call[0] for call in transport.calls] == ["10.0.0.1:7444", "10.0.0.2:7444"]
            assert transport.lookup.await_count == 2
        finally:
            await client.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure,code",
    [
        (rpc_failure(grpc.StatusCode.UNAVAILABLE), "guest_unavailable"),
        (rpc_failure(grpc.StatusCode.DEADLINE_EXCEEDED), "timeout"),
        (rpc_failure(owner=None), "conflict"),
        (rpc_failure(detail="another precondition"), "conflict"),
        (rpc_failure(owner="not-an-ip"), "conflict"),
    ],
)
def test_unary_never_retries_ambiguous_or_unrelated_errors(transport, failure, code):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        transport.outcomes.append(failure)
        try:
            with pytest.raises(ZooError) as error:
                await client.guest_call("sandbox", "exec")
            assert error.value.code == code
            assert len(transport.calls) == 1 and transport.lookup.await_count == 1
        finally:
            await client.shutdown()

    asyncio.run(scenario())


def test_second_ownership_error_is_not_retried(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        transport.outcomes.extend([rpc_failure(), rpc_failure()])
        try:
            with pytest.raises(gateway_client.OwnershipChanged) as error:
                await client.guest_call("sandbox", "exec")
            assert error.value.before_dispatch and len(transport.calls) == 2
        finally:
            await client.shutdown()

    asyncio.run(scenario())


def test_dns_failure_blocks_new_requests_without_closing_existing_channels(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        await client.guest_call("sandbox", "exec")
        channel = transport.channels["10.0.0.1:7444"]
        transport.lookup.side_effect = socket.gaierror("DNS unavailable")
        try:
            with pytest.raises(ZooError):
                await client.refresh()
            with pytest.raises(ZooError):
                await client.guest_call("sandbox", "exec")
            channel.close.assert_not_called()
            assert len(transport.calls) == 1
            transport.lookup.side_effect = None
            await client.refresh()
            await client.guest_call("sandbox", "exec")
            assert len(transport.channels) == 1
        finally:
            await client.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("responded", [False, True])
def test_stream_ownership_contract_retains_only_opening_frame_and_never_retries(transport, responded):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        first = protocol.Frame(header_json=b'{"id":1,"op":"pty_open","args":{"stream":"session"}}')
        consumed = []

        async def frames():
            consumed.append(first)
            yield first
            consumed.append("later")
            yield protocol.Frame(payload=b"later")

        transport.stream_responses = ([protocol.Frame(payload=b"opened")] if responded else []) + [rpc_failure()]
        stream = client.guest_stream("sandbox", frames())
        try:
            if responded:
                assert (await anext(stream)).payload == b"opened"
            with pytest.raises(gateway_client.OwnershipChanged) as error:
                await anext(stream)
            assert error.value.before_dispatch is not responded
            assert error.value.initial_frame.sandbox_id == "sandbox"
            assert error.value.initial_frame.header_json == first.header_json
            assert consumed == [first] and len(transport.streams) == 1
            transport.streams[0].cancel.assert_called_once()
        finally:
            await stream.aclose()
            transport.streams[0].cancel.assert_called_once()
            await client.shutdown()

    asyncio.run(scenario())


def test_node_stream_metadata_and_native_bytes(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()

        async def chunks():
            yield b"SSH-client"

        transport.stream_responses = [protocol.Bytes(data=b"SSH-server")]
        stream = client.node_stream("host", "ssh", chunks())
        try:
            assert await anext(stream) == b"SSH-server"
            assert transport.streams[0].opening[0].data == b"SSH-client"
            assert ("server_id", "host") in transport.streams[0].metadata
            assert ("target", "ssh") in transport.streams[0].metadata
        finally:
            await stream.aclose()
            transport.streams[0].cancel.assert_called_once()
            await client.shutdown()

    asyncio.run(scenario())


def test_sync_bridge_uses_existing_api_loop_and_rejects_blocking_that_loop(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        try:
            with pytest.raises(RuntimeError, match="API event loop"):
                client.sync(client.guest_status("sandbox"))
            reply = await asyncio.to_thread(client.sync, client.guest_status("sandbox"))
            assert reply.connected
        finally:
            await client.shutdown()
        with pytest.raises(ZooError):
            client.sync(client.guest_status("sandbox"))

    asyncio.run(scenario())


def test_missing_ca_prevents_startup_and_creates_no_channels(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        transport.certificate.side_effect = ZooError("unavailable", "gateway CA is missing")
        with pytest.raises(ZooError):
            await client.start()
        assert client.loop is None and client.monitor is None and not client.started
        assert not transport.channels
        transport.lookup.assert_not_called()

    asyncio.run(scenario())


def test_startup_dns_failure_is_closed_and_successful_restart_is_possible(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        transport.lookup.side_effect = socket.gaierror("DNS unavailable")
        with pytest.raises(ZooError):
            await client.start()
        assert not client.started and client.loop is None and not transport.channels
        transport.lookup.side_effect = None
        await client.start()
        try:
            assert client.owner("sandbox") == "10.0.0.1"
        finally:
            await client.shutdown()

    asyncio.run(scenario())


def test_total_deadline_cancels_call_without_retransmission(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        stub = client.stub("sandbox")

        async def blocked(request, **kwargs):
            await asyncio.Future()

        stub.GuestCall.side_effect = blocked
        try:
            with pytest.raises(ZooError) as error:
                await client.guest_call("sandbox", "exec", timeout=0.01)
            assert error.value.code == "timeout" and stub.GuestCall.await_count == 1
        finally:
            await client.shutdown()

    asyncio.run(scenario())


def test_node_unavailable_is_typed_and_not_retried(transport):
    async def scenario():
        client = gateway_client.GatewayClient()
        await client.start()
        stub = client.stub("host")
        stub.NodeStatus.side_effect = rpc_failure(grpc.StatusCode.UNAVAILABLE)
        try:
            with pytest.raises(ZooError) as error:
                await client.node_status("host")
            assert error.value.code == "unavailable" and "zoo-node" in (error.value.hint or "")
            assert stub.NodeStatus.await_count == 1
        finally:
            await client.shutdown()

    asyncio.run(scenario())
