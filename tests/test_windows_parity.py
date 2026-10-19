"""Windows lifecycle: versioned helper upload, template versions, hang recovery and moves through object storage.
Host commands are answered the way windows/zoovm.ps1 answers them."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from apps.server import objects, sandbox_api, windows
from apps.server.errors import ZooError
from apps.server.guest import token

SOURCE = SimpleNamespace(id="winsrc-1234", docker_url="node://zoo@winsrc-1234")
TARGET = SimpleNamespace(id="wintgt", docker_url="node://zoo@wintgt")
VERSION = "windows-26100.4061-202610071200"


class Host:
    """Windows hosts as the API sees them: zoovm's answers per server, and every script it ran."""

    def __init__(self):
        self.zoovm: list[tuple[str, tuple]] = []
        self.scripts: list[tuple[str, str]] = []
        self.vms = {"winsrc-1234": {"zoo-s"}, "wintgt": set()}
        self.templates = {"wintgt": set()}
        self.fail: str | None = None

    def run_zoovm(self, server_id, *args, timeout=120):
        self.zoovm.append((server_id, args))
        command, name = args[0], args[1] if len(args) > 1 else ""
        if self.fail and self.fail == command:
            return 1, b"", b"zoovm: boom"
        if command == "list":
            return 0, json.dumps([{"name": n, "state": "stopped"} for n in self.vms[server_id]]).encode(), b""
        if command == "get":
            return (0 if name in self.vms[server_id] else 1), b"", b""
        if command == "base":
            return 0, (VERSION + "\r\n").encode(), b""
        if command == "chain":
            rows = [
                {"name": "disk.vhdx", "path": r"C:\Users\zoo\.zoovm\vms\zoo-s\disk.vhdx", "size": 3 << 30},
                {"name": "zoo-windows-base-20261007120000.vhdx", "path": r"C:\t\b2.vhdx", "size": 100},
                {"name": "zoo-windows-base-20261001120000.vhdx", "path": r"C:\t\b1.vhdx", "size": 20 << 30},
            ]
            return 0, json.dumps(rows).encode(), b""
        if command == "clone":
            self.vms[server_id].add(list(args)[2])
        if command == "delete":
            self.vms[server_id].discard(name)
        if command == "adopt":
            self.vms[server_id].add(name)
        return 0, b"", b""

    def check(self, server_id, script, data=b"", timeout=60):
        self.scripts.append((server_id, script))
        if self.fail and self.fail in script:
            raise RuntimeError("boom")
        if "Test-Path -LiteralPath (Join-Path" in script:
            return "\n".join(sorted(self.templates[server_id]))
        return ""


@pytest.fixture
def host(monkeypatch):
    host = Host()
    monkeypatch.setattr(windows, "zoovm", host.run_zoovm)
    monkeypatch.setattr(windows, "check", host.check)
    monkeypatch.setattr(windows, "connect", lambda server_id, url: None)
    return host


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("ZOO_OBJECT_STORE", "s3://vms/zoo")
    monkeypatch.setenv("ZOO_S3_ENDPOINT", "https://minio.example:9000")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AK")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SK")
    deleted: list[str] = []
    monkeypatch.setattr(objects, "delete", deleted.append)
    return deleted


