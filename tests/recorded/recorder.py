"""Records the traffic of the macOS and Windows backends once, from a real host, and replays it in CI.

Every command a backend sends goes through a few functions (host `run`, guest `guest`, on macOS the guest's
window service `window`, and on Windows zoo-guest's `guest_raw` and `window`). Recording wraps them and saves each call with its result; replaying
swaps them for a stub that checks the backend sends exactly the recorded commands, in order, and hands back
the recorded results. A changed command, an extra call or a different result fails the test.
VNC-driven tools (screenshot, mouse, keyboard) talk RFB rather than SSH and are not covered here.
"""

import base64
import builtins
import json
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from server import macos, macos_tools, registry, windows, windows_tools

FIXTURES = Path(__file__).parent / "fixtures"
SEAMS: dict[str, dict[str, list[Any]]] = {
    "macos": {"run": [macos], "guest": [macos, macos_tools], "window": [macos_tools]},
    "windows": {
        "run": [windows],
        "guest": [windows, windows_tools],
        "guest_raw": [windows],
        "window": [windows, windows_tools],
    },
}
# reaching any of these during a replay means a code path opened a connection the recording never saw
CONNECTIONS = {"macos": ["connect", "host", "guest_client"], "windows": ["connect", "host"]}


def encode(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"b64": base64.b64encode(value).decode()}
    if isinstance(value, tuple):
        return {"tuple": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    return value


def decode(value: Any) -> Any:
    if isinstance(value, dict) and value.keys() == {"b64"}:
        return base64.b64decode(value["b64"])
    if isinstance(value, dict) and value.keys() == {"tuple"}:
        return tuple(decode(v) for v in value["tuple"])
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        return {k: decode(v) for k, v in value.items()}
    return value


def rebuild(error: dict) -> Exception:
    kind = getattr(builtins, error["type"], None)
    if not (isinstance(kind, type) and issubclass(kind, Exception)):
        kind = RuntimeError
    return kind(error["message"])


def ssh_tools(platform: str) -> list[str]:
    """Tools whose implementation on this platform goes through the guest rather than the gateway screen."""
    from server.registry import TOOLS

    return [
        name
        for name, tool in TOOLS.items()
        if getattr(tool, "mac" if platform == "macos" else "win").__module__ != registry.__name__
    ]


def call_args(fn: Callable, args: tuple, kwargs: dict) -> dict:
    import inspect

    bound = inspect.signature(fn).bind(*args, **kwargs)
    bound.apply_defaults()
    return encode(dict(bound.arguments))


@contextmanager
def recording(platform: str, mp: pytest.MonkeyPatch, calls: list[dict]):
    for name, modules in SEAMS[platform].items():
        original = getattr(modules[0], name)

        def wrapper(*args, _name=name, _fn=original, **kwargs):
            entry = {"fn": _name, "args": call_args(_fn, args, kwargs)}
            calls.append(entry)
            try:
                result = _fn(*args, **kwargs)
            except Exception as e:
                entry["error"] = {"type": type(e).__name__, "message": str(e)}
                raise
            entry["result"] = encode(result)
            return result

        for module in modules:
            mp.setattr(module, name, wrapper)
    yield


@contextmanager
def replaying(platform: str, mp: pytest.MonkeyPatch, calls: list[dict]):
    pending = list(calls)

    for name, modules in SEAMS[platform].items():
        original = getattr(modules[0], name)

        def stub(*args, _name=name, _fn=original, **kwargs):
            assert pending, f"unrecorded {_name} call: {call_args(_fn, args, kwargs)}"
            expected = pending.pop(0)
            assert expected["fn"] == _name, f"expected a {expected['fn']} call, got {_name}"
            assert call_args(_fn, args, kwargs) == expected["args"], f"{_name} was sent a different command"
            if "error" in expected:
                raise rebuild(expected["error"])
            return decode(expected["result"])

        for module in modules:
            mp.setattr(module, name, stub)
    for name in CONNECTIONS[platform]:
        module = macos if platform == "macos" else windows
        mp.setattr(module, name, lambda *a, _n=name, **k: pytest.fail(f"replay tried to open a connection via {_n}"))
    yield pending


def fixture_path(platform: str) -> Path:
    return FIXTURES / f"{platform}.json"


def load(platform: str) -> dict | None:
    path = fixture_path(platform)
    return json.loads(path.read_text()) if path.exists() else None
