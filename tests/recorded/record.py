"""Records macOS or Windows backend traffic from a running sandbox into tests/recorded/fixtures/<platform>.json.

Run it on the machine that runs the API, against a sandbox you don't mind the tools acting on (it opens and
closes an app, closes a window and writes zoo-test files in the home directory):

    uv run python -m tests.recorded.record macos <sandbox-id>
    uv run python -m tests.recorded.record windows <sandbox-id>

Re-record after changing a backend's commands on purpose; review the fixture diff like code.
"""

import json
import sys
from datetime import UTC, datetime

import pytest

from apps.server.registry import TOOLS
from db.connection import db_manager
from tests.recorded.recorder import encode, fixture_path, recording, ssh_tools
from tests.tool_args import SAMPLE_ARGS

APPS = {"macos": "TextEdit", "windows": "notepad"}
WINDOW_TOOLS = {
    "window_focus",
    "window_minimize",
    "window_restore",
    "window_maximize",
    "window_unmaximize",
    "window_close",
}


def arguments(platform: str, name: str, window_id: str | None) -> dict:
    args = dict(SAMPLE_ARGS[name])
    if name == "open_app":
        args["command"] = APPS[platform]
    if name == "close_app":
        args["target"] = APPS[platform]
    if name in WINDOW_TOOLS and window_id:
        args["window_id"] = window_id
    return args


def run_tool(platform: str, runtime_id: str, name: str, args: dict, calls: list) -> dict:
    tool = TOOLS[name]
    impl = tool.mac if platform == "macos" else tool.win
    with pytest.MonkeyPatch.context() as mp, recording(platform, mp, calls):
        try:
            return {"result": encode(impl(runtime_id, **args))}
        except Exception as e:
            return {"error": {"type": type(e).__name__, "message": str(e)}}


def main(platform: str, sandbox_id: str):

    db_manager.init_db()
    with db_manager.session() as db:
        sandbox = db.get_sandbox(id=sandbox_id)
    if sandbox is None or sandbox.kind != platform or sandbox.status != "running":
        sys.exit(f"{sandbox_id} is not a running {platform} sandbox")
    runtime_id = sandbox.runtime_id

    order = [n for n in SAMPLE_ARGS if n in ssh_tools(platform) and n not in WINDOW_TOOLS and n != "close_app"]
    tools: dict[str, dict] = {}
    for name in order:
        args = arguments(platform, name, None)
        calls: list[dict] = []
        tools[name] = {"args": args, "calls": calls, **run_tool(platform, runtime_id, name, args, calls)}
        print(f"{name}: {'error' if 'error' in tools[name] else 'ok'}")

    windows = tools.get("windows_list", {}).get("result") or []
    window_id = next((w["id"] for w in windows if APPS[platform].lower() in json.dumps(w).lower()), None)
    for name in [n for n in SAMPLE_ARGS if n in WINDOW_TOOLS] + ["close_app"]:
        args = arguments(platform, name, window_id)
        calls = []
        tools[name] = {"args": args, "calls": calls, **run_tool(platform, runtime_id, name, args, calls)}
        print(f"{name}: {'error' if 'error' in tools[name] else 'ok'}")

    fixture = {
        "platform": platform,
        "runtime_id": runtime_id,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tools": tools,
    }
    fixture_path(platform).write_text(json.dumps(fixture, indent=1) + "\n")
    print(f"wrote {fixture_path(platform)}")


if __name__ == "__main__":
    # reads the operator's .env for DATABASE_URL and the secrets key, like the API does
    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) != 3 or sys.argv[1] not in ("macos", "windows"):
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
