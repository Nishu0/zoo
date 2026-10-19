"""End to end against a real stack: real containers, real tools, real network policy.

    ZOO_RUNTIME=docker docker compose up -d --build
    scripts/e2e.sh        # signs up, makes an API key and runs this suite

Linux sandboxes require Kata Containers and a host with KVM.
ZOO_E2E_KINDS picks the sandbox kinds (default desktop,browser,code) and ZOO_E2E_SERVER_ID the server for macos and
windows ones, so the nightly Mac and Hyper-V runners run the same suite.
"""

import asyncio
import json
import os
import shutil
import subprocess
import time

import httpx
import pytest
import websockets
from zoo_sdk import Zoo, ZooError

from apps.server.registry import KINDS, TOOLS
from tests.tool_args import SAMPLE_ARGS

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not os.environ.get("ZOO_E2E_URL"), reason="set ZOO_E2E_URL and ZOO_E2E_API_KEY to run"),
]
WINDOW_TOOLS = [
    "window_focus",
    "window_minimize",
    "window_restore",
    "window_maximize",
    "window_unmaximize",
    "window_close",
]
BOOT_TIMEOUT = int(os.environ.get("ZOO_E2E_BOOT_TIMEOUT", "240"))
KINDS_UNDER_TEST = os.environ.get("ZOO_E2E_KINDS", "desktop,browser,code").split(",")
SCREENS = [k for k in KINDS_UNDER_TEST if k != "code"]
needs_code = pytest.mark.skipif("code" not in KINDS_UNDER_TEST, reason="code sandboxes aren't under test")


@pytest.fixture(scope="module")
def zoo() -> Zoo:
    return Zoo(api_key=os.environ["ZOO_E2E_API_KEY"], base_url=os.environ["ZOO_E2E_URL"])


@pytest.fixture
def make(zoo):
    created = []

    def make(kind: str):
        server_id = os.environ.get("ZOO_E2E_SERVER_ID") if kind in ("macos", "windows") else None
        sandbox = zoo.create(name=f"e2e-{kind}", kind=kind, server_id=server_id, timeout=BOOT_TIMEOUT)
        created.append(sandbox)
        return sandbox

    yield make
    for sandbox in created:
        try:
            sandbox.delete()
        except ZooError:
            pass


def a_window(sandbox, kind: str) -> str:
    """A window the window tools can act on: a fresh terminal where apps are allowed, else Firefox."""
    if kind != "browser":
        sandbox.tool("open_app", **SAMPLE_ARGS["open_app"])
    # a new window gets its title a moment after it maps
    deadline = time.monotonic() + 15
    while True:
        windows = sandbox.tool("windows_list")
        match = [w for w in windows if ("Terminal" if kind != "browser" else "Firefox") in w["title"]]
        if match or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    assert match, f"no window to act on in {windows}"
    return match[0]["id"]


@pytest.mark.parametrize("kind", KINDS_UNDER_TEST)
def test_every_tool_runs(make, kind):
    sandbox = make(kind)
    allowed = KINDS[kind]
    names = [n for n in SAMPLE_ARGS if n not in WINDOW_TOOLS and (allowed is None or TOOLS[n].category in allowed)]
    if "open_app" in names:
        names.remove("close_app")
        names.append("close_app")
    for name in names:
        if name == "close_app":
            sandbox.tool("open_app", **SAMPLE_ARGS["open_app"])
        result = sandbox.tool(name, **SAMPLE_ARGS[name])
        if name == "read_file":
            assert "zoo" in str(result)
        if name == "execute_command":
            assert result["exit_code"] == 0 and result["stdout"].strip() == "zoo"
    if allowed is None or "windows" in allowed:
        window_id = a_window(sandbox, kind)
        for name in WINDOW_TOOLS:
            sandbox.tool(name, window_id=window_id)


@pytest.mark.parametrize("kind", KINDS_UNDER_TEST)
def test_tools_outside_the_kind_are_refused(make, kind):
    allowed = KINDS[kind]
    if allowed is None:
        pytest.skip("desktop sandboxes allow every tool")
    sandbox = make(kind)
    refused = next(n for n, t in TOOLS.items() if t.category not in allowed)
    with pytest.raises(ZooError, match="400"):
        sandbox.tool(refused, **SAMPLE_ARGS[refused])


