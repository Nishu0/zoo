"""Run the actual readiness shell with disposable process/console fixtures."""

import shlex
import subprocess

import pytest

from apps.server import macos


@pytest.mark.parametrize(
    "console,processes,ready",
    [
        ("admin", [(501, "Finder"), (501, "Dock")], True),
        ("admin", [(501, "Finder"), (501, "Dock"), (501, "Setup Assistant")], False),
        ("admin", [(501, "Finder"), (501, "Dock"), (501, "SetupAssistant")], False),
        ("admin", [(501, "Finder")], False),
        ("admin", [(501, "Dock")], False),
        ("loginwindow", [(501, "Finder"), (501, "Dock")], False),
        ("root", [(501, "Finder"), (501, "Dock")], False),
        ("admin", [(501, "Finder"), (501, "Dock"), (502, "SetupAssistant")], True),
        ("admin", [(501, "Finder"), (501, "Dock"), (501, "SetupAssistantHelper")], True),
        ("admin", [(502, "Finder"), (502, "Dock")], False),
    ],
)
def test_desktop_readiness_requires_user_finder_dock_and_no_setup_assistant(
    monkeypatch, tmp_path, console, processes, ready
):
    table = tmp_path / "processes"
    table.write_text("".join(f"{uid}\t{name}\n" for uid, name in processes))
    console_file = tmp_path / "console"
    console_file.write_text(console + "\n")
    prefix = f"""set -eu
processes={shlex.quote(str(table))}
console={shlex.quote(str(console_file))}
id() {{ [ "$#" = 2 ] && [ "$1" = -u ] && [ "$2" = admin ] || return 92; printf '501\\n'; }}
stat() {{ [ "$#" = 3 ] && [ "$1" = -f ] && [ "$2" = %Su ] && [ "$3" = /dev/console ] || return 93; cat "$console"; }}
pgrep() {{
    [ "$#" = 4 ] && [ "$1" = -u ] && [ "$3" = -x ] || return 94
    /usr/bin/awk -F '\\t' -v selected="$2" '$1 == selected {{print $2}}' "$processes" | /usr/bin/grep -E -x -- "$4"
}}
"""
    calls = []

    def guest(runtime_id, command, **options):
        calls.append((runtime_id, options))
        result = subprocess.run(["/bin/sh", "-c", prefix + command], capture_output=True, check=False, timeout=3)
        return result.returncode, result.stdout, result.stderr

    monkeypatch.setattr(macos, "USER", "admin")
    monkeypatch.setattr(macos, "guest", guest)
    assert macos.desktop_ready("host") is ready
    assert calls == [(macos.base_id("host"), {"timeout": 5, "ssh": True})]
