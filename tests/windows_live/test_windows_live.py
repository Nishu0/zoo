"""Windows tools against a real zoo-guest on a real Windows desktop, with no VM: the CI job `windows-live` runs this
on GitHub's Windows runner. Every Windows tool goes through zoo-guest, so this covers the commands the API sends and
the guest that runs them; Hyper-V itself (clone, start, push) is covered by the nightly run on a Hyper-V host.

ZOO_GUEST_EXE is the guest build to test (make guest-windows). Skipped anywhere else."""

import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from server import a11y, windows, windows_tools
from server import guest as guest_module

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("ZOO_GUEST_EXE"), reason="needs Windows and ZOO_GUEST_EXE"
)

SANDBOX = "live"
RID = f"windows:live:{windows.vm_name(SANDBOX)}"


def start_guest(live_url: str, tmp_path, exe: str | None = None, sandbox_id: str = SANDBOX) -> subprocess.Popen:
    port = urlparse(live_url).port
    env = tmp_path / f"{sandbox_id}.env"
    env.write_text(
        f"ZOO_GUEST_URL=ws://127.0.0.1:{port}/guest/connect\n"
        f"ZOO_GUEST_TOKEN={guest_module.token(sandbox_id)}\nZOO_SANDBOX_ID={sandbox_id}\n"
    )
    exe = exe or os.environ["ZOO_GUEST_EXE"]
    return subprocess.Popen([exe, "-env", str(env), "-log", str(tmp_path / f"{sandbox_id}.log")])


def connected(sandbox_id: str = SANDBOX):
    agent = windows.hub.for_sandbox(sandbox_id)
    assert agent is not None, f"no guest connected for {sandbox_id}"
    return agent


