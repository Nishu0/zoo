"""Replays recorded macOS and Windows SSH sessions; see recorder.py for how they are made."""

import pytest

from apps.server.registry import TOOLS
from tests.recorded.recorder import decode, load, replaying, ssh_tools


def cases():
    for platform in ("macos", "windows"):
        fixture = load(platform)
        if fixture is None:
            yield pytest.param(
                platform,
                None,
                None,
                marks=pytest.mark.skip(
                    reason=f"no {platform} recording yet: run tests/recorded/record.py on a {platform} host"
                ),
            )
            continue
        for name in fixture["tools"]:
            yield pytest.param(platform, name, fixture, id=f"{platform}-{name}")


@pytest.mark.parametrize("platform,name,fixture", cases())
def test_replay(platform, name, fixture, monkeypatch):
    recorded = fixture["tools"][name]
    tool = TOOLS[name]
    impl = tool.mac if platform == "macos" else tool.win
    with replaying(platform, monkeypatch, recorded["calls"]) as pending:
        if "error" in recorded:
            with pytest.raises(Exception) as e:
                impl(fixture["runtime_id"], **recorded["args"])
            assert str(e.value) == recorded["error"]["message"]
        else:
            assert impl(fixture["runtime_id"], **recorded["args"]) == decode(recorded["result"])
    assert pending == [], f"{name} skipped recorded calls: {[c['fn'] for c in pending]}"


@pytest.mark.parametrize("platform", ["macos", "windows"])
def test_recording_covers_every_ssh_tool(platform):
    fixture = load(platform)
    if fixture is None:
        pytest.skip(f"no {platform} recording yet")
    assert set(fixture["tools"]) == set(ssh_tools(platform))