@needs_code
def test_deny_network_policy_blocks_traffic(make):
    sandbox = make("code")
    probe = "curl -sS -o /dev/null --max-time 8 -w '%{http_code}' https://example.com"
    assert sandbox.exec(probe, timeout=20)["exit_code"] == 0

    sandbox.set_network("deny", allow_dns=True)
    assert sandbox.exec(probe, timeout=20)["exit_code"] != 0

    sandbox.add_rule("domain", "example.com", "allow")
    assert sandbox.exec(probe, timeout=20)["exit_code"] == 0


@needs_code
def test_lifecycle(zoo, make):
    sandbox = make("code")
    sandbox.exec("echo kept > ~/kept.txt")
    sandbox.stop()
    assert sandbox.refresh().status == "stopped"
    sandbox.start()
    assert sandbox.exec("cat ~/kept.txt")["stdout"].strip() == "kept"
    sandbox.delete()
    assert sandbox.id not in [s.id for s in zoo.sandboxes()]


@pytest.mark.parametrize("kind", SCREENS)
def test_viewer_gets_the_screen_without_a_password(make, kind):
    """The API logs in to the sandbox's VNC server itself and offers the browser no auth, behind a one-time ticket."""
    sandbox = make(kind)
    url = os.environ["ZOO_E2E_URL"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['ZOO_E2E_API_KEY']}"}
    ticket = httpx.post(f"{url}/sandboxes/{sandbox.id}/vnc-ticket", headers=headers).json()["ticket"]
    socket = f"{url.replace('http', 'ws', 1)}/sandboxes/{sandbox.id}/ws?ticket={ticket}"

    async def handshake() -> bytes:
        async with websockets.connect(socket, max_size=None) as ws:
            buffer = b""

            async def read(n: int) -> bytes:
                nonlocal buffer
                while len(buffer) < n:
                    message = await ws.recv()
                    buffer += message if isinstance(message, bytes) else message.encode()
                data, buffer = buffer[:n], buffer[n:]
                return data

            assert await read(12) == b"RFB 003.008\n"
            await ws.send(b"RFB 003.008\n")
            assert await read(2) == bytes([1, 1])  # one security type: none
            await ws.send(bytes([1]))
            assert await read(4) == b"\0\0\0\0"
            await ws.send(b"\x01")  # ClientInit, shared
            return await read(4)  # ServerInit starts with the framebuffer width and height

    size = asyncio.run(asyncio.wait_for(handshake(), 30))
    assert int.from_bytes(size[:2], "big") > 0 and int.from_bytes(size[2:], "big") > 0

    async def reuse():
        async with websockets.connect(socket):
            pass

    # the ticket is spent: the API refuses the websocket before accepting it
    with pytest.raises(websockets.exceptions.InvalidStatus):
        asyncio.run(reuse())


def api(path: str, method: str = "GET") -> httpx.Response:
    url = os.environ["ZOO_E2E_URL"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['ZOO_E2E_API_KEY']}"}
    return httpx.request(method, f"{url}{path}", headers=headers, timeout=30)


