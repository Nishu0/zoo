import websockets
from fastapi import WebSocket, WebSocketDisconnect

from server.vnc import Gate


async def forward_client_to_target(client: WebSocket, target, gate: Gate | None = None):
    """Passes what the viewer sends on, through the gate when there is one (a VNC viewer after its handshake)."""
    try:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                break
            if gate is not None:
                data = gate.feed(message.get("bytes") or (message.get("text") or "").encode())
                if data:
                    await target.send(data)
            elif message.get("bytes") is not None:
                await target.send(message["bytes"])
            elif message.get("text") is not None:
                await target.send(message["text"])
    except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
        pass


async def forward_target_to_client(target, client: WebSocket):
    try:
        async for message in target:
            if isinstance(message, bytes):
                await client.send_bytes(message)
            else:
                await client.send_text(message)
    except websockets.exceptions.ConnectionClosed:
        pass