def test_helpers_upload_only_what_differs_and_are_checked(monkeypatch):
    files = windows.helper_files()
    on_host = {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    on_host["setup.ps1"] = "0" * 64
    written = {}

    def write(server_id, name, data):
        written[name] = data
        if name in files:
            on_host[name] = hashlib.sha256(data).hexdigest()

    monkeypatch.setattr(windows, "write_host_file", write)
    monkeypatch.setattr(windows, "host_helper_hashes", lambda server_id: dict(on_host))
    windows.uploaded.discard("srv")
    windows.upload_helpers("srv")
    assert sorted(written) == ["helpers.json", "setup.ps1"]
    manifest = json.loads(written["helpers.json"])
    assert (
        manifest["version"] == windows.helpers_version(files) and manifest["files"]["zoovm.ps1"] == on_host["zoovm.ps1"]
    )

    # a write that didn't take (a full disk, a locked file) fails the connection instead of running a stale script
    windows.uploaded.discard("srv")
    on_host["zoovm.ps1"] = "1" * 64
    monkeypatch.setattr(windows, "write_host_file", lambda server_id, name, data: None)
    with pytest.raises(RuntimeError, match="zoovm.ps1"):
        windows.upload_helpers("srv")
    windows.uploaded.discard("srv")


def test_a_clone_records_its_template_version(host, monkeypatch):
    monkeypatch.setenv("ZOO_API_URL", "http://10.0.0.5:8000")
    for name in ("push_identity", "wait_for_guest", "update_guest", "set_vnc_password", "write_env"):
        monkeypatch.setattr(windows, name, lambda *a, **k: None)
    monkeypatch.setattr(windows.hub, "bind", lambda rid, sandbox_id: None)
    monkeypatch.setattr(windows, "guest_ip", lambda rid: "172.20.0.5")
    rid = windows.start("new", SOURCE, {})
    assert (
        "winsrc-1234",
        ("clone", windows.BASE_VM, "zoo-new", "-Cpu", str(windows.CPUS), "-Memory", str(windows.MEMORY_MB)),
    ) in host.zoovm
    assert windows.base_of(rid) == VERSION


def test_a_hung_windows_vm_is_found_after_two_quiet_checks(zoo, monkeypatch):
    api, now = zoo.sandbox_api, [1000.0]
    monkeypatch.setattr(sandbox_api, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(sandbox_api.hub, "heartbeat", lambda sandbox_id: False)
    drawing = [True]
    monkeypatch.setattr(sandbox_api, "responsive", lambda rid: drawing[0])
    vm = SimpleNamespace(id="w", kind="windows", runtime_id="windows:srv:zoo-w")
    assert not api.hung(vm)
    drawing[0] = False
    now[0] += sandbox_api.HANG_SECONDS / 2
    assert not api.hung(vm)
    now[0] += sandbox_api.HANG_SECONDS
    assert api.hung(vm)


def test_a_vm_moves_with_the_templates_the_target_lacks(host, store, monkeypatch):
    removed = []
    monkeypatch.setattr(windows, "delete", lambda sandbox_id, server: removed.append(server.id))
    host.templates["wintgt"] = {"moved-winsrc-1-zoo-windows-base-20261001120000.vhdx"}
    windows.move("s", SOURCE, TARGET)

    uploads = [s for sid, s in host.scripts if sid == SOURCE.id and "curl.exe -fsS --retry 3 -T" in s]
    downloads = [s for sid, s in host.scripts if sid == TARGET.id and "curl.exe -fsS --retry 3 -o" in s]
    # the disk in 3 parts of 1 GB, and only the newer template: the target has the older one from an earlier move
    assert len(uploads) == len(downloads) == 2
    assert uploads[0].count("https://minio.example:9000/vms/zoo/moves/s/0.") == 3 and "disk.vhdx" in downloads[0]
    assert "moved-winsrc-1-zoo-windows-base-20261007120000.vhdx" in downloads[1]
    assert any("zoo-base" in s and VERSION in s for sid, s in host.scripts if sid == TARGET.id)
    adopt = next(a for sid, a in host.zoovm if sid == TARGET.id and a[0] == "adopt")
    assert adopt[:4] == (
        "adopt",
        "zoo-s",
        "-Parents",
        "moved-winsrc-1-zoo-windows-base-20261007120000.vhdx,moved-winsrc-1-zoo-windows-base-20261001120000.vhdx",
    )
    assert removed == [SOURCE.id] and store == ["moves/s/0.0000", "moves/s/0.0001", "moves/s/0.0002", "moves/s/1.0000"]


def test_a_failed_move_keeps_the_source_and_cleans_up(host, store, monkeypatch):
    removed = []
    monkeypatch.setattr(windows, "delete", lambda sandbox_id, server: removed.append(server.id))
    host.fail = "adopt"
    with pytest.raises(RuntimeError):
        windows.move("s", SOURCE, TARGET)
    assert removed == [] and len(store) == 3 + 1 + 20  # every part of the disk and both templates is cleaned up
    assert [a[0] for sid, a in host.zoovm if sid == TARGET.id][-1] == "delete"


def test_moved_templates_keep_their_first_name():
    assert windows.moved_name("abcdefghij", "zoo-windows-base-1.vhdx") == "moved-abcdefgh-zoo-windows-base-1.vhdx"
    assert (
        windows.moved_name("other", "moved-abcdefgh-zoo-windows-base-1.vhdx")
        == "moved-abcdefgh-zoo-windows-base-1.vhdx"
    )


def test_windows_needs_a_guest_url(host, monkeypatch):
    monkeypatch.delenv("ZOO_API_URL", raising=False)
    with pytest.raises(ZooError, match="ZOO_API_URL"):
        windows.start("new", SOURCE, {})


def test_the_identity_goes_in_through_hyper_v_and_leaves_no_copy_on_the_host(host, monkeypatch):
    monkeypatch.setenv("ZOO_API_URL", "http://10.0.0.5:8000")
    monkeypatch.setattr(windows, "host_home", lambda server_id: r"C:\Users\ci")
    monkeypatch.setattr(windows, "time", SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda s: None))
    written, removed, attempts = {}, [], []
    monkeypatch.setattr(windows, "write_host_file", lambda server_id, name, data: written.update({name: data}))
    monkeypatch.setattr(windows, "run", lambda server_id, script, data=b"", timeout=60: removed.append(script))

    def push(server_id, name, source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3:
            raise RuntimeError("zoovm: the guest's integration services aren't running yet")

    monkeypatch.setattr(windows, "push", push)
    windows.push_identity("winsrc-1234", "zoo-s", "s")
    env = written["env-zoo-s.env"].decode()
    assert "ZOO_SANDBOX_ID=s\n" in env and f"ZOO_GUEST_TOKEN={token('s')}" in env
    assert attempts[-1] == (r"C:\Users\ci\.zoovm\env-zoo-s.env", windows.GUEST_ENV) and len(attempts) == 3
    assert "env-zoo-s.env" in removed[0]


class Guest:
    def __init__(self, hash_):
        self.hash, self.calls = hash_, []

    def has(self, service):
        return True

    def run(self, argv, stdin=b"", timeout=600):
        # the update waits for the replacement process, which reports its parent as the old guest's helper
        if b"ParentProcessId" in stdin:
            return 0, b"4242", b""
        return 0, self.hash.encode(), b""

    def call(self, op, args, payload=b"", timeout=600):
        self.calls.append(op)
        return {"pid": 4242}, b""


def test_an_outdated_guest_updates_itself_and_reconnects(monkeypatch, tmp_path):
    build = tmp_path / "zoo-guest.exe"
    build.write_bytes(b"new build")
    monkeypatch.setattr(windows, "GUEST_BINARY", str(build))
    old, new = Guest("ABC"), Guest(hashlib.sha256(b"new build").hexdigest().upper())
    current = [old]
    monkeypatch.setattr(windows.hub, "for_runtime", lambda rid: current[0])
    monkeypatch.setattr(windows.hub, "for_sandbox", lambda sandbox_id: current[0])
    monkeypatch.setattr(windows, "upload_vm_guest", lambda server_id: r"C:\h\.zoovm\bin\zoo-guest-vm.exe")
    pushed = []
    monkeypatch.setattr(windows, "push", lambda server_id, name, source, destination: pushed.append(destination))
    monkeypatch.setattr(windows.time, "sleep", lambda s: current.__setitem__(0, new))
    windows.update_guest("windows:srv:zoo-s", "s")
    assert pushed == [windows.GUEST_EXE + ".new"] and old.calls == ["update"]
    # the replacement is the new build
    current[0] = new
    pushed.clear()
    windows.update_guest("windows:srv:zoo-s", "s")  # already current
    assert pushed == []
