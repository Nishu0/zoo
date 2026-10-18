"""Exercise the host coordinator without restoring or booting a real macOS VM."""

import os
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def coordinator(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    home = tmp_path / "home"
    vm = home / ".zoovm/vms/zoo-test"
    vm.mkdir(parents=True)
    (vm / "installed").touch()
    (vm / "machine.bin").write_text("unchanged-machine-identity")
    trace = home / "commands"
    status = home / ".zoovm/preparation/zoo-test"

    def executable(name, body):
        path = tools / name
        path.write_text("#!/bin/bash\nset -eu\n" + body)
        path.chmod(0o755)
        return str(path)

    runtime = executable(
        "zoovm",
        """printf '%s\\n' "$*" >> "$HOME/commands"
case "$1" in
stop) sleep 0.2; if [ -f "$HOME/vm-pid" ]; then kill "$(cat "$HOME/vm-pid")" 2>/dev/null || true; fi ;;
run) echo "$$" > "$HOME/vm-pid"; exec /bin/sleep 60 ;;
ip) echo 192.168.64.2 ;;
install) [ "$#" -eq 4 ] && [ "$3" = --ipsw ] && [ -f "$4" ] || exit 99; touch "$HOME/.zoovm/vms/$2/installed"; echo 'installing 100%' ;;
esac
""",
    )
    seed = executable(
        "seed",
        """cat >/dev/null
if [ -f "$HOME/configured" ]; then echo ZOO_BASE_CONFIGURED=zoo-test;
else echo ZOO_BASE_SEEDED=zoo-test; fi
""",
    )
    executable("id", "echo 501\n")
    executable("sudo", 'shift; exec "$@"\n')
    nc = executable("nc", "exit 0\n")
    source = (Path(__file__).resolve().parents[1] / "macos/prepare-base.sh").read_text()
    source = source.replace("/usr/local/bin/zoovm", runtime)
    source = source.replace("/usr/local/libexec/zoo-macos/base-prepare", seed)
    source = source.replace("/usr/bin/nc", nc)
    lockf = tools / "lockf"
    lockf.write_text(
        "#!/usr/bin/env python3\nimport fcntl, sys\ntry: fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(75)\n"
    )
    lockf.chmod(0o755)
    # Prefer actual native lockf on Macs; Linux CI uses its syscall equivalent.
    lock_command = "/usr/bin/lockf"
    if not Path(lock_command).exists():
        source = source.replace(lock_command, str(lockf))
        lock_command = str(lockf)
    source = source.replace(
        "PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin", f"PATH={tools}:/usr/bin:/bin:/usr/sbin:/sbin"
    )
    coordinator = tmp_path / "prepare.sh"
    coordinator.write_text(source)
    processes = []

    def wait_for(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("coordinator did not reach expected state")

    def phase():
        return (status / "state").read_text().strip() if (status / "state").exists() else ""

    def start(log, extra=()):
        process = subprocess.Popen(
            ["bash", str(coordinator), "--name", "zoo-test", "--guest-user", "admin", *extra],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            env={**os.environ, "HOME": str(home)},
            start_new_session=True,
        )
        processes.append(process)
        assert process.stdin is not None
        process.stdin.write(b"ssh-ed25519 AAAATESTKEY\n")
        process.stdin.close()
        return process

    with (tmp_path / "preparation.log").open("wb") as log:
        try:
            yield SimpleNamespace(
                home=home,
                vm=vm,
                trace=trace,
                status=status,
                phase=phase,
                wait_for=wait_for,
                start=lambda extra=(): start(log, extra),
                script=coordinator,
                lock_command=lock_command,
                tools=tools,
            )
        finally:
            for process in processes:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)


def test_interrupted_preparation_retries_an_installed_configured_guest(coordinator):
    c = coordinator
    first = c.start()
    c.wait_for(lambda: c.phase() == "booting" and (c.home / "vm-pid").exists())
    assert int((c.status / "pid").read_text()) == first.pid
    first.send_signal(signal.SIGTERM)
    assert first.wait(timeout=10) != 0
    assert c.phase() == "failed"
    assert (c.status / "error").read_text().strip()
    assert not (c.status / "lock").exists()

    # The root helper reports successful on-disk firstboot completion instead
    # of overwriting guest credentials when a coordinator is retried.
    (c.home / "configured").touch()
    second = c.start()
    c.wait_for(lambda: (c.status / "pid").read_text().strip() == str(second.pid))
    c.wait_for(lambda: c.phase() == "starting")
    assert second.wait(timeout=10) == 0
    assert c.phase() == "verifying"
    assert not (c.status / "error").exists()
    assert not (c.status / "lock").exists()
    calls = c.trace.read_text().splitlines()
    assert not any(call.startswith("install ") for call in calls)
    assert calls.count("run zoo-test") == 2
    assert (c.vm / "machine.bin").read_text() == "unchanged-machine-identity"
    assert not (c.vm / "zoo-ready").exists()


@pytest.mark.parametrize("directory", ["lock", "recovery"])
@pytest.mark.parametrize("pid", [None, "invalid-pid", "0"])
def test_recovers_aged_incomplete_lock_directories(coordinator, directory, pid):
    c = coordinator
    orphan = c.status / directory
    orphan.mkdir(parents=True)
    stale = time.time() - 120
    if pid is not None:
        (orphan / "pid").write_text(pid)
        os.utime(orphan / "pid", (stale, stale))
    os.utime(orphan, (stale, stale))
    (c.home / "configured").touch()
    process = c.start()
    assert process.wait(timeout=10) == 0
    assert c.phase() == "verifying"
    assert not (c.status / "lock").exists()
    assert not (c.status / "recovery").exists()


@pytest.mark.parametrize("directory", ["lock", "recovery"])
def test_preserves_recent_incomplete_lock_acquisition(coordinator, directory):
    c = coordinator
    recent = c.status / directory
    recent.mkdir(parents=True)
    process = c.start()
    assert process.wait(timeout=10) == 0
    assert recent.exists()
    assert not c.trace.exists()


def test_does_not_steal_live_lock_even_when_directory_is_old(coordinator):
    c = coordinator
    locked = c.status / "lock"
    locked.mkdir(parents=True)
    (locked / "pid").write_text(str(os.getpid()))
    stale = time.time() - 120
    os.utime(locked, (stale, stale))
    process = c.start()
    assert process.wait(timeout=10) == 0
    assert (locked / "pid").read_text() == str(os.getpid())
    assert not c.trace.exists()


def test_kernel_guard_blocks_a_duplicate_then_releases_after_sigkill(coordinator):
    c = coordinator
    first = c.start()
    c.wait_for(lambda: c.phase() == "booting" and (c.home / "vm-pid").exists())
    before = c.trace.read_text()
    duplicate = c.start()
    assert duplicate.wait(timeout=10) == 0
    assert c.trace.read_text() == before
    first.kill()
    assert first.wait(timeout=10) != 0
    (c.home / "configured").touch()
    retry = c.start()
    assert retry.wait(timeout=10) == 0
    assert c.phase() == "verifying"
    assert c.trace.read_text().splitlines().count("run zoo-test") == 2


def test_legacy_macos_lockf_uses_stock_perl_and_keeps_serialization(coordinator):
    c = coordinator
    legacy = c.tools / "legacy-lockf"
    legacy.write_text("#!/bin/sh\nexit 64\n")
    legacy.chmod(0o755)
    c.script.write_text(c.script.read_text().replace(c.lock_command, str(legacy)))
    first = c.start()
    c.wait_for(lambda: c.phase() == "booting" and (c.home / "vm-pid").exists())
    before = c.trace.read_text()
    duplicate = c.start()
    assert duplicate.wait(timeout=10) == 0
    assert c.trace.read_text() == before
    first.kill()
    assert first.wait(timeout=10) != 0
    (c.home / "configured").touch()
    retry = c.start()
    assert retry.wait(timeout=10) == 0
    assert c.phase() == "verifying"


def test_custom_restore_image_with_spaces_is_forwarded_only_for_a_missing_base(coordinator):
    c = coordinator
    (c.vm / "installed").unlink()
    image = c.home / "custom restore image.ipsw"
    image.touch()
    (c.home / "configured").touch()
    process = c.start(("--ipsw", str(image)))
    assert process.wait(timeout=10) == 0
    assert c.phase() == "verifying"
    assert f"install zoo-test --ipsw {image}" in c.trace.read_text()


def test_an_installed_base_ignores_an_unused_restore_image_override(coordinator):
    c = coordinator
    (c.home / "configured").touch()
    process = c.start(("--ipsw", str(c.home / "no-longer-existing.ipsw")))
    assert process.wait(timeout=10) == 0
    assert c.phase() == "verifying"
    assert "install " not in c.trace.read_text()
