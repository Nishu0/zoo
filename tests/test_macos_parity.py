"""macOS lifecycle: the 2-VM limit and its queue, base versions, hang recovery, moves through object storage, and
window tools through the guest. Host commands are answered from outputs a real Mac with zoovm gives."""

import json
import re
from types import SimpleNamespace

import pytest

from db.connection import db_manager
from server import jobs as jobs_module
from server import macos, macos_tools, objects, sandbox_api
from server.errors import ZooError
from server.jobs import WAITING, Handler, Wait
from server.sandbox_api import AUTO, CreateSandboxRequest
from tests.conftest import member, present
from tests.test_sandboxes import add_server

SERVER = SimpleNamespace(id="mac", docker_url="node://zoo@mac")
VERSION = b'{"os":"15.6.1","build":"24G90"}'


class Mac:
    """A Mac's shell as zoovm leaves it: `zoovm list` JSON, `zoovm get` exit codes, version.json, the ready marker."""

    def __init__(self, running=(), vms=(), marker="15.6.1-24G90-202610061200"):
        self.running, self.vms, self.marker = set(running), set(vms) | set(running), marker
        self.commands: list[str] = []
        self.fail: str | None = None

    def __call__(self, server_id, command, stdin=None, timeout=60):
        self.commands.append(command)
        if self.fail and self.fail in command:
            return 1, b"", b"boom"
        if command == "zoovm list":
            rows = [{"name": n, "state": "running" if n in self.running else "stopped"} for n in sorted(self.vms)]
            return 0, json.dumps(rows).encode(), b""
        if command.startswith("zoovm get "):
            return (0 if command.split()[2] in self.vms else 1), b"", b""
        if command.startswith("zoovm vnc "):
            return 0, b"vnc://:pw@127.0.0.1:5902\n", b""
        if command.startswith("zoovm version "):
            return 0, VERSION + b"\n", b""
        if command.startswith("cat ") and "zoo-ready" in command:
            return 0, self.marker.encode(), b""
        if "tar -czf" in command:
            return 0, b"part.aa\npart.ab\n", b""
        return 0, b"", b""


@pytest.fixture
def mac(monkeypatch):
    mac = Mac()
    monkeypatch.setattr(macos, "run", mac)
    monkeypatch.setattr(macos, "connect", lambda server_id, url: None)
    monkeypatch.setattr(macos, "base_ready", lambda server_id: True)
    for name in ("wait_for_guest", "set_admin", "write_env", "install_guest"):
        monkeypatch.setattr(macos, name, lambda *a, **k: None)
    monkeypatch.setattr(macos, "boot", lambda server_id, name: "vnc://:pw@127.0.0.1:5901")
    return mac


def test_a_full_mac_makes_the_boot_wait(mac):
    mac.running = mac.vms = {"zoo-a", "zoo-b"}
    with pytest.raises(Wait, match="^Mac full"):
        macos.start("c", SERVER, {})
    assert not any("zoovm clone" in c for c in mac.commands)
    # a VM already running (a retried boot) doesn't count against itself
    mac.running = mac.vms = {"zoo-a", "zoo-c"}
    assert macos.start("c", SERVER, {}) == "macos:mac:zoo-c"


def test_a_clone_records_its_base_version(mac):
    macos.start("c", SERVER, {})
    [clone] = [c for c in mac.commands if "zoovm clone" in c]
    assert clone.startswith(f"zoovm clone {macos.BASE_VM} zoo-c && zoovm set zoo-c")
    assert clone.endswith("printf '%s\\n' 15.6.1-24G90-202610061200 > ~/.zoovm/vms/zoo-c/zoo-base")


def test_a_base_without_a_version_gets_one(mac):
    mac.marker = ""
    version = macos.base_version("mac")
    assert re.fullmatch(r"15\.6\.1-24G90-\d{12}", version)
    assert mac.commands[-1] == f"printf '%s\\n' {version} > ~/.zoovm/vms/{macos.BASE_VM}/zoo-ready"


def test_a_waiting_job_keeps_its_attempts_until_it_waited_too_long(zoo, client, alice, monkeypatch):
    jobs = zoo.sandbox_api.jobs
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    monkeypatch.setitem(jobs.handlers, "move", Handler(lambda job: (_ for _ in ()).throw(Wait("Mac full: x")), 3, (5,)))
    with db_manager.session() as db:
        job = jobs.enqueue(db, sandbox_id, "move")
    jobs.execute(jobs.claim(job))
    with db_manager.session() as db:
        job = present(db.get_job(id=job.id))
    assert (job.state, job.attempts, job.last_error) == ("queued", 0, WAITING + "Mac full: x")
    monkeypatch.setattr(jobs_module, "MAX_WAIT", -60)
    jobs.execute(jobs.claim(job))
    with db_manager.session() as db:
        job = present(db.get_job(id=job.id))
    assert job.state == "failed" and (job.last_error or "").startswith("waited")


def test_macos_placement_queues_or_says_mac_full(zoo, alice, monkeypatch):
    monkeypatch.setattr(zoo.sandbox_api.jobs, "inline", False)
    monkeypatch.setattr(macos, "MAX_VMS", 1)
    user = member()
    server = add_server("alice@example.com", "macos", "m")
    with db_manager.session() as db:
        zoo.sandbox_api.create(CreateSandboxRequest(kind="macos", server_id=server), user, db)
    with db_manager.session() as db:
        assert zoo.sandbox_api.place(AUTO, user, db, "macos", queue=True) == server
        with pytest.raises(sandbox_api.HTTPException) as e:
            zoo.sandbox_api.place(AUTO, user, db, "macos")
    assert e.value.status_code == 409 and e.value.detail.startswith("Mac full")


