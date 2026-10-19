"""Preparing a Mac is an API operation; sandbox jobs wait for the resulting usable guest."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from apps.server import macos
from tests.test_sandboxes import add_server


@pytest.fixture(autouse=True)
def guest_ssh_ready(monkeypatch):
    monkeypatch.setattr(macos, "guest", Mock(return_value=(0, b"", b"")))

    @contextmanager
    def guard(_):
        yield True

    monkeypatch.setattr(macos, "preparation_guard", guard)


@pytest.mark.parametrize("state", ["missing", "failed", "stopped", "running"])
def test_prepare_can_complete_an_existing_unready_macos_base(client, alice, zoo, monkeypatch, state):
    server_id = add_server("alice@example.com", "macos")
    install = Mock()
    monkeypatch.setattr(macos, "base_status", lambda _: {"state": state, "ready": False})
    monkeypatch.setattr(macos, "base_install", install)
    monkeypatch.setattr(zoo.servers_api, "watch_base", Mock())
    response = client.post(f"/servers/{server_id}/base/install", headers=alice)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "installing"
    assert response.json()["ready"] is False
    install.assert_called_once_with(server_id)


def test_prepare_does_not_modify_another_workspaces_mac(client, alice, bob, zoo, monkeypatch):
    server_id = add_server("bob@example.com", "macos")
    install = Mock()
    monkeypatch.setattr(macos, "base_install", install)
    response = client.post(f"/servers/{server_id}/base/install", headers=alice)
    assert response.status_code == 404
    install.assert_not_called()


@pytest.mark.parametrize("phase", ["downloading", "installing", "configuring", "booting"])
def test_preparation_stages_report_progress_without_claiming_the_guest_is_ready(monkeypatch, phase):
    monkeypatch.setattr(macos, "preparation_status", lambda _: (phase, ""))
    verify = Mock()
    monkeypatch.setattr(macos, "base_ready", verify)
    status = macos.base_status("mac")
    assert status["state"] == "installing"
    assert status["ready"] is False
    assert status["message"]
    verify.assert_not_called()


def test_preparation_failure_preserves_the_host_error_for_retry(monkeypatch):
    monkeypatch.setattr(macos, "preparation_status", lambda _: ("failed", "restore failed: not enough free disk space"))
    status = macos.base_status("mac")
    assert status["state"] == "failed"
    assert status["ready"] is False
    assert "not enough free disk space" in status["message"]


@pytest.mark.parametrize("guest_ready", [False, True])
def test_verification_finishes_only_after_guest_commands_work_then_frees_the_base_slot(monkeypatch, guest_ready):
    timeline = Mock()
    ready = Mock(return_value=guest_ready)
    stop = Mock()
    write = Mock()
    for name, operation in (("ready", ready), ("stop", stop), ("write", write)):
        timeline.attach_mock(operation, name)
    monkeypatch.setattr(macos, "preparation_status", lambda _: ("verifying", ""))
    monkeypatch.setattr(macos, "base_ready", ready)
    monkeypatch.setattr(macos, "base_stop", stop)
    monkeypatch.setattr(macos, "check", write)
    status = macos.base_status("mac")
    if guest_ready:
        assert status["state"] == "stopped"
        assert status["ready"] is True
        assert [operation[0] for operation in timeline.mock_calls] == ["ready", "stop", "write"]
    else:
        assert status["state"] == "installing"
        assert status["ready"] is False
        stop.assert_not_called()
        write.assert_not_called()


def test_agent_boot_finishes_preparation_before_accepting_a_ready_guest(monkeypatch):
    monkeypatch.setattr(macos, "preparation_status", lambda _: ("verifying", ""))
    monkeypatch.setattr(macos, "base_ready", lambda _: True)
    stop = Mock()
    monkeypatch.setattr(macos, "base_stop", stop)
    monkeypatch.setattr(macos, "check", Mock())
    macos.ensure_base("mac")
    stop.assert_called_once_with("mac")


def test_public_bootstrap_archive_contains_only_the_packaged_macos_helpers(client):
    import io
    import tarfile
    from pathlib import Path

    response = client.get("/v1/nodes/bootstrap/macos")
    assert response.status_code == 200, response.text
    expected = {"base-prepare.sh", "firstboot.sh", "configure-guest.sh", "guest-reidentify.sh", "prepare-base.sh"}
    root = Path(__file__).resolve().parents[1] / "macos"
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as bundle:
        assert set(bundle.getnames()) == expected
        for item in bundle:
            assert item.isfile()
            content = bundle.extractfile(item)
            assert content is not None
            assert content.read() == (root / item.name).read_bytes()


def test_concurrent_status_requests_finalize_the_shared_base_once(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered, release = threading.Event(), threading.Event()

    def verify(_):
        entered.set()
        assert release.wait(timeout=3)
        return True

    monkeypatch.setattr(macos, "preparation_status", lambda _: ("verifying", ""))
    monkeypatch.setattr(macos, "base_ready", verify)
    monkeypatch.setattr(macos, "_preparation_locks", {})
    stop = Mock()
    monkeypatch.setattr(macos, "base_stop", stop)
    monkeypatch.setattr(macos, "check", Mock())
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(macos.base_status, "mac")
        assert entered.wait(timeout=3)
        second = workers.submit(macos.base_status, "mac")
        try:
            assert second.result(timeout=3)["state"] == "installing"
        finally:
            release.set()
        assert first.result(timeout=3)["ready"] is True
    stop.assert_called_once_with("mac")


@pytest.mark.parametrize(
    "phase,pid,expected",
    [
        ("downloading", None, "failed"),
        ("booting", "99999999", "failed"),
        ("installing", "live", "installing"),
        ("verifying", None, "verifying"),
        ("ready", None, "ready"),
    ],
)
def test_preparation_status_detects_interrupted_processes_and_keeps_verification_recoverable(
    monkeypatch, tmp_path, phase, pid, expected
):
    import os
    import shlex
    import subprocess

    (tmp_path / "state").write_text(phase + "\n")
    if pid is not None:
        (tmp_path / "pid").write_text(str(os.getpid()) if pid == "live" else pid)
    monkeypatch.setattr(macos, "PREPARATION", shlex.quote(str(tmp_path)))

    def run(_, command):
        result = subprocess.run(["bash", "-c", command], capture_output=True, timeout=3, check=False)
        return result.returncode, result.stdout, result.stderr

    monkeypatch.setattr(macos, "run", run)
    state = macos.preparation_status("mac")
    assert state is not None
    assert state[0] == expected
    if expected == "failed":
        assert "retry preparation" in state[1]


def test_guest_exec_without_a_logged_in_desktop_does_not_finish_preparation(monkeypatch):
    monkeypatch.setattr(macos, "preparation_status", lambda _: ("verifying", ""))
    monkeypatch.setattr(macos, "base_ready", lambda _: True)
    monkeypatch.setattr(macos, "guest", Mock(return_value=(1, b"", b"console user is loginwindow")))
    stop = Mock()
    monkeypatch.setattr(macos, "base_stop", stop)
    status = macos.base_status("mac")
    assert status["state"] == "installing"
    assert status["ready"] is False
    assert "desktop" in status["message"]
    stop.assert_not_called()


def test_guest_verification_has_an_actionable_deadline(monkeypatch, tmp_path):
    import shlex
    import subprocess

    (tmp_path / "state").write_text("verifying\n")
    monkeypatch.setattr(macos, "PREPARATION", shlex.quote(str(tmp_path)))

    def run(_, command):
        # Darwin stat's modification-time output; also runnable on Linux CI.
        result = subprocess.run(
            ["bash", "-c", "stat() { printf '1\\n'; }; " + command],
            capture_output=True,
            timeout=3,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr

    monkeypatch.setattr(macos, "run", run)
    state = macos.preparation_status("mac")
    assert state is not None
    assert state[0] == "failed"
    assert "verification timed out" in state[1]
    assert "retry preparation" in state[1]


def test_preparation_cannot_finish_when_the_base_vm_fails_to_stop(monkeypatch):
    monkeypatch.setattr(macos, "stop", Mock())
    monkeypatch.setattr(macos, "running_vms", lambda _: {macos.BASE_VM})
    mark = Mock()
    monkeypatch.setattr(macos, "mark_base", mark)
    with pytest.raises(RuntimeError, match="(?i)stop"):
        macos.base_stop("mac")
    mark.assert_not_called()


def test_other_process_holding_verification_guard_prevents_any_guest_work(monkeypatch):
    @contextmanager
    def guard(_):
        yield False

    monkeypatch.setattr(macos, "preparation_guard", guard)
    monkeypatch.setattr(macos, "preparation_status", lambda _: ("verifying", ""))
    ready, stop = Mock(), Mock()
    monkeypatch.setattr(macos, "base_ready", ready)
    monkeypatch.setattr(macos, "base_stop", stop)
    status = macos.base_status("mac")
    assert status["state"] == "installing"
    assert status["ready"] is False
    ready.assert_not_called()
    stop.assert_not_called()


@pytest.mark.parametrize("changed", ["ready", "failed", "booting"])
def test_verification_rechecks_phase_after_acquiring_the_host_guard(monkeypatch, changed):
    phases = Mock(side_effect=[("verifying", ""), (changed, "")])
    monkeypatch.setattr(macos, "preparation_status", phases)
    ready, stop = Mock(), Mock()
    monkeypatch.setattr(macos, "base_ready", ready)
    monkeypatch.setattr(macos, "base_stop", stop)
    status = macos.base_status("mac")
    assert status["state"] == "installing"
    assert status["ready"] is False
    ready.assert_not_called()
    stop.assert_not_called()
