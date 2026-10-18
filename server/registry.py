import base64
import inspect
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from server import a11y, macos, windows
from server import macos_tools as mac
from server import windows_tools as win
from server.errors import ZooError
from server.gateway_client import client
from server.gatewaypb import gateway_pb2 as protocol
from server.guest import hub
from server.images import check, encode
from server.tools import AppTools, FileSystem, ShellTools, WindowTools, run

PERMISSIONS = {
    ("shell", "exec"): "Run shell commands",
    ("screen", "read"): "Take screenshots and list apps",
    ("input", "control"): "Control mouse, keyboard, windows and apps",
    ("files", "read"): "Read files",
    ("files", "write"): "Write, move and delete files",
}


@dataclass
class Tool:
    name: str
    category: str
    fn: Callable[..., Any]
    permission: str
    action: str
    mac: Callable[..., Any]
    win: Callable[..., Any]

    def impl(self, runtime_id: str) -> Callable[..., Any]:
        if macos.is_vm(runtime_id):
            return self.mac
        if windows.is_vm(runtime_id):
            return self.win
        return self.fn

    def call(self, runtime_id: str, **args) -> Any:
        return self.impl(runtime_id)(runtime_id, **args)

    @property
    def params(self) -> list[inspect.Parameter]:
        return [p for n, p in inspect.signature(self.fn).parameters.items() if n != "container_id"]

    def schema(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "permission": f"{self.permission}.{self.action}",
            "params": {
                p.name: {
                    "type": getattr(p.annotation, "__name__", str(p.annotation)),
                    "required": p.default is inspect.Parameter.empty,
                }
                for p in self.params
            },
        }


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
# the last pointer position per sandbox, where a scroll without coordinates happens
pointers: dict[str, tuple[int, int]] = {}


def keysym(name: str) -> int:
    if len(name) == 1:
        code = ord(name)
        return code if code < 0x100 else 0x01000000 + code
    try:
        return KEYSYMS[name.lower()]
    except KeyError:
        raise ValueError(f"unknown key {name}")


def gateway_screen(method: str, container_id: str, timeout: float = 60, **fields) -> protocol.ScreenReply:
    """A call on the sandbox's VNC session in the gateway, the one screen and input path for every OS."""
    sandbox_id = hub.sandbox_of(container_id)
    if sandbox_id is None:
        raise ZooError("not_found", "sandbox not found")
    return client.sync(client.screen(method, protocol.ScreenRequest(sandbox_id=sandbox_id, **fields), timeout))


def pointer(container_id: str, x: int, y: int, mask: int = 0):
    gateway_screen("Pointer", container_id, x=x, y=y, mask=mask)
    pointers[container_id] = (x, y)


def press(container_id: str, button: str, x: int, y: int, count: int):
    if button not in BUTTONS:
        raise ValueError("Invalid button")
    pointer(container_id, x, y)
    for index in range(count):
        if index:
            time.sleep(0.1)
        pointer(container_id, x, y, BUTTONS[button])
        pointer(container_id, x, y)


def combo(container_id: str, names: list[str]):
    syms = [keysym(name) for name in names]
    for sym in syms:
        gateway_screen("Key", container_id, keysym=sym, down=True)
    for sym in reversed(syms):
        gateway_screen("Key", container_id, keysym=sym, down=False)


def click(container_id: str, x: int, y: int, button: str = "left") -> dict:
    press(container_id, button, x, y, 1)
    return {"success": True, "x": x, "y": y, "button": button}


def double_click(container_id: str, x: int, y: int, button: str = "left") -> dict:
    press(container_id, button, x, y, 2)
    return {"success": True, "x": x, "y": y, "button": button}


def move_mouse(container_id: str, x: int, y: int) -> dict:
    pointer(container_id, x, y)
    return {"success": True, "x": x, "y": y}


def scroll(container_id: str, direction: str, amount: int = 3, x: int | None = None, y: int | None = None) -> dict:
    if direction not in SCROLL:
        raise ValueError("Invalid scroll direction")
    if amount < 1:
        raise ValueError("Amount must be >= 1")
    if (x is None) != (y is None):
        raise ValueError("Both x and y must be provided")
    if x is None or y is None:
        x, y = pointers.get(container_id) or (0, 0)
        if container_id not in pointers:
            found = gateway_screen("ScreenShot", container_id)
            x, y = found.width // 2, found.height // 2
    for _ in range(amount):
        pointer(container_id, x, y, SCROLL[direction])
        pointer(container_id, x, y)
        time.sleep(0.08)
    return {"success": True, "direction": direction, "amount": amount}


