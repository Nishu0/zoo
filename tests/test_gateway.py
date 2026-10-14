"""Python facades use the native gateway protocol, without Python guest or node listeners."""

import asyncio
import json

import pytest

from server import guest, nodes
from server.errors import ZooError
from server.guest import Terminal, Tunnel
from tests.conftest import GATEWAY
from tests.fake_gateway import fake_guest


def test_guest_facades_use_native_calls_and_streams():
    calls = []

    def answer(op, args, payload):
        calls.append((op, args, payload))
        if op == "exec":
            return {"exit_code": 0, "stdout_len": 2}, b"ok"
        if op in ("pty_input", "tunnel_write"):
            GATEWAY.deliver(
                "sandbox",
                {"op": "pty_data" if op == "pty_input" else "tunnel_data", "stream": args["stream"]},
                payload.upper(),
            )
        return {}, b""

    GATEWAY.guests["sandbox"] = fake_guest("sandbox", answer, ["exec", "metrics", "pty", "tunnel"])
    found = guest.hub.for_sandbox("sandbox")
    assert found is not None and found.has("exec")
    assert found.run(["true"]) == (0, b"ok", b"")
    assert guest.hub.for_sandbox("sandbox") is found
    assert guest.hub.heartbeat("sandbox")

    async def scenario():
        terminal = Terminal(found)
        await terminal.open(80, 24)
        await terminal.write(b"hello")
        assert await terminal.events.get() == ("data", b"HELLO")
        await terminal.close()
        assert await terminal.events.get() == ("exit", None)
        tunnel = Tunnel(found)
        await tunnel.open(5900)
        await tunnel.write(b"hello")
        assert await tunnel.read() == b"HELLO"
        await tunnel.close()
        assert await tunnel.read() == b""
        assert not found.streams and not GATEWAY.streams

    asyncio.run(scenario())
    assert [call[0] for call in calls] == [
        "exec",
        "pty_open",
        "pty_input",
        "pty_kill",
        "tunnel_open",
        "tunnel_write",
        "tunnel_close",
    ]


def test_node_ssh_socket_is_pumped_on_the_persistent_loop(monkeypatch):
    GATEWAY.nodes["host"] = {"targets": ["ssh"]}
    opened = []

    async def stream(server_id, target, source):
        opened.append((server_id, target))
        yield b"SSH-2.0-test\r\n"
        async for chunk in source:
            yield chunk.upper()

    monkeypatch.setattr(GATEWAY, "node_stream", stream, raising=False)

    async def scenario():
        await GATEWAY.start()
        sock = None
        try:
            sock = await nodes.hub.open_async("host", "ssh")
            assert await asyncio.to_thread(sock.recv, 100) == b"SSH-2.0-test\r\n"
            await asyncio.to_thread(sock.sendall, b"hello")
            assert await asyncio.to_thread(sock.recv, 100) == b"HELLO"
            assert opened == [("host", "ssh")]
        finally:
            if sock is not None:
                sock.close()
            await nodes.hub.shutdown()
            await GATEWAY.shutdown()
        assert not nodes.hub.tunnels

    asyncio.run(scenario())


def test_remote_urls_are_derived_with_prefix_and_ipv6(monkeypatch):
    monkeypatch.setenv("ZOO_API_URL", "https://[2001:db8::1]:8443/prefix/")
    assert guest.guest_env("sandbox", remote=True)["ZOO_GUEST_URL"] == "wss://[2001:db8::1]:8443/prefix/guest/connect"
    assert guest.guest_endpoint() == ("2001:db8::1", 8443)
    monkeypatch.delenv("ZOO_API_URL")
    with pytest.raises(ZooError):
        guest.guest_endpoint()


def test_reply_errors_are_preserved():
    with pytest.raises(guest.GuestError, match="permission denied"):
        guest.reply(json.dumps({"ok": False, "error": "permission denied"}).encode(), b"")
