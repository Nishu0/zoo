from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from server import macos
from server.errors import ZooError


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
        accessibility=Mock(return_value=({}, b"")),
        mark=Mock(return_value="ready-version"),
        timeline=Mock(),
    )
    for name in ("bind", "install", "accessibility", "mark"):
        state.timeline.attach_mock(getattr(state, name), name)
    monkeypatch.setattr(
        macos, "run", lambda server_id, command, timeout=60: (1 if command.startswith("test -f ") else 0, b"", b"")
    )
    monkeypatch.setattr(macos, "running_vms", lambda server_id: {macos.BASE_VM})
    monkeypatch.setattr(macos, "guest", Mock(return_value=(0, b"", b"")))
    monkeypatch.setattr(macos, "guests", {state.runtime_id: Mock()})
    monkeypatch.setattr(macos.hub, "bind", state.bind)
    monkeypatch.setattr(macos, "install_guest", state.install)
    monkeypatch.setattr(macos, "connected", lambda runtime_id, service: SimpleNamespace(call=state.accessibility))
    monkeypatch.setattr(macos, "mark_base", state.mark)
    return state


def test_base_is_marked_ready_only_after_guest_install_and_accessibility_probe(base_bootstrap):
    assert macos.base_ready(base_bootstrap.host)
    assert base_bootstrap.timeline.mock_calls == [
        call.bind(base_bootstrap.runtime_id, "base-host"),
        call.install(base_bootstrap.runtime_id, "base-host"),
        call.accessibility("a11y", {"max_nodes": 1}, timeout=5),
        call.mark(base_bootstrap.host),
    ]


@pytest.mark.parametrize("failure", ["install", "accessibility"])
def test_base_is_not_marked_ready_when_guest_or_accessibility_setup_fails(base_bootstrap, failure):
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
