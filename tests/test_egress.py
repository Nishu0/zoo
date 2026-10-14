"""Host-side network and app policy (server/egress.py, guest/egress.go), the hardening around it, and KMS wrapping."""

import io
import json
import tarfile

import httpx
import pytest

from server import docker, egress, kms, macos, windows
from tests.conftest import runtime_of


class Container:
    def __init__(self):
        self.id = "c0ffee"
        self.attrs = {"NetworkSettings": {"Networks": {"zoo": {"IPAddress": "172.18.0.5", "GlobalIPv6Address": ""}}}}


def test_docker_policy_for_the_hosts_egress_daemon(monkeypatch):
    monkeypatch.setattr(docker, "container", lambda cid: Container())
    monkeypatch.setattr(docker, "guest_endpoint", lambda cid: ("api", 8000))
    monkeypatch.setattr(egress, "addresses", lambda host: ["172.18.0.2"])
    name, data = docker.network_policy("c0ffee", "deny", True, [("domain", "github.com", "allow")])
    with tarfile.open(fileobj=io.BytesIO(docker.tar_file(name, data))) as tar:
        assert tar.getmember("c0ffee.json").mode == 0o600
    assert json.loads(data) == {
        "id": "c0ffee",
        "addrs": ["172.18.0.5"],
        "default": "deny",
        "dns": True,
        "rules": [{"type": "domain", "value": "github.com", "effect": "allow"}],
        "always": [{"ip": "172.18.0.2", "port": 8000}],
    }


def test_rule_values_are_checked_before_they_reach_a_host(client, alice, sandbox):
    url = f"/sandboxes/{sandbox['id']}/network/rules"
    for rule_type, value in [("cidr", "10.0.0.0/33"), ("ip", "example.com"), ("domain", "bad domain"), ("domain", "*")]:
        res = client.post(url, json={"rule_type": rule_type, "value": value, "effect": "deny"}, headers=alice)
        assert res.status_code == 422, (rule_type, value)
    for rule_type, value in [("cidr", "10.1.0.0/16"), ("ip", "2001:db8::1"), ("domain", "*.Example.com")]:
        res = client.post(url, json={"rule_type": rule_type, "value": value, "effect": "allow"}, headers=alice)
        assert res.status_code == 201, res.text
    assert {r["value"] for r in res.json()["rules"]} == {"10.1.0.0/16", "2001:db8::1", "*.example.com"}


def test_proxied_only_when_names_decide():
    assert egress.proxied("deny", [])
    assert egress.proxied("allow", [("domain", "x.com", "deny")])
    assert not egress.proxied("allow", [("cidr", "10.0.0.0/8", "deny")])
    assert egress.file_name("macos:srv:zoo-1") == "macos_srv_zoo-1.json"


@pytest.mark.parametrize("backend", [macos, windows])
def test_vm_hosts_upload_the_standalone_egress_build(monkeypatch, tmp_path, backend):
    binary = tmp_path / "zoo-egress"
    binary.write_bytes(b"standalone egress build")
    monkeypatch.setattr(backend, "EGRESS_BUILD", str(binary))
    monkeypatch.setattr(backend, "GUEST_BINARY", str(tmp_path / "missing-guest"))
    monkeypatch.setattr(backend, "run", lambda *args, **kwargs: (0, b"", b""))
    uploaded = []

    def check(server_id, script, *args, **kwargs):
        uploaded.append(kwargs.get("stdin", args[0] if args else b""))
        return ""

    monkeypatch.setattr(backend, "check", check)
    backend.ensure_egress("host")
    assert b"standalone egress build" in uploaded


def test_windows_vm_reaches_only_the_host_proxy(monkeypatch):
    host, guest = [], []
    monkeypatch.setattr(windows, "ensure_egress", lambda server_id: None)
    monkeypatch.setattr(windows, "guest_ip", lambda rid: "172.20.0.9")
    monkeypatch.setattr(windows, "guest_endpoint", lambda: ("zoo.example", 443))
    monkeypatch.setattr(egress, "addresses", lambda h: ["203.0.113.7"])
    monkeypatch.setattr(
        windows, "check", lambda sid, script, data=b"", timeout=60: host.append((script, data)) or "x\n172.20.0.1\n"
    )
    monkeypatch.setattr(
        windows, "guest_check", lambda rid, script, data=b"", timeout=60, ssh=False: guest.append(script)
    )
    windows.apply_network(
        "windows:srv:zoo-sb", "allow", False, [("domain", "evil.com", "deny"), ("cidr", "9.9.9.0/24", "deny")]
    )
    ((script, data),) = host
    assert json.loads(data)["addrs"] == ["172.20.0.9"]
    assert "if ($true) {" in script and "if ($false) {" in script  # filtered, DNS off
    assert "'203.0.113.7|443'" in script and "foreach ($c in @('9.9.9.0/24'))" in script
    assert "foreach ($c in @())" in script  # no allow rules: an empty list, not one $null
    assert "$proxy = '172.20.0.1:15128'" in guest[0] and "Rule proxy Allow '172.20.0.1' TCP 15128" in guest[0]
    assert "-DefaultOutboundAction Block" in guest[0] and "evil.com" not in guest[0]


