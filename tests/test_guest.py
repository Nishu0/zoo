import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from apps.server import guest
from apps.server.guest import hub, pack, token, unpack
from apps.server.monitor import guest_usage
from apps.server.tools import FileSystem
from tests.conftest import GATEWAY, runtime_of
from tests.fake_gateway import fake_guest

SERVICES = ["exec", "pty", "files", "screen", "input", "metrics"]


def png(width: int = 8, height: int = 6) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(out, format="PNG")
    return out.getvalue()


class FakeGuest:
    def __init__(self, sandbox_id: str, reply, services: list[str] = SERVICES):
        self.sandbox_id = sandbox_id
        self.services = services
        self.reply = reply
        self.calls = []
        self.ws = SimpleNamespace(send_bytes=self.deliver)

    def answer(self, op, args, payload):
        self.calls.append((op, args, payload))
        return self.reply(op, args, payload)

    def deliver(self, frame):
        header, payload = unpack(frame)
        GATEWAY.deliver(self.sandbox_id, header, payload)

    def __enter__(self):
        hub.statuses.pop(self.sandbox_id, None)
        GATEWAY.guests[self.sandbox_id] = fake_guest(self.sandbox_id, self.answer, self.services)
        return self

    def __exit__(self, *exc):
        hub.drop(self.sandbox_id)
        GATEWAY.guests.pop(self.sandbox_id, None)


def test_frames_round_trip():
    assert unpack(pack({"id": 1, "op": "exec"}, b"\x00data")) == ({"id": 1, "op": "exec"}, b"\x00data")
    with pytest.raises(ValueError):
        unpack(b"\x00\x00\x00\x09{}")


def test_tokens_are_per_sandbox(monkeypatch):
    assert token("a") == token("a") != token("b")
    monkeypatch.setenv("ZOO_RUNTIME", "docker")
    monkeypatch.setenv("ZOO_API_URL", "https://api.example.test/prefix")
    assert guest.guest_env("a", remote=False)["ZOO_GUEST_TOKEN"] == token("a")
    assert guest.guest_env("a", remote=True)["ZOO_GUEST_URL"] == "wss://api.example.test/prefix/guest/connect"
    assert guest.guest_endpoint() == ("api.example.test", 443)


def test_python_hub_has_no_guest_listener():
    assert not hasattr(hub, "serve")


def test_guest_errors_surface_and_disconnects_are_reported():
    def reply(op, args, payload):
        raise RuntimeError("permission denied")

    hub.bind("rt-err", "sb-err")
    with FakeGuest("sb-err", reply), pytest.raises(guest.GuestError, match="permission denied"):
        FileSystem.write_file("rt-err", "/etc/passwd", "x")
    assert hub.for_runtime("rt-err") is None
    assert hub.status("sb-err") == {"connected": False}


def test_terminal_streams_through_the_guest(client, alice, sandbox):
    sid = sandbox["id"]
    assert client.post(f"/sandboxes/{sid}/terminal-ticket", headers=alice).status_code == 409

    def reply(op, args, payload):
        if op == "pty_open":
            return {"stream": args["stream"]}, b""
        if op == "pty_input":
            fake.ws.send_bytes(pack({"op": "pty_data", "stream": args["stream"]}, payload.upper()))
            if payload == b"exit\n":
                fake.ws.send_bytes(pack({"op": "pty_exit", "stream": args["stream"], "exit_code": 3}))
        return None

    hub.bind(runtime_of(sid), sid)
    with FakeGuest(sid, reply) as fake:
        ticket = client.post(f"/sandboxes/{sid}/terminal-ticket", headers=alice).json()["ticket"]
        url = f"/sandboxes/{sid}/terminal?ticket={ticket}&cols=100&rows=30"
        with client.websocket_connect(url) as term:
            term.send_bytes(b"ls\n")
            assert term.receive_bytes() == b"LS\n"
            term.send_text(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
            term.send_bytes(b"exit\n")
            assert term.receive_bytes() == b"EXIT\n"
            assert json.loads(term.receive_text()) == {"type": "exit", "code": 3}
        # a ticket opens one terminal
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(url) as again:
            again.receive_bytes()
    ops = [(op, {k: v for k, v in args.items() if k != "stream"}) for op, args, _ in fake.calls]
    assert ops[:4] == [
        ("pty_open", {"argv": [], "cols": 100, "rows": 30}),
        ("pty_input", {}),
        ("pty_resize", {"cols": 120, "rows": 40}),
        ("pty_input", {}),
    ]


def test_monitoring_falls_back_to_guest_metrics():
    hub.bind("rt-metrics", "sb-metrics")
    assert guest_usage("sb-metrics") is None
    with FakeGuest("sb-metrics", lambda op, args, payload: ({}, b"")):
        assert guest_usage("sb-metrics") == {
            "cpu_percent": 5,
            "memory_usage": 0,
            "memory_limit": 0,
            "memory_percent": 0.0,
            "network_rx": 0,
            "network_tx": 0,
            "pids": 0,
            "status": "running",
        }