def wait_for_guest(sandbox, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status = api(f"/sandboxes/{sandbox.id}/guest").json()
        if status.get("connected") or time.monotonic() > deadline:
            return status
        time.sleep(0.5)


@pytest.mark.parametrize("kind", KINDS_UNDER_TEST)
def test_guest_agent_connects(make, kind):
    if kind in ("macos", "windows"):
        pytest.skip("the guest agent is Linux-only for now")
    status = wait_for_guest(make(kind))
    assert status["connected"], status
    assert {"exec", "pty", "files", "metrics"} <= set(status["services"])
    if kind != "code":
        assert {"screen", "input"} <= set(status["services"])


@pytest.mark.skipif("desktop" not in KINDS_UNDER_TEST, reason="desktop sandboxes aren't under test")
def test_guest_input_reaches_the_desktop(make):
    """Keys and clicks go through XTest in the guest; the shell checks what they did."""
    sandbox = make("desktop")
    assert wait_for_guest(sandbox)["connected"]
    sandbox.tool("click", x=37, y=41)
    assert sandbox.exec("xdotool getmouselocation")["stdout"].startswith("x:37 y:41 ")
    window_id = a_window(sandbox, "desktop")
    sandbox.tool("window_focus", window_id=window_id)
    sandbox.tool("type_text", text="echo typed-$((6*7)) é > ~/typed.txt")
    sandbox.tool("press_key", key="Return")
    out = ""
    for _ in range(20):
        out = sandbox.exec("cat ~/typed.txt 2>/dev/null")["stdout"].strip()
        if out:
            break
        time.sleep(0.5)
    assert out == "typed-42 é"
    with pytest.raises(ZooError, match="unknown key"):
        sandbox.tool("press_key", key="NoSuchKey")


@pytest.mark.parametrize("kind", [k for k in KINDS_UNDER_TEST if k in ("desktop", "code")])
def test_terminal(make, kind):
    sandbox = make(kind)
    assert wait_for_guest(sandbox)["connected"]
    ticket = api(f"/sandboxes/{sandbox.id}/terminal-ticket", "POST").json()["ticket"]
    url = os.environ["ZOO_E2E_URL"].rstrip("/").replace("http", "ws", 1)
    socket = f"{url}/sandboxes/{sandbox.id}/terminal?ticket={ticket}&cols=90&rows=20"

    async def session() -> tuple[str, dict]:
        output = ""
        async with websockets.connect(socket, max_size=None) as ws:
            await ws.send(b"stty size; whoami; echo $((6*7))\n")
            while "\n42" not in output.replace("\r", ""):
                message = await ws.recv()
                assert isinstance(message, bytes)
                output += message.decode(errors="replace")
            await ws.send('{"type": "resize", "cols": 101, "rows": 33}')
            await ws.send(b"stty size; exit 5\n")
            while True:
                message = await ws.recv()
                if isinstance(message, str):
                    return output, json.loads(message)
                output += message.decode(errors="replace")

    output, end = asyncio.run(asyncio.wait_for(session(), 30))
    assert "20 90" in output and "zoo" in output and "33 101" in output
    assert end == {"type": "exit", "code": 5}


@pytest.mark.skipif("desktop" not in KINDS_UNDER_TEST, reason="desktop sandboxes aren't under test")
def test_screen_diff_and_wait_until_stable(make):
    """The guest diffs frames per session and waits for the screen to settle without round-trips."""
    sandbox = make("desktop")
    status = wait_for_guest(sandbox)
    assert {"diff", "tunnel"} <= set(status["services"]), status
    settled = sandbox.tool("wait_until_stable", timeout=10.0, quiet_ms=500)
    assert settled["stable"], settled
    first = sandbox.tool("screen_diff", session="e2e")
    assert first["changed"] == 1 and first["box"]["x"] == 0 and first["image"]
    assert first["box"]["width"] == first["width"]
    again = sandbox.tool("screen_diff", session="e2e", format="webp", scale=0.5)
    assert again["changed"] < 0.05, again["box"]
    sandbox.tool("click", x=37, y=41)
    sandbox.tool("open_app", command="xfce4-terminal")
    assert not sandbox.tool("wait_until_stable", timeout=0.3, quiet_ms=2000)["stable"]
    assert sandbox.tool("wait_until_stable", timeout=20.0, quiet_ms=700)["stable"]
    after = sandbox.tool("screen_diff", session="e2e", format="jpeg")
    assert after["changed"] > 0 and after["box"] and after["image"] and after["format"] == "jpeg"
    with pytest.raises(ZooError):
        sandbox.tool("wait_until_stable", timeout=120.0)


@pytest.mark.skipif("desktop" not in KINDS_UNDER_TEST, reason="desktop sandboxes aren't under test")
def test_desktop_publishes_no_vnc_port(make):
    """Desktops on images that carry the tunnel label are viewed through the guest, so 6080 isn't published."""
    if not shutil.which("docker"):
        pytest.skip("needs the docker cli on the sandbox's host")
    sandbox = make("desktop")
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{json .HostConfig.PortBindings}}", f"zoo-sandbox-{sandbox.id}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        pytest.skip("the sandbox runs on another docker host")
    assert "6080" not in out.stdout, out.stdout