def drag(
    container_id: str,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    button: str = "left",
    duration: float = 0.5,
) -> dict:
    if button not in BUTTONS:
        raise ValueError("Invalid button")
    steps = max(1, int(duration / 0.02))
    pointer(container_id, start_x, start_y)
    pointer(container_id, start_x, start_y, BUTTONS[button])
    for index in range(1, steps + 1):
        x = start_x + (end_x - start_x) * index // steps
        y = start_y + (end_y - start_y) * index // steps
        pointer(container_id, x, y, BUTTONS[button])
        time.sleep(duration / steps)
    pointer(container_id, end_x, end_y)
    return {"success": True, "start": [start_x, start_y], "end": [end_x, end_y], "button": button}


def type_text(container_id: str, text: str) -> dict:
    gateway_screen("Type", container_id, text=text)
    return {"success": True, "length": len(text)}


def press_key(container_id: str, key: str) -> dict:
    combo(container_id, key.split("+") if len(key) > 1 else [key])
    return {"success": True, "key": key}


def hotkey(container_id: str, keys: list[str]) -> dict:
    if not keys:
        raise ValueError("At least one key is required")
    combo(container_id, keys)
    return {"success": True, "keys": list(keys)}


def screenshot(container_id: str, format: str = "png", scale: float = 1.0, quality: int = 80) -> str:
    check(format, scale)
    # the gateway encodes png and jpeg; webp comes from its png
    found = gateway_screen(
        "ScreenShot", container_id, format="png" if format == "webp" else format, scale=scale, quality=quality
    )
    image = encode(found.image, format, 1.0, quality) if format == "webp" else found.image
    return base64.b64encode(image).decode()


