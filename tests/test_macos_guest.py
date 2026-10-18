from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from server import macos
from server.errors import ZooError

CONNECTED = macos.connected


class Agent:
    def __init__(self):
        self.calls = []

    def has(self, service):
        return service == "exec"

    def run(self, argv, stdin=b"", timeout=600):
        self.calls.append((argv, stdin))
        return 0, b"out", b""


def test_guest_runs_through_the_agent_but_root_and_setup_stay_on_ssh(monkeypatch):
    agent, ssh = Agent(), []
    monkeypatch.setattr(macos.hub, "for_runtime", lambda rid: agent)
    monkeypatch.setattr(macos, "guest_client", lambda rid: "guest")
    monkeypatch.setattr(macos, "root_client", lambda rid: "root")
    monkeypatch.setattr(
        macos, "execute", lambda client, command, stdin, timeout: ssh.append((client, command)) or (0, b"", b"")
    )
    rid = "macos:srv:zoo-sb"
    assert macos.guest(rid, "cat > f", stdin=b"data") == (0, b"out", b"")
    assert agent.calls == [(["/bin/sh", "-c", f"cd {macos.HOME} && cat > f"], b"data")]
    macos.guest(rid, "pfctl -e", root=True)
    macos.guest(rid, "shasum f", ssh=True)
    assert len(agent.calls) == 1
    assert ssh == [("root", f"cd {macos.HOME} && pfctl -e"), ("guest", f"cd {macos.HOME} && shasum f")]
    # tools never go over SSH: without the guest they fail
    monkeypatch.setattr(macos.hub, "for_runtime", lambda rid: None)
    with pytest.raises(ZooError, match="guest agent is not connected"):
        macos.guest(rid, "true")


def test_admin_rights_follow_the_sandbox(monkeypatch):
    scripts = []
    monkeypatch.setattr(macos, "guest_check", lambda rid, script, root=False: scripts.append((script, root)))
    macos.set_admin("macos:srv:zoo-sb", False)
    macos.set_admin("macos:srv:zoo-sb", True)
    (drop, root1), (grant, root2) = scripts
    assert root1 and root2
    assert "rm -f /etc/sudoers.d/zoo" in drop and drop.endswith(
        f"! dseditgroup -o checkmember -m {macos.USER} admin >/dev/null"
    )
    assert "NOPASSWD" in grant and "-a " + macos.USER in grant
    # A nested admin membership can already satisfy checkmember, but disappears
    # on demotion. Always add the explicit SSH SACL entry before removing admin.
    explicit_ssh = f"dseditgroup -o edit -a {macos.USER} -t user com.apple.access_ssh"
    assert drop.index(explicit_ssh) < drop.index(f"dseditgroup -o edit -d {macos.USER} -t user admin")
    assert explicit_ssh in grant
    assert "checkmember -m " + macos.USER + " com.apple.access_ssh" not in drop
    assert drop.startswith("set -e\n")


@pytest.mark.parametrize("missing", ["binary", "address"])
def test_guest_install_requires_a_build_and_connection_address(monkeypatch, tmp_path, missing):
    binary = tmp_path / "zoo-guest"
    if missing == "address":
        binary.write_bytes(b"guest build")
    monkeypatch.setattr(macos, "GUEST_BINARY", str(binary))
    monkeypatch.setattr(
        macos,
        "guest_env",
        lambda sandbox_id, remote: (
            {} if missing == "address" else {"ZOO_GUEST_URL": "wss://gateway.test/guest/connect"}
        ),
    )
    bootstrap = Mock()
    monkeypatch.setattr(macos, "guest", bootstrap)
    with pytest.raises(ZooError) as error:
        macos.install_guest("macos:srv:zoo-sb", "sb")
    assert error.value.code == "guest_unavailable"
    bootstrap.assert_not_called()


def test_guest_install_connection_timeout_is_reported(monkeypatch, tmp_path):
    binary = tmp_path / "zoo-guest"
    binary.write_bytes(b"guest build")
    monkeypatch.setattr(macos, "GUEST_BINARY", str(binary))
    monkeypatch.setattr(
        macos, "guest_env", lambda sandbox_id, remote: {"ZOO_GUEST_URL": "wss://gateway.test/guest/connect"}
    )
    monkeypatch.setattr(macos, "guest", Mock(return_value=(0, b"", b"")))
    bootstrap = Mock()
    monkeypatch.setattr(macos, "guest_check", bootstrap)
    monkeypatch.setattr(macos.hub, "for_sandbox", lambda sandbox_id: None)
    with pytest.raises(ZooError, match="guest did not connect") as error:
        macos.install_guest("macos:srv:zoo-sb", "sb", timeout=0)
    assert error.value.code == "guest_unavailable"
    assert bootstrap.call_args.kwargs["root"] is True


