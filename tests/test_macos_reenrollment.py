"""Recover only a proven Zoo base when a new control plane replaces its SSH key."""

import shlex
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import paramiko
import pytest

from apps.server import macos


@pytest.fixture
def recovery(monkeypatch):
    state = SimpleNamespace(
        rid=macos.base_id("host"),
        password="ab" * 18,
        client=Mock(),
        tunnel=Mock(),
        stale_user=Mock(),
        stale_root=Mock(),
    )
    state.run = Mock(
        side_effect=lambda host, command: (0, (state.password if "stat -f %Lp" in command else "old-key").encode(), b"")
    )
    state.execute = Mock(return_value=(0, b"", b""))
    monkeypatch.setattr(macos, "run", state.run)
    monkeypatch.setattr(macos, "check", Mock(return_value="192.168.64.2"))
    monkeypatch.setattr(macos, "open_socket", Mock(return_value=state.tunnel))
    monkeypatch.setattr(macos.paramiko, "SSHClient", Mock(return_value=state.client))
    monkeypatch.setattr(macos, "execute", state.execute)
    monkeypatch.setattr(macos, "public_key", lambda: "new-key")
    monkeypatch.setattr(macos, "validate_controller_key", lambda key: key)
    monkeypatch.setattr(macos, "guests", {state.rid: state.stale_user})
    monkeypatch.setattr(macos, "roots", {state.rid: state.stale_root})
    return state


def test_recovery_uses_private_password_only_for_user_auth_and_closes_connections(recovery):
    macos.recover_base_key(recovery.rid)
    options = recovery.client.connect.call_args.kwargs
    assert options["username"] == macos.USER
    assert options["password"] == recovery.password
    assert options["allow_agent"] is False and options["look_for_keys"] is False
    proof, rotation = recovery.execute.call_args_list
    assert "sudo -n" in proof.args[1] and "/var/db/zoo/done" in proof.args[1]
    assert "/var/db/zoo/user" in proof.args[1]
    assert recovery.password not in str(recovery.execute.call_args_list)
    assert rotation.args[2] == b"new-key\nold-key\n"
    command = recovery.run.call_args_list[0].args[1]
    assert "zoo-seeded" in command and "test ! -L" in command
    assert "stat -f %Lp" in command and "= 600" in command and "stat -f %u" in command
    for connection in (recovery.client, recovery.tunnel, recovery.stale_user, recovery.stale_root):
        connection.close.assert_called_once()
    assert recovery.rid not in macos.guests and recovery.rid not in macos.roots


def test_recovery_refuses_every_sandbox_before_reading_credentials(recovery):
    with pytest.raises(ValueError, match="restricted to the base"):
        macos.recover_base_key("macos:host:zoo-sandbox")
    recovery.run.assert_not_called()
    recovery.client.connect.assert_not_called()


def test_invalid_host_password_guard_never_discloses_partial_secret(recovery):
    recovery.run.return_value = (1, recovery.password.encode(), b"")
    recovery.run.side_effect = None
    with pytest.raises(RuntimeError, match="no private Zoo bootstrap password") as failure:
        macos.recover_base_key(recovery.rid)
    assert recovery.password not in str(failure.value)
    recovery.client.connect.assert_not_called()


def test_unproven_guest_cannot_rotate_keys_and_password_connection_is_closed(recovery):
    recovery.execute.return_value = (1, b"", b"not a configured Zoo guest")
    with pytest.raises(RuntimeError, match="not a configured Zoo guest"):
        macos.recover_base_key(recovery.rid)
    assert recovery.execute.call_count == 1
    recovery.client.close.assert_called_once()
    recovery.tunnel.close.assert_called_once()


def test_real_rotation_script_preserves_unrelated_keys_and_retires_recorded_controller(recovery, tmp_path):
    macos.recover_base_key(recovery.rid)
    script = shlex.split(recovery.execute.call_args.args[1])[4]
    root, user, state = tmp_path / "root", tmp_path / "user", tmp_path / "state"
    for directory in (root, user):
        directory.mkdir()
        (directory / "authorized_keys").write_text("unrelated-key\nold-key\nnew-key\n")
    state.mkdir()
    (state / "done").touch()
    (state / "user").write_text(macos.USER)
    script = script.replace("/var/root/.ssh", str(root)).replace("/Users/$user/.ssh", str(user))
    script = script.replace("/var/db/zoo", str(state))
    subprocess.run(
        ["/bin/sh", "-c", "chown() { :; }\n" + script, "test", macos.USER],
        input=b"new-key\nold-key\n",
        check=True,
    )
    for directory in (root, user):
        authorized = directory / "authorized_keys"
        assert authorized.read_text() == "unrelated-key\nnew-key\n"
        assert authorized.stat().st_mode & 0o777 == 0o600


def test_stale_ready_marker_requires_live_recovery_desktop_and_new_agent_exec(monkeypatch):
    monkeypatch.setattr(macos, "public_key", lambda: "new-controller")
    monkeypatch.setattr(
        macos, "run", lambda host, command, **kwargs: (1 if command.startswith("test -f") else 0, b"", b"")
    )
    monkeypatch.setattr(macos, "running_vms", lambda host: {macos.BASE_VM})
    desktop = Mock(side_effect=[paramiko.AuthenticationException("old key"), True])
    recover, install, mark, bind = Mock(), Mock(), Mock(), Mock()
    execution = Mock(return_value=(0, b"", b""))
    monkeypatch.setattr(macos, "desktop_ready", desktop)
    monkeypatch.setattr(macos, "recover_base_key", recover)
    monkeypatch.setattr(macos, "install_guest", install)
    monkeypatch.setattr(macos, "mark_base", mark)
    monkeypatch.setattr(macos.hub, "bind", bind)
    monkeypatch.setattr(macos, "connected", lambda *args: SimpleNamespace(run=execution))
    assert macos.base_ready("host")
    recover.assert_called_once_with(macos.base_id("host"))
    assert desktop.call_count == 2
    install.assert_called_once_with(macos.base_id("host"), "base-host")
    execution.assert_called_once_with(["true"], timeout=5)
    mark.assert_called_once_with("host", verified=True)


def test_ordinary_version_update_cannot_bless_an_old_controller(monkeypatch):
    monkeypatch.setattr(macos, "run", lambda *args: (1, b"", b""))
    monkeypatch.setattr(macos, "public_key", lambda: "new-controller")
    write = Mock()
    monkeypatch.setattr(macos, "check", write)
    macos.mark_base("host")
    assert all("zoo-controller-key" not in item.args[1] for item in write.call_args_list)
    write.reset_mock()
    macos.mark_base("host", verified=True)
    assert "zoo-controller-key" in write.call_args_list[0].args[1]


def test_stopped_ready_base_with_unknown_controller_is_not_trusted(monkeypatch):
    probe = Mock(return_value=(1, b"", b""))
    monkeypatch.setattr(macos, "run", probe)
    monkeypatch.setattr(macos, "public_key", lambda: "new-controller")
    monkeypatch.setattr(macos, "running_vms", lambda host: set())
    assert macos.base_ready("host") is False
    assert "zoo-controller-key" in probe.call_args.args[1]
    assert "new-controller" in probe.call_args.args[1]