def test_a_hung_vm_is_found_after_two_quiet_checks(zoo, monkeypatch):
    api, now = zoo.sandbox_api, [1000.0]
    monkeypatch.setattr(sandbox_api, "time", SimpleNamespace(monotonic=lambda: now[0]))
    heartbeat, drawing = [True], [True]
    monkeypatch.setattr(sandbox_api.hub, "heartbeat", lambda sandbox_id: heartbeat[0])
    monkeypatch.setattr(sandbox_api, "responsive", lambda rid: drawing[0])
    vm = SimpleNamespace(id="s", kind="macos", runtime_id="macos:mac:zoo-s")
    assert not api.hung(vm)
    heartbeat[0] = False
    assert not api.hung(vm)  # quiet guest, but the screen still draws
    drawing[0] = False
    now[0] += sandbox_api.HANG_SECONDS / 2
    assert not api.hung(vm)  # first sign: a suspect
    now[0] += sandbox_api.HANG_SECONDS
    assert api.hung(vm)
    assert not api.hung(SimpleNamespace(id="l", kind="desktop", runtime_id="c"))


def test_presigned_urls_match_aws_and_custom_endpoints(monkeypatch):
    url = objects.presign_url(
        "GET",
        "examplebucket.s3.amazonaws.com",
        "/test.txt",
        "us-east-1",
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "20130524T000000Z",
        86400,
    )
    # AWS's published example for query-string signing
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")
    monkeypatch.setenv("ZOO_OBJECT_STORE", "s3://vms/zoo")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.delenv("ZOO_S3_ENDPOINT", raising=False)
    assert objects.location("moves/a") == ("vms.s3.eu-west-1.amazonaws.com", "/zoo/moves/a")
    monkeypatch.setenv("ZOO_S3_ENDPOINT", "http://minio:9000")
    assert objects.location("moves/a") == ("minio:9000", "/vms/zoo/moves/a")


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("ZOO_OBJECT_STORE", "s3://vms/zoo")
    monkeypatch.setenv("ZOO_S3_ENDPOINT", "https://minio.example:9000")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AK")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SK")
    deleted: list[str] = []
    monkeypatch.setattr(objects, "delete", deleted.append)
    return deleted


def test_a_vm_moves_through_object_storage(mac, store, monkeypatch):
    removed = []
    monkeypatch.setattr(macos, "delete", lambda sandbox_id, server: removed.append(server.id))
    macos.move(
        "s", SimpleNamespace(id="a", docker_url="node://zoo@a"), SimpleNamespace(id="b", docker_url="node://zoo@b")
    )
    upload = next(c for c in mac.commands if "curl -fsS --retry 3 -T" in c)
    assert upload.count(" -T ") == 2 and "https://minio.example:9000/vms/zoo/moves/s/part.ab?X-Amz-" in upload
    download = next(c for c in mac.commands if "tar -xzf" in c)
    assert download.startswith("zoovm delete zoo-s && ") and download.endswith("&& zoovm get zoo-s")
    assert store == ["moves/s/part.aa", "moves/s/part.ab"] and removed == ["a"]


def test_a_failed_move_keeps_the_source_and_cleans_up(mac, store, monkeypatch):
    removed = []
    monkeypatch.setattr(macos, "delete", lambda sandbox_id, server: removed.append(server.id))
    mac.fail = "tar -xzf"
    with pytest.raises(RuntimeError):
        macos.move(
            "s", SimpleNamespace(id="a", docker_url="node://zoo@a"), SimpleNamespace(id="b", docker_url="node://zoo@b")
        )
    assert removed == [] and len(store) == 2 and any(c.startswith("rm -rf ~/.zoovm/move-zoo-s") for c in mac.commands)


class WindowGuest:
    def __init__(self):
        self.calls = []

    def has(self, service):
        return service == "windows"

    def call(self, op, args, payload=b"", timeout=600):
        self.calls.append((op, args))
        if args["action"] == "list":
            return {"windows": [{"id": "TextEdit:1", "title": "Untitled", "app": "TextEdit", "pid": 7}]}, b""
        return {}, b""


def test_window_tools_use_the_guest(monkeypatch):
    agent = WindowGuest()
    monkeypatch.setattr(macos.hub, "for_runtime", lambda rid: agent)
    tools = macos_tools.MacWindows
    assert tools.windows_list("r") == [{"id": "TextEdit:1", "title": "Untitled", "app": "TextEdit"}]
    assert tools.window_maximize("r", "TextEdit:1") and tools.window_close("r", "TextEdit:1")
    assert agent.calls[1:] == [
        ("window", {"action": "maximize", "id": "TextEdit:1"}),
        ("window", {"action": "close", "id": "TextEdit:1"}),
    ]
    with pytest.raises(ValueError):
        tools.window_focus("r", "TextEdit")
    monkeypatch.setattr(macos.hub, "for_runtime", lambda rid: None)
    with pytest.raises(ZooError, match="guest agent is not connected"):
        tools.window_minimize("r", "TextEdit:1")