def wait_for(condition, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("timed out")
        time.sleep(0.2)


@pytest.fixture
def vm(live_url, tmp_path, monkeypatch):
    """A runtime id whose guest is the zoo-guest under test, with the sandbox home in a temporary directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(windows, "HOME", str(home))
    monkeypatch.setattr(windows_tools, "HOME", str(home))
    monkeypatch.setattr(windows_tools, "ENV_FILE", str(home / ".zoo" / "env.ps1"))
    windows.hub.bind(RID, SANDBOX)
    process = start_guest(live_url, tmp_path)
    try:
        wait_for(lambda: windows.hub.for_sandbox(SANDBOX) is not None)
        yield RID
    finally:
        process.kill()
        log = tmp_path / "guest.log"
        if log.exists():
            print(log.read_text(errors="replace"))


def test_commands_run_in_powershell_with_exit_codes_and_timeouts(vm):
    result = windows_tools.WinShell.execute_command(vm, "Write-Output hello; exit 3")
    assert result["stdout"].strip() == "hello" and result["exit_code"] == 3
    assert windows_tools.WinShell.execute_command(vm, "Start-Sleep 10", timeout=1)["exit_code"] == 124


def test_secrets_reach_commands(vm):
    windows.write_env(vm, {"ZOO_LIVE_SECRET": "it's here"})
    assert windows_tools.WinShell.execute_command(vm, "$env:ZOO_LIVE_SECRET")["stdout"].strip() == "it's here"


def test_files_round_trip(vm):
    windows_tools.WinFiles.create_directory(vm, "~/docs")
    windows_tools.WinFiles.write_file(vm, "~/docs/a.txt", "héllo\nworld")
    assert windows_tools.WinFiles.read_file(vm, "~/docs/a.txt") == "héllo\nworld"
    windows_tools.WinFiles.copy_file(vm, "~/docs/a.txt", "~/docs/b.txt")
    windows_tools.WinFiles.move_file(vm, "~/docs/b.txt", "~/docs/c.txt")
    names = {f["name"] for f in windows_tools.WinFiles.list_files(vm, "~/docs")}
    assert names == {"a.txt", "c.txt"}
    windows_tools.WinFiles.delete_file(vm, "~/docs/c.txt")
    assert windows_tools.WinFiles.get_file_info(vm, "~/docs/a.txt")["size"] == len("héllo\nworld".encode())


def test_a_directory_moves_through_tar(vm):
    windows_tools.WinFiles.create_directory(vm, "~/profile")
    windows_tools.WinFiles.write_file(vm, "~/profile/settings.json", '{"a": 1}')
    data = windows.export_dir(vm, windows.HOME + "\\profile")
    windows_tools.WinFiles.delete_file(vm, "~/profile")
    windows.import_dir(vm, windows.HOME, data)
    assert windows_tools.WinFiles.read_file(vm, "~/profile/settings.json") == '{"a": 1}'


def test_apps_and_windows_on_the_desktop(vm):
    assert windows_tools.WinApps.open_app(vm, "notepad")["started"]
    wait_for(lambda: any(w["app"].lower() == "notepad" for w in windows_tools.WinWindows.windows_list(vm)))
    window = next(w for w in windows_tools.WinWindows.windows_list(vm) if w["app"].lower() == "notepad")
    windows_tools.WinWindows.window_minimize(vm, window["id"])
    windows_tools.WinWindows.window_restore(vm, window["id"])
    windows_tools.WinWindows.window_focus(vm, window["id"])
    tree = a11y.accessibility_tree(vm, app="notepad")
    assert tree["elements"] > 0 and "notepad" in tree["app"].lower()
    assert windows_tools.WinApps.close_app(vm, "notepad") is True
    wait_for(lambda: not any(w["app"].lower() == "notepad" for w in windows_tools.WinWindows.windows_list(vm)))


def test_app_policy_blocks_and_allows_an_exe(vm):
    key = r"HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\zoo-live-test.exe"
    windows.apply_apps(vm, {"zoo-live-test.exe": "deny"})
    try:
        debugger = windows.guest_check(vm, f"(Get-ItemProperty -Path {windows.q(key)}).Debugger").strip()
        assert debugger == r"C:\zoo\blocked-by-policy.exe"
        windows.apply_apps(vm, {"zoo-live-test.exe": "allow"})
        assert windows.guest_check(vm, f"(Get-ItemProperty -Path {windows.q(key)}).Debugger").strip() == ""
    finally:
        windows.guest_check(vm, f"Remove-Item -Path {windows.q(key)} -ErrorAction SilentlyContinue")


def test_metrics_are_the_heartbeat(vm):
    wait_for(lambda: connected().metrics is not None, timeout=25)
    metrics = connected().metrics or {}
    assert metrics["memory_limit"] > 0 and metrics["pids"] > 0 and metrics["disk_total"] > 0
    assert windows.hub.heartbeat(SANDBOX)


def test_the_guest_updates_itself_and_reconnects(vm, live_url, tmp_path):
    # a second guest, run from a copy in tmp_path, so the update swaps files there and not the build under test
    build = Path(os.environ["ZOO_GUEST_EXE"]).read_bytes()
    exe = tmp_path / "bin" / "zoo-guest.exe"
    exe.parent.mkdir()
    exe.write_bytes(build)
    process = start_guest(live_url, tmp_path, str(exe), sandbox_id="live-update")
    restarted = None
    try:
        wait_for(lambda: windows.hub.for_sandbox("live-update") is not None)
        before = connected("live-update")
        (tmp_path / "bin" / "zoo-guest.exe.new").write_bytes(build)
        result, _ = before.call("update", {}, timeout=30)
        restarted = result["pid"]
        wait_for(lambda: windows.hub.for_sandbox("live-update") not in (None, before))
        assert (tmp_path / "bin" / "zoo-guest.exe.old").exists()
        assert not (tmp_path / "bin" / "zoo-guest.exe.new").exists()
        wait_for(lambda: process.poll() is not None, timeout=10)  # the old guest exits on its own
    finally:
        process.kill()
        if restarted:
            subprocess.run(["taskkill.exe", "/F", "/PID", str(restarted)], check=False)