def screen_diff(
    container_id: str, session: str = "default", format: str = "png", scale: float = 1.0, quality: int = 80
) -> dict:
    """What changed on screen since this session's last call: `box` in screen pixels (None if nothing changed),
    `changed` as a fraction of the screen, and `image`, just that box. A session's first call returns everything."""
    check(format, scale)
    found = gateway_screen(
        "ScreenDiff",
        container_id,
        session=session,
        format="png" if format == "webp" else format,
        scale=scale,
        quality=quality,
    )
    tiles = max(1, -(-found.width // 32) * -(-found.height // 32))
    image = found.image
    if image and format == "webp":
        image = encode(image, format, 1.0, quality)
    box = None
    if found.region_width:
        box = {"x": found.x, "y": found.y, "width": found.region_width, "height": found.region_height}
    return {
        "width": found.width,
        "height": found.height,
        "changed": len(found.changed_tiles) / tiles,
        "format": format,
        "box": box,
        "image": base64.b64encode(image).decode() if image else None,
    }


def wait_until_stable(container_id: str, timeout: float = 5.0, quiet_ms: int = 500, threshold: float = 0.0) -> dict:
    """Waits until no more than `threshold` of the screen has changed for `quiet_ms`, or `timeout` seconds pass."""
    if not 0 < timeout <= 60:
        raise ValueError("timeout must be in (0, 60] seconds")
    if quiet_ms <= 0 or not 0 <= threshold < 1:
        raise ValueError("quiet_ms must be positive and threshold in [0, 1)")
    found = gateway_screen(
        "WaitStable",
        container_id,
        timeout + 10,
        timeout_ms=int(timeout * 1000),
        quiet_ms=quiet_ms,
        threshold=threshold,
    )
    return {"stable": found.stable, "waited_ms": found.elapsed_ms}


def has_screen(kind: str) -> bool:
    return kind != "code"


def responsive(container_id: str) -> bool:
    """Whether the sandbox still draws: its VNC session in the gateway gives a frame."""
    try:
        gateway_screen("ScreenShot", container_id, 30)
        return True
    except Exception:
        return False


def start_browser(container_id: str, url: str) -> dict:
    """Legacy desktop images need a launch; the browser-only image supervises Firefox itself."""
    command = (
        "if [ -f /etc/zoo/browser-only ]; then touch /tmp/zoo-browser-start; "
        f"else nohup firefox-esr --new-tab {shlex.quote(url)} >/dev/null 2>&1 & fi"
    )
    result = run(container_id, ["sh", "-c", command], {"DISPLAY": ":1"})
    if result.exit_code != 0:
        raise RuntimeError(result.output.decode(errors="replace"))
    return {"opened": url}


def open_url(container_id: str, url: str, display: str = ":1") -> dict:
    command = f"nohup firefox-esr --new-tab {shlex.quote(url)} >/dev/null 2>&1 &"
    result = run(container_id, ["sh", "-c", command], {"DISPLAY": display})
    if result.exit_code != 0:
        raise RuntimeError(result.output.decode(errors="replace"))
    return {"opened": url}


def fetch_url(container_id: str, url: str, timeout: int = 20) -> dict:
    return ShellTools.execute_command(
        container_id, f"curl -sSL --max-time {int(timeout)} {shlex.quote(url)} | head -c 200000", timeout + 5
    )


KINDS = {
    "desktop": None,
    "browser": {"observe", "mouse", "keyboard", "windows", "browser"},
    "code": {"shell", "files", "web"},
    "macos": None,
    "windows": None,
}

INPUT = ("input", "control")
SCREEN = ("screen", "read")
READ = ("files", "read")
WRITE = ("files", "write")

TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(name, category, fn, *perm, mac_fn, win_fn)
        for name, category, fn, mac_fn, win_fn, perm in [
            ("screenshot", "observe", screenshot, screenshot, screenshot, SCREEN),
            ("screen_diff", "observe", screen_diff, screen_diff, screen_diff, SCREEN),
            ("wait_until_stable", "observe", wait_until_stable, wait_until_stable, wait_until_stable, SCREEN),
            (
                "accessibility_tree",
                "observe",
                a11y.accessibility_tree,
                a11y.accessibility_tree,
                a11y.accessibility_tree,
                SCREEN,
            ),
            ("click", "mouse", click, click, click, INPUT),
            ("move_mouse", "mouse", move_mouse, move_mouse, move_mouse, INPUT),
            ("double_click", "mouse", double_click, double_click, double_click, INPUT),
            ("scroll", "mouse", scroll, scroll, scroll, INPUT),
            ("drag", "mouse", drag, drag, drag, INPUT),
            ("type_text", "keyboard", type_text, type_text, type_text, INPUT),
            ("press_key", "keyboard", press_key, press_key, press_key, INPUT),
            ("hotkey", "keyboard", hotkey, hotkey, hotkey, INPUT),
            (
                "windows_list",
                "windows",
                WindowTools.windows_list,
                mac.MacWindows.windows_list,
                win.WinWindows.windows_list,
                SCREEN,
            ),
            (
                "window_focus",
                "windows",
                WindowTools.window_focus,
                mac.MacWindows.window_focus,
                win.WinWindows.window_focus,
                INPUT,
            ),
            (
                "window_minimize",
                "windows",
                WindowTools.window_minimize,
                mac.MacWindows.window_minimize,
                win.WinWindows.window_minimize,
                INPUT,
            ),
            (
                "window_restore",
                "windows",
                WindowTools.window_restore,
                mac.MacWindows.window_restore,
                win.WinWindows.window_restore,
                INPUT,
            ),
            (
                "window_maximize",
                "windows",
                WindowTools.window_maximize,
                mac.MacWindows.window_maximize,
                win.WinWindows.window_maximize,
                INPUT,
            ),
            (
                "window_unmaximize",
                "windows",
                WindowTools.window_unmaximize,
                mac.MacWindows.window_unmaximize,
                win.WinWindows.window_unmaximize,
                INPUT,
            ),
            (
                "window_close",
                "windows",
                WindowTools.window_close,
                mac.MacWindows.window_close,
                win.WinWindows.window_close,
                INPUT,
            ),
            (
                "installed_apps",
                "apps",
                AppTools.installed_apps,
                mac.MacApps.installed_apps,
                win.WinApps.installed_apps,
                SCREEN,
            ),
            ("open_app", "apps", AppTools.open_app, mac.MacApps.open_app, win.WinApps.open_app, INPUT),
            ("close_app", "apps", AppTools.close_app, mac.MacApps.close_app, win.WinApps.close_app, INPUT),
            ("open_url", "browser", open_url, mac.open_url, win.open_url, INPUT),
            ("fetch_url", "web", fetch_url, mac.fetch_url, win.fetch_url, ("shell", "exec")),
            (
                "execute_command",
                "shell",
                ShellTools.execute_command,
                mac.MacShell.execute_command,
                win.WinShell.execute_command,
                ("shell", "exec"),
            ),
            ("list_files", "files", FileSystem.list_files, mac.MacFiles.list_files, win.WinFiles.list_files, READ),
            (
                "get_file_info",
                "files",
                FileSystem.get_file_info,
                mac.MacFiles.get_file_info,
                win.WinFiles.get_file_info,
                READ,
            ),
            ("read_file", "files", FileSystem.read_file, mac.MacFiles.read_file, win.WinFiles.read_file, READ),
            ("write_file", "files", FileSystem.write_file, mac.MacFiles.write_file, win.WinFiles.write_file, WRITE),
            (
                "create_directory",
                "files",
                FileSystem.create_directory,
                mac.MacFiles.create_directory,
                win.WinFiles.create_directory,
                WRITE,
            ),
            ("delete_file", "files", FileSystem.delete_file, mac.MacFiles.delete_file, win.WinFiles.delete_file, WRITE),
            ("move_file", "files", FileSystem.move_file, mac.MacFiles.move_file, win.WinFiles.move_file, WRITE),
            ("copy_file", "files", FileSystem.copy_file, mac.MacFiles.copy_file, win.WinFiles.copy_file, WRITE),
        ]
    ]
}