def test_store_apps_are_denied_with_applocker(monkeypatch):
    scripts = []
    monkeypatch.setattr(windows, "guest_check", lambda rid, script, ssh=False: scripts.append(script))
    windows.apply_apps(
        "windows:srv:zoo-sb", {"Microsoft.WindowsCalculator_8wekyb3d8bbwe!App": "deny", "notepad.exe": "allow"}
    )
    assert "@('Microsoft.WindowsCalculator_8wekyb3d8bbwe')" in scripts[0] and "Set-AppLockerPolicy" in scripts[0]
    assert "Remove-ItemProperty" in scripts[0]


def test_macos_policy_goes_to_the_mac_and_the_vm(monkeypatch):
    host, guest = [], []
    monkeypatch.setattr(macos, "ensure_egress", lambda server_id: None)
    monkeypatch.setattr(macos, "running_vms", lambda server_id: {"zoo-other"})
    monkeypatch.setattr(macos, "guest_endpoint", lambda: None)
    monkeypatch.setattr(
        macos, "check", lambda sid, command, stdin=None, timeout=60: host.append((command, stdin)) or "192.168.64.5\n"
    )
    monkeypatch.setattr(
        macos, "guest_check", lambda rid, script, stdin=None, root=False: guest.append((script, stdin, root))
    )
    macos.apply_network(
        "macos:srv:zoo-sb", "deny", True, [("domain", "github.com", "allow"), ("cidr", "10.0.0.0/8", "deny")]
    )
    command, data = host[-1]
    assert json.loads(data)["addrs"] == ["192.168.64.5"] and "zoo-sb.json" in command and "zoo-other.json" in command
    _, rules, root = guest[0]
    assert root and b"block return out quick to 10.0.0.0/8" in rules and b"github.com" not in rules
    assert rules.rstrip().endswith(b"block return out all")


def test_profile_apps_per_os(client, alice):
    assert "safari" in client.get("/profile-apps?platform=macos", headers=alice).json()
    assert "edge" in client.get("/profile-apps?platform=windows", headers=alice).json()
    assert client.get("/profile-apps?platform=beos", headers=alice).status_code == 422


def test_admin_is_a_macos_option(client, alice):
    res = client.post("/sandboxes", json={"kind": "desktop", "admin": True}, headers=alice)
    assert res.status_code == 400 and "macOS" in res.json()["error"]["message"]


def test_local_wrapping_and_aws_signing():
    assert kms.unwrap(kms.wrap(b"data-key")) == b"data-key"
    assert kms.aws_region("arn:aws:kms:eu-west-1:123:key/abc") == "eu-west-1"
    # AWS's published Signature Version 4 example (get-vanilla)
    header = kms.sigv4(
        "GET",
        "example.amazonaws.com",
        "/",
        {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"},
        b"",
        "us-east-1",
        "service",
        "AKIDEXAMPLE",
        "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        "20150830T123600Z",
    )
    assert header.endswith("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_vault_transit_wraps_data_keys(monkeypatch):
    calls = []

    def post(url, json, headers, timeout):
        calls.append((url, headers["X-Vault-Token"]))
        if url.endswith("/encrypt/zoo"):
            return httpx.Response(200, json={"data": {"ciphertext": "vault:v1:" + json["plaintext"]}})
        return httpx.Response(200, json={"data": {"plaintext": json["ciphertext"].removeprefix("vault:v1:")}})

    monkeypatch.setenv("ZOO_KMS", "vault:transit/zoo")
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example/")
    monkeypatch.setenv("VAULT_TOKEN", "t0k")
    monkeypatch.setattr(kms.httpx, "post", post)
    wrapped = kms.wrap(b"data-key")
    assert wrapped.startswith("vault:transit/zoo:vault:v1:")
    assert kms.unwrap(wrapped) == b"data-key"
    assert calls == [
        ("https://vault.example/v1/transit/encrypt/zoo", "t0k"),
        ("https://vault.example/v1/transit/decrypt/zoo", "t0k"),
    ]
    monkeypatch.setenv("ZOO_KMS", "nope:x")
    with pytest.raises(RuntimeError):
        kms.wrap(b"k")


def test_captured_profiles_record_their_os(client, alice, sandbox, fake):
    fake.containers[runtime_of(sandbox["id"])].dirs["/home/zoo/.mozilla"] = b"tar"
    res = client.post(f"/sandboxes/{sandbox['id']}/profiles", json={"name": "p", "app": "firefox"}, headers=alice)
    assert res.status_code == 201 and res.json()["platform"] == "linux"
    res = client.post(f"/sandboxes/{sandbox['id']}/profiles", json={"name": "p", "app": "safari"}, headers=alice)
    assert res.status_code == 422