@pytest.fixture
def base_bootstrap(monkeypatch):
    state = SimpleNamespace(
        host="host",
        runtime_id=macos.base_id("host"),
        bind=Mock(),
        install=Mock(),
        execution=Mock(return_value=(0, b"", b"")),
        mark=Mock(return_value="ready-version"),
        timeline=Mock(),
    )
    for name in ("bind", "install", "execution", "mark"):
        state.timeline.attach_mock(getattr(state, name), name)
    monkeypatch.setattr(
        macos, "run", lambda server_id, command, timeout=60: (1 if command.startswith("test -f ") else 0, b"", b"")
    )
    monkeypatch.setattr(macos, "running_vms", lambda server_id: {macos.BASE_VM})
    monkeypatch.setattr(macos, "guest", Mock(return_value=(0, b"", b"")))
    monkeypatch.setattr(macos, "guests", {state.runtime_id: Mock()})
    monkeypatch.setattr(macos.hub, "bind", state.bind)
    monkeypatch.setattr(macos, "install_guest", state.install)
    monkeypatch.setattr(macos, "connected", lambda runtime_id, service: SimpleNamespace(run=state.execution))
    monkeypatch.setattr(macos, "mark_base", state.mark)
    return state


def test_base_is_marked_ready_only_after_guest_install_and_exec_probe(base_bootstrap):
    assert macos.base_ready(base_bootstrap.host)
    assert base_bootstrap.timeline.mock_calls == [
        call.bind(base_bootstrap.runtime_id, "base-host"),
        call.install(base_bootstrap.runtime_id, "base-host"),
        call.execution(["true"], timeout=5),
        call.mark(base_bootstrap.host, verified=True),
    ]


@pytest.mark.parametrize("failure", ["install", "execution"])
def test_base_is_not_marked_ready_when_guest_install_or_exec_probe_fails(base_bootstrap, failure):
    getattr(base_bootstrap, failure).side_effect = ZooError("guest_unavailable", "base bootstrap failed")
    assert macos.base_ready(base_bootstrap.host) is False
    base_bootstrap.mark.assert_not_called()
    assert base_bootstrap.runtime_id not in macos.guests


def test_network_policy_keeps_the_guest_reaching_the_api(monkeypatch):
    monkeypatch.setenv("ZOO_API_URL", "https://zoo.example")
    assert macos.guest_rule() == ["pass out quick proto tcp to zoo.example port 443 keep state"]
    monkeypatch.delenv("ZOO_API_URL", raising=False)
    with pytest.raises(ZooError, match="ZOO_API_URL"):
        macos.guest_rule()


@pytest.mark.parametrize("unavailable", ["stopped", "ssh_port", "ssh_authentication"])
def test_base_bootstrap_waits_for_running_vm_and_authenticated_guest_ssh(base_bootstrap, monkeypatch, unavailable):
    if unavailable == "stopped":
        monkeypatch.setattr(macos, "running_vms", lambda _: set())
    elif unavailable == "ssh_port":
        monkeypatch.setattr(macos, "run", lambda *args, **kwargs: (1, b"", b""))
    else:
        monkeypatch.setattr(macos, "guest", Mock(side_effect=ConnectionError("guest SSH not ready")))
    assert macos.base_ready(base_bootstrap.host) is False
    base_bootstrap.install.assert_not_called()
    base_bootstrap.mark.assert_not_called()


def test_prepared_base_marker_avoids_reinstalling_the_guest(base_bootstrap, monkeypatch):
    monkeypatch.setattr(macos, "run", lambda *args, **kwargs: (0, b"", b""))
    assert macos.base_ready(base_bootstrap.host)
    base_bootstrap.bind.assert_not_called()
    base_bootstrap.install.assert_not_called()
    base_bootstrap.execution.assert_not_called()
    base_bootstrap.mark.assert_not_called()


def test_exec_capable_base_is_ready_without_optional_accessibility_permission(base_bootstrap, monkeypatch):
    # Native macOS privacy permission is optional for shell/files and must not block unattended setup.
    monkeypatch.setattr(macos.hub, "for_runtime", lambda _: Agent())
    monkeypatch.setattr(macos, "connected", CONNECTED)
    assert macos.base_ready(base_bootstrap.host)
    base_bootstrap.mark.assert_called_once_with(base_bootstrap.host, verified=True)


@pytest.mark.parametrize("result", [(1, b"", b"permission denied"), (127, b"", b"command missing")])
def test_connected_agent_is_not_ready_when_exec_probe_fails(base_bootstrap, result):
    base_bootstrap.execution.return_value = result
    assert macos.base_ready(base_bootstrap.host) is False
    base_bootstrap.mark.assert_not_called()


def test_base_with_setup_assistant_still_running_is_not_marked_ready(base_bootstrap, monkeypatch):
    monkeypatch.setattr(macos, "desktop_ready", lambda _: False)
    assert macos.base_ready(base_bootstrap.host) is False
    base_bootstrap.bind.assert_not_called()
    base_bootstrap.install.assert_not_called()
    base_bootstrap.execution.assert_not_called()
    base_bootstrap.mark.assert_not_called()
