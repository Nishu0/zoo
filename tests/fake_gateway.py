"""Native gateway RPC stand-in for Python API and facade tests. No gateway listener is started."""

import asyncio
import json
from types import SimpleNamespace

from server.errors import ZooError
from server.gatewaypb import gateway_pb2 as protocol


class FakeGateway:
    def __init__(self):
        self.loop = None
        self.reset()

    def reset(self):
        self.guests = {}
        self.nodes = {}
        self.streams = {}
        self.screens = []
        self.viewers = []

    async def start(self):
        self.loop = asyncio.get_running_loop()

    async def shutdown(self):
        self.loop = None

    async def readiness(self):
        return None

    def owner(self, identity):
        return "fake-gateway"

    async def refresh(self):
        return None

    def sync(self, coroutine):
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if self.loop is not None and self.loop.is_running():
            if current is self.loop:
                coroutine.close()
                raise RuntimeError("await the gateway operation on the API loop")
            return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()
        return asyncio.run(coroutine)

    async def guest_status(self, identity, timeout=5):
        guest = self.guests.get(identity)
        return protocol.GuestStatusReply(
            connected=guest is not None,
            version=1,
            os="linux",
            services=guest.services if guest else [],
            metrics_json=b'{"cpu_percent":5}' if guest else b"",
            heartbeat=guest is not None,
        )

    async def guest_call(self, identity, op, args=None, payload=b"", timeout=60):
        guest = self.guests.get(identity)
        if guest is None:
            raise ZooError("guest_unavailable", "guest is not connected")
        try:
            result, output = guest.answer(op, args or {}, payload)
            header = {"ok": True, "result": result}
        except Exception as error:
            header, output = {"ok": False, "error": str(error)}, b""
        return protocol.GuestCallReply(header_json=json.dumps(header).encode(), payload=output)

    def deliver(self, identity, header, payload):
        queue = self.streams[(identity, header["stream"])][0]
        queue.put_nowait(protocol.Frame(sandbox_id=identity, header_json=json.dumps(header).encode(), payload=payload))

    async def guest_stream(self, identity, frames):
        first = await anext(frames)
        header = json.loads(first.header_json)
        args = header["args"]
        guest = self.guests[identity]
        incoming = asyncio.Queue()
        key = (identity, args["stream"])
        self.streams[key] = (incoming, asyncio.get_running_loop())

        async def write():
            async for frame in frames:
                request = json.loads(frame.header_json)
                guest.answer(request["op"], request["args"], frame.payload)

        writer = None
        try:
            guest.answer(header["op"], args, first.payload)
            yield protocol.Frame(
                header_json=json.dumps({"id": 1, "ok": True, "result": {"stream": args["stream"]}}).encode()
            )
            writer = asyncio.create_task(write())
            while True:
                yield await incoming.get()
        finally:
            if writer is not None:
                writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
            self.streams.pop(key, None)
            guest.answer("pty_kill" if header["op"] == "pty_open" else "tunnel_close", {"stream": args["stream"]}, b"")

    async def screen(self, method, request, timeout=60):
        self.screens.append((method, request))
        return protocol.ScreenReply(image=b"\x89PNG", width=1280, height=800)

    async def viewer_stream(self, identity, view_only, data):
        self.viewers.append((identity, view_only))
        yield b"RFB 003.008\n"

    async def node_status(self, identity, timeout=5):
        report = self.nodes.get(identity)
        return protocol.NodeStatusReply(online=report is not None, report_json=json.dumps(report or {}).encode())


def fake_guest(identity, answer, services):
    return SimpleNamespace(sandbox_id=identity, answer=answer, services=services)
