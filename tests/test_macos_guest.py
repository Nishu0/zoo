import pytest

from server import guest, macos
from server.errors import ZooError


class Agent:
    def __init__(self):
        self.calls = []

    def has(self, service):
        return service == "exec"

    def exec_run(self, argv, stdin=b"", timeout=600):
        self.calls.append((argv, stdin))
        return 0, b"out", b""


def test_guest_runs_through_the_agent_but_root_and_setup_stay_on_ssh(monkeypatch):
    agent, ssh = Agent(), []
    monkeypatch.setattr(macos.hub, "for_runtime", lambda rid: agent)
    monkeypatch.setattr(macos, "guest_client", lambda rid: None)
    monkeypatch.setattr(macos, "root_client", lambda rid: None)
    monkeypatch.setattr(macos, "execute", lambda client, command, stdin, timeout: ssh.append(command) or (0, b"", b""))
    rid = "macos:srv:zoo-sb"
    assert macos.guest(rid, "cat > f", stdin=b"data") == (0, b"out", b"")
    assert agent.calls == [(["/bin/sh", "-c", f"cd {macos.HOME} && cat > f"], b"data")]
    macos.guest(rid, "pfctl -e", root=True)
    macos.guest(rid, "shasum f", ssh=True)
    assert len(agent.calls) == 1 and len(ssh) == 2 and ssh[0].startswith("sudo -n")
    # a base VM that lets the API log in as root needs no sudo from the guest user
    monkeypatch.setattr(macos, "root_client", lambda rid: "root")
    macos.guest(rid, "pfctl -e", root=True)
    assert ssh[-1] == f"cd {macos.HOME} && pfctl -e"
    # tools never go over SSH: without the guest they fail
    monkeypatch.setattr(macos.hub, "for_runtime", lambda rid: None)
    with pytest.raises(ZooError, match="guest agent is not connected"):
        macos.guest(rid, "true")


def test_admin_rights_follow_the_sandbox(monkeypatch):
    scripts = []
    monkeypatch.setattr(macos, "guest_check", lambda rid, script, root=False: scripts.append((script, root)))
    monkeypatch.setattr(macos, "root_client", lambda rid: None)
    macos.set_admin("macos:srv:zoo-sb", False)
    assert scripts == []  # an older base VM keeps its admin user rather than lose root
    monkeypatch.setattr(macos, "root_client", lambda rid: "root")
    macos.set_admin("macos:srv:zoo-sb", False)
    macos.set_admin("macos:srv:zoo-sb", True)
    (drop, root1), (grant, root2) = scripts
    assert root1 and root2
    assert "rm -f /etc/sudoers.d/zoo" in drop and drop.endswith(
        f"! dseditgroup -o checkmember -m {macos.USER} admin >/dev/null"
    )
    assert "NOPASSWD" in grant and "-a " + macos.USER in grant


def test_network_policy_keeps_the_guest_reaching_the_api(monkeypatch):
    monkeypatch.setattr(guest, "REMOTE_URL", "wss://zoo.example/guest")
    assert macos.guest_rule() == ["pass out quick proto tcp to zoo.example port 443 keep state"]
    monkeypatch.setattr(guest, "REMOTE_URL", "")
    assert macos.guest_rule() == []
