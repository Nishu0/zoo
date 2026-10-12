import struct
import time
import zlib
from urllib.parse import urlparse

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

VERSION = b"RFB 003.008\n"
NO_AUTH, VNC_AUTH = 1, 2

BUTTONS = {"left": 1, "middle": 2, "right": 4}
SCROLL = {"up": 8, "down": 16, "left": 32, "right": 64}

KEYSYMS = {
    "return": 0xFF0D,
    "enter": 0xFF0D,
    "tab": 0xFF09,
    "escape": 0xFF1B,
    "esc": 0xFF1B,
    "backspace": 0xFF08,
    "delete": 0xFFFF,
    "space": 0x0020,
    "home": 0xFF50,
    "end": 0xFF57,
    "left": 0xFF51,
    "up": 0xFF52,
    "right": 0xFF53,
    "down": 0xFF54,
    "page_up": 0xFF55,
    "prior": 0xFF55,
    "page_down": 0xFF56,
    "next": 0xFF56,
    "insert": 0xFF63,
    "shift": 0xFFE1,
    "shift_l": 0xFFE1,
    "shift_r": 0xFFE2,
    "ctrl": 0xFFE3,
    "control": 0xFFE3,
    "control_l": 0xFFE3,
    "control_r": 0xFFE4,
    "alt": 0xFFE9,
    "alt_l": 0xFFE9,
    "alt_r": 0xFFEA,
    "option": 0xFFE9,
    "cmd": 0xFFEB,
    "command": 0xFFEB,
    "super": 0xFFEB,
    "super_l": 0xFFEB,
    "super_r": 0xFFEC,
    "meta": 0xFFEB,
    "win": 0xFFEB,
    "caps_lock": 0xFFE5,
    **{f"f{i}": 0xFFBD + i for i in range(1, 13)},
}


def keysym(name: str) -> int:
    if len(name) == 1:
        return char_keysym(name)
    try:
        return KEYSYMS[name.lower()]
    except KeyError:
        raise ValueError(f"unknown key {name}")


def char_keysym(char: str) -> int:
    if char == "\n":
        return KEYSYMS["return"]
    if char == "\t":
        return KEYSYMS["tab"]
    code = ord(char)
    return code if code < 0x100 else 0x01000000 + code


def vnc_response(password: str, challenge: bytes) -> bytes:
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in password.encode()[:8].ljust(8, b"\0"))
    encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return encryptor.update(challenge) + encryptor.finalize()


def password_of(url: str) -> str:
    return urlparse(url).password or ""


