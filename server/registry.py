import base64
import inspect
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from server import a11y, macos, screen, windows
from server import macos_tools as mac
from server import vnc_tools as vnc
from server import windows_tools as win
from server.guest import hub
from server.images import check, encode
from server.tools import (
    AppTools,
    FileSystem,
    KeyboardTools,
    MoseTools,
    ObserveTools,
    ShellTools,
    WindowTools,
    run_x,
)

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


def hotkey(container_id: str, keys: list[str], display: str = ":1") -> dict:
    return KeyboardTools.hotkey(container_id, *keys, display=display)


def screenshot(
    container_id: str, display: str = ":1", format: str = "png", scale: float = 1.0, quality: int = 80
) -> str:
    check(format, scale)
    guest = hub.for_runtime(container_id)
    if guest is not None and guest.has("screen"):
        # the guest encodes png and jpeg itself; webp comes from its png
        image = guest.screenshot(display, "png" if format == "webp" else format, scale, quality)
        if format == "webp":
            image = encode(image, format, 1.0, quality)
    else:
        image = encode(ObserveTools.screenshot(container_id, display), format, scale, quality)
    return base64.b64encode(image).decode()


def screen_diff(
    container_id: str,
    display: str = ":1",
    session: str = "default",
    format: str = "png",
    scale: float = 1.0,
    quality: int = 80,
) -> dict:
    """What changed on screen since this session's last call: `box` in screen pixels (None if nothing changed),
    `changed` as a fraction of the screen, and `image`, just that box. A session's first call returns everything."""
    check(format, scale)
    guest = hub.for_runtime(container_id)
    if guest is not None and guest.has("diff"):
        result, image = guest.screen_diff(display, session, "png" if format == "webp" else format, scale, quality)
        if image and format == "webp":
            image = encode(image, format, 1.0, quality)
        result["format"] = format
    else:
        png = ObserveTools.screenshot(container_id, display)
        result, image = screen.diff((container_id, display, session), png, format, scale, quality)
    return {**result, "image": base64.b64encode(image).decode() if image else None}


def wait_until_stable(
    container_id: str, display: str = ":1", timeout: float = 5.0, quiet_ms: int = 500, threshold: float = 0.0
) -> dict:
    """Waits until no more than `threshold` of the screen has changed for `quiet_ms`, or `timeout` seconds pass."""
    screen.check_wait(timeout, quiet_ms, threshold)
    guest = hub.for_runtime(container_id)
    if guest is not None and guest.has("diff"):
        return guest.wait_until_stable(display, timeout, quiet_ms, threshold)
    return screen.wait_until_stable(
        lambda: ObserveTools.screenshot(container_id, display), timeout, quiet_ms, threshold
    )


def open_url(container_id: str, url: str, display: str = ":1") -> dict:
    run_x(container_id, ["sh", "-c", f"nohup firefox-esr --new-tab {shlex.quote(url)} >/dev/null 2>&1 &"], display)
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
            ("screenshot", "observe", screenshot, vnc.screenshot, vnc.screenshot, SCREEN),
            ("screen_diff", "observe", screen_diff, vnc.screen_diff, vnc.screen_diff, SCREEN),
            (
                "wait_until_stable",
                "observe",
                wait_until_stable,
                vnc.wait_until_stable,
                vnc.wait_until_stable,
                SCREEN,
            ),
            (
                "accessibility_tree",
                "observe",
                a11y.accessibility_tree,
                a11y.accessibility_tree,
                a11y.accessibility_tree,
                SCREEN,
            ),
            ("click", "mouse", MoseTools.click, vnc.Mouse.click, vnc.Mouse.click, INPUT),
            ("move_mouse", "mouse", MoseTools.move, vnc.Mouse.move, vnc.Mouse.move, INPUT),
            ("double_click", "mouse", MoseTools.double_click, vnc.Mouse.double_click, vnc.Mouse.double_click, INPUT),
            ("scroll", "mouse", MoseTools.scroll, vnc.Mouse.scroll, vnc.Mouse.scroll, INPUT),
            ("drag", "mouse", MoseTools.drag, vnc.Mouse.drag, vnc.Mouse.drag, INPUT),
            ("type_text", "keyboard", KeyboardTools.type_text, vnc.Keyboard.type_text, vnc.Keyboard.type_text, INPUT),
            ("press_key", "keyboard", KeyboardTools.press_key, vnc.Keyboard.press_key, vnc.Keyboard.press_key, INPUT),
            ("hotkey", "keyboard", hotkey, vnc.hotkey, vnc.hotkey, INPUT),
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