def png(width: int, height: int, rgb: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    stride = width * 3
    raw = b"".join(b"\0" + rgb[y * stride : (y + 1) * stride] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


class Channel:
    """Blocking reads and writes over a socket-like object (a paramiko channel)."""

    def __init__(self, sock):
        self.sock = sock

    def read(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("vnc connection closed")
            data += chunk
        return data

    def write(self, data: bytes):
        self.sock.sendall(data)


def authenticate(conn: Channel, password: str):
    """Runs the RFB 3.8 version and security handshake against the server."""
    if not conn.read(12).startswith(b"RFB "):
        raise ConnectionError("not a vnc server")
    conn.write(VERSION)
    types = conn.read(conn.read(1)[0])
    if not types:
        raise ConnectionError(conn.read(struct.unpack(">I", conn.read(4))[0]).decode(errors="replace"))
    if VNC_AUTH in types:
        conn.write(bytes([VNC_AUTH]))
        conn.write(vnc_response(password, conn.read(16)))
    elif NO_AUTH in types:
        conn.write(bytes([NO_AUTH]))
    else:
        raise ConnectionError(f"unsupported vnc security types {list(types)}")
    if struct.unpack(">I", conn.read(4))[0] != 0:
        raise ConnectionError("vnc authentication failed")


class Buffered:
    """Exact-length async reads over a message stream (a websocket) whose messages split the RFB bytes anywhere."""

    def __init__(self, receive):
        self.receive = receive
        self.data = b""

    async def read(self, n: int) -> bytes:
        while len(self.data) < n:
            self.data += await self.receive()
        data, self.data = self.data[:n], self.data[n:]
        return data


async def authenticate_async(conn: Buffered, write, password: str):
    """authenticate() over a websocket, as a client of the VNC server behind it."""
    if not (await conn.read(12)).startswith(b"RFB "):
        raise ConnectionError("not a vnc server")
    await write(VERSION)
    types = await conn.read((await conn.read(1))[0])
    if not types:
        raise ConnectionError((await conn.read(struct.unpack(">I", await conn.read(4))[0])).decode(errors="replace"))
    if VNC_AUTH in types:
        await write(bytes([VNC_AUTH]))
        await write(vnc_response(password, await conn.read(16)))
    elif NO_AUTH in types:
        await write(bytes([NO_AUTH]))
    else:
        raise ConnectionError(f"unsupported vnc security types {list(types)}")
    if struct.unpack(">I", await conn.read(4))[0] != 0:
        raise ConnectionError("vnc authentication failed")


RAW = 0
DESKTOP_SIZE = -223
EXTENDED_DESKTOP_SIZE = -308


class VNC:
    def __init__(self, sock, password: str):
        self.conn = Channel(sock)
        authenticate(self.conn, password)
        self.conn.write(b"\x01")
        self.width, self.height = struct.unpack(">HH", self.conn.read(4))
        self.conn.read(16)
        self.conn.read(struct.unpack(">I", self.conn.read(4))[0])
        pixel_format = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        self.conn.write(b"\x00\x00\x00\x00" + pixel_format)
        # Apple's Virtualization VNC server traps and kills the VM if the client doesn't advertise these.
        encodings = [RAW, DESKTOP_SIZE, EXTENDED_DESKTOP_SIZE]
        self.conn.write(struct.pack(f">BxH{len(encodings)}i", 2, len(encodings), *encodings))
        self.x = self.y = 0

    def screenshot(self) -> bytes:
        frame = bytearray(self.width * self.height * 3)
        self.conn.write(struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height))
        while True:
            kind = self.conn.read(1)[0]
            if kind == 0:
                break
            if kind == 1:
                self.conn.read(3)
                self.conn.read(struct.unpack(">H", self.conn.read(2))[0] * 6)
            elif kind == 3:
                self.conn.read(3)
                self.conn.read(struct.unpack(">I", self.conn.read(4))[0])
            elif kind != 2:
                raise ConnectionError(f"unexpected vnc message {kind}")
        self.conn.read(1)
        resized = False
        for _ in range(struct.unpack(">H", self.conn.read(2))[0]):
            x, y, w, h, encoding = struct.unpack(">HHHHi", self.conn.read(12))
            if encoding in (DESKTOP_SIZE, EXTENDED_DESKTOP_SIZE):
                if encoding == EXTENDED_DESKTOP_SIZE:
                    screens = self.conn.read(4)[0]
                    self.conn.read(16 * screens)
                if (w, h) != (self.width, self.height):
                    self.width, self.height, resized = w, h, True
                continue
            if encoding != RAW:
                raise ConnectionError(f"unexpected vnc encoding {encoding}")
            if resized or x + w > self.width or y + h > self.height:
                self.conn.read(w * h * 4)
                continue
            pixels = self.conn.read(w * h * 4)
            for row in range(h):
                src = pixels[row * w * 4 : (row + 1) * w * 4]
                start = ((y + row) * self.width + x) * 3
                line = frame[start : start + w * 3]
                line[0::3], line[1::3], line[2::3] = src[2::4], src[1::4], src[0::4]
                frame[start : start + w * 3] = line
        if resized:
            return self.screenshot()
        return png(self.width, self.height, bytes(frame))

    def pointer(self, x: int, y: int, mask: int = 0):
        self.x, self.y = x, y
        self.conn.write(struct.pack(">BBHH", 5, mask, x, y))

    def click(self, x: int, y: int, button: str = "left", count: int = 1):
        self.pointer(x, y)
        for i in range(count):
            if i:
                time.sleep(0.1)
            self.pointer(x, y, BUTTONS[button])
            self.pointer(x, y)

    def scroll(self, direction: str, amount: int):
        for _ in range(amount):
            self.pointer(self.x, self.y, SCROLL[direction])
            self.pointer(self.x, self.y)
            time.sleep(0.08)

    def drag(self, start: tuple[int, int], end: tuple[int, int], button: str, duration: float):
        steps = max(1, int(duration / 0.02))
        self.pointer(*start)
        self.pointer(*start, BUTTONS[button])
        for i in range(1, steps + 1):
            x = start[0] + (end[0] - start[0]) * i // steps
            y = start[1] + (end[1] - start[1]) * i // steps
            self.pointer(x, y, BUTTONS[button])
            time.sleep(duration / steps)
        self.pointer(*end)

    def key(self, sym: int, down: bool):
        self.conn.write(struct.pack(">BBxxI", 4, int(down), sym))

    def combo(self, names: list[str]):
        syms = [keysym(n) for n in names]
        for sym in syms:
            self.key(sym, True)
        for sym in reversed(syms):
            self.key(sym, False)

    def type(self, text: str, delay: float):
        for char in text:
            sym = char_keysym(char)
            self.key(sym, True)
            self.key(sym, False)
            time.sleep(delay)


# what a viewer sends that changes the screen: keys, pointer, clipboard, xvp (shutdown/reboot), desktop resize, GII
# input devices and QEMU's extended key event
INPUT_MESSAGES = {4, 5, 6, 250, 251, 253, 255}


def client_message_size(data: bytes) -> int | None:
    """The length of the RFB client-to-server message at the start of data, None until enough of it is there to
    tell. Raises ValueError for a message this proxy doesn't know, so a gated viewer can't slip input past it."""
    kind = data[0]
    fixed = {0: 20, 3: 10, 4: 8, 5: 6, 150: 10, 250: 4}
    if kind in fixed:
        return fixed[kind]
    if kind == 2:  # SetEncodings: a count of 4-byte encodings
        return None if len(data) < 4 else 4 + 4 * struct.unpack(">H", data[2:4])[0]
    if kind == 6:  # ClientCutText; a negative length is the extended clipboard's
        return None if len(data) < 8 else 8 + abs(struct.unpack(">i", data[4:8])[0])
    if kind == 248:  # ClientFence: a payload of up to 64 bytes
        return None if len(data) < 9 else 9 + data[8]
    if kind == 251:  # SetDesktopSize: 16 bytes per screen
        return None if len(data) < 8 else 8 + 16 * data[6]
    if kind == 253:  # GII: a length in the byte order its second byte's top bit names
        return None if len(data) < 4 else 4 + int.from_bytes(data[2:4], "big" if data[1] & 0x80 else "little")
    if kind == 255:  # QEMU: only the extended key event is used by noVNC
        if len(data) < 2:
            return None
        if data[1] == 0:
            return 12
        raise ValueError(f"unknown QEMU client message {data[1]}")
    raise ValueError(f"unknown RFB client message {kind}")


class Gate:
    """Sits between a viewer and the VNC server, after the security handshake, and reads what the viewer sends as
    whole RFB messages. A view-only gate drops input, and ends the connection (ValueError) at a message it can't
    read. Any other gate passes messages on; at a message it can't read it stops reading and passes everything on."""

    def __init__(self, view_only: bool):
        self.view_only = view_only
        self.buffer = b""
        # the viewer's first byte is its ClientInit
        self.initialised = False
        self.passthrough = False

    def feed(self, data: bytes) -> bytes:
        """What to pass on to the VNC server of what the viewer sent."""
        out = b""
        if self.passthrough:
            return data
        if not self.initialised and data:
            out, data, self.initialised = data[:1], data[1:], True
        self.buffer += data
        while self.buffer:
            try:
                size = client_message_size(self.buffer)
            except ValueError:
                if self.view_only:
                    raise
                out, self.buffer, self.passthrough = out + self.buffer, b"", True
                break
            if size is None or len(self.buffer) < size:
                break
            message, self.buffer = self.buffer[:size], self.buffer[size:]
            kind = message[0]
            if self.view_only and kind in INPUT_MESSAGES:
                continue
            out += message
        return out
