"""zoo-node: joining with a one-time token, the mTLS Connect stream (reports, tunnels, updates, renewal), placing
sandboxes by the free memory nodes report, and converting SSH servers in place. The node side here is a Python
client speaking the same protocol as node/ (whose own tests cover the Go side)."""

import base64
import datetime
import hashlib
import json
import os
import queue
import socket
import threading
from types import SimpleNamespace
from typing import cast

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from db.connection import db_manager
from db.generated.models import Server
from server import docker, nodes, nodes_api, ssh
from server.nodepb import node_pb2 as pb
from server.nodepb import node_pb2_grpc as pb_grpc
from tests.conftest import STORE, member, present, sql
from tests.test_sandboxes import add_server


@pytest.fixture(autouse=True)
def fresh_nodes(monkeypatch, tmp_path):
    # each test has its own database, so its own CA and streams
    monkeypatch.setattr(nodes, "_authority", None)
    monkeypatch.setattr(nodes, "hub", nodes.Hub())
    monkeypatch.setattr(ssh, "KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    yield
    for session in list(nodes.hub.sessions.values()):
        nodes.hub.detach(session)


def csr() -> tuple[ec.EllipticCurvePrivateKey, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "zoo-node")]))
        .sign(key, hashes.SHA256())
    )
    return key, request.public_bytes(serialization.Encoding.PEM).decode()


def same_key(a, b) -> bool:
    spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    return a.public_bytes(*spki) == b.public_bytes(*spki)


def new_token(client, headers, name="edge-1", platform="linux") -> dict:
    res = client.post(f"/nodes/tokens?platform={platform}", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def join(client, token: str, os_name="linux", **extra):
    key, request = csr()
    res = client.post(
        "/nodes/join",
        json={"token": token, "csr": request, "hostname": "edge", "os": os_name, "arch": "amd64", **extra},
    )
    return key, res


def test_a_node_joins_once_with_its_token_and_becomes_a_server(client, alice):
    made = new_token(client, alice)
    body = nodes.decode_token(made["token"])
    assert body["u"] == "http://testserver" and body["f"] == nodes.authority().fingerprint
    assert made["join_command"] == f"sudo zoo-node join '{made['token']}' --service"
    assert made["install_command"].endswith(f"--token '{made['token']}'")

    key, res = join(client, made["token"])
    assert res.status_code == 200, res.text
    joined = res.json()
    cert = x509.load_pem_x509_certificate(joined["certificate"].encode())
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == joined["node_id"]
    assert same_key(cert.public_key(), key.public_key())
    assert joined["ca"] == nodes.authority().pem.decode() and joined["endpoints"] == nodes.endpoints(
        "http://testserver"
    )

    servers = client.get("/servers", headers=alice).json()
    assert [(s["id"], s["name"], s["docker_url"], s["platform"]) for s in servers] == [
        (joined["server_id"], "edge-1", f"node://{joined['server_id']}", "linux")
    ]
    assert servers[0]["node"]["id"] == joined["node_id"] and servers[0]["node"]["connected"] is False

    # one time only
    _, again = join(client, made["token"])
    assert again.status_code == 401


def test_join_refuses_bad_expired_and_foreign_tokens(client, alice):
    assert join(client, "zn1.garbage")[1].status_code == 401
    assert join(client, "not-a-token")[1].status_code == 401
    made = new_token(client, alice)
    sql("UPDATE node_tokens SET expires_at = now() - interval '1 minute'")
    assert join(client, made["token"])[1].status_code == 401
    # a token only names its own secret: a forged one with another secret gets nowhere
    body = nodes.decode_token(new_token(client, alice)["token"])
    forged = "zn1." + base64.urlsafe_b64encode(json.dumps({**body, "s": "guess"}).encode()).decode()
    assert join(client, forged)[1].status_code == 401
    # a bad CSR doesn't leave a server behind
    res = client.post(
        "/nodes/join",
        json={"token": new_token(client, alice)["token"], "csr": "nope", "os": "linux", "arch": "amd64"},
    )
    assert res.status_code == 422
    assert client.get("/servers", headers=alice).json() == []


def test_mac_and_windows_nodes_bring_their_ssh_account_and_host_key(client, alice):
    made = new_token(client, alice, platform="macos")
    assert "--token" in made["install_command"] and made["join_command"].startswith("zoo-node join")
    _, res = join(client, made["token"], "darwin")
    assert res.status_code == 422

    host_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
    _, res = join(client, new_token(client, alice)["token"], "darwin", ssh_user="zoo", ssh_host_key=host_key)
    assert res.status_code == 200, res.text
    server_id = res.json()["server_id"]
    server = next(s for s in client.get("/servers", headers=alice).json() if s["id"] == server_id)
    assert server["docker_url"] == f"node://zoo@{server_id}" and server["platform"] == "macos"
    # the API checks the host key under the server's id when it reaches the Mac through the node
    with open(ssh.KNOWN_HOSTS) as f:
        assert f.read().splitlines() == [f"{server_id} {host_key}"]

    windows = new_token(client, alice, platform="windows")
    assert windows["install_command"].endswith(f" -Token '{windows['token']}'\"")


def test_a_migration_token_links_the_node_to_the_existing_server(client, alice):
    server_id = add_server("alice@example.com")
    with db_manager.session() as db:
        user = member()
    token, _ = nodes.create_token(user.id, user.workspace_id, "box", "http://testserver", server_id=server_id)
    _, res = join(client, token)
    assert res.status_code == 200, res.text
    first = res.json()
    assert first["server_id"] == server_id
    servers = client.get("/servers", headers=alice).json()
    # the SSH URL stays: it is the fallback while the node is offline
    assert [(s["id"], s["docker_url"]) for s in servers] == [(server_id, "ssh://zoo@box")]

    # joining again replaces the node, and with it the certificate
    token, _ = nodes.create_token(user.id, user.workspace_id, "box", "http://testserver", server_id=server_id)
    second = join(client, token)[1].json()
    with db_manager.session() as db:
        node = present(db.get_node_by_server(server_id=server_id))
        assert db.get_node(id=first["node_id"]) is None
    assert node.id == second["node_id"]

    # a migration token for a Linux server can't be used by a Mac
    token, _ = nodes.create_token(user.id, user.workspace_id, "box", "http://testserver", server_id=server_id)
    host_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
    assert join(client, token, "darwin", ssh_user="zoo", ssh_host_key=host_key)[1].status_code == 409


# --- the stream ---


class NodeClient:
    """Plays zoo-node over a real mTLS gRPC connection."""

    def __init__(self, port: int, key, certificate: str):
        pem_key = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        creds = grpc.ssl_channel_credentials(
            root_certificates=nodes.authority().pem, private_key=pem_key, certificate_chain=certificate.encode()
        )
        self.channel = grpc.secure_channel(
            f"127.0.0.1:{port}", creds, options=[("grpc.ssl_target_name_override", "localhost")]
        )
        self.outbox: queue.Queue = queue.Queue()
        self.inbox: queue.Queue = queue.Queue()
        self.error: grpc.RpcError | None = None
        self.calls = pb_grpc.NodeStub(self.channel).Connect(iter(self.outbox.get, None))
        threading.Thread(target=self.receive, daemon=True).start()

    def receive(self):
        try:
            for message in self.calls:
                self.inbox.put(message)
        except grpc.RpcError as e:
            self.error = e
        self.inbox.put(None)

    def send(self, **body):
        self.outbox.put(pb.NodeMessage(**body))

    def next(self, kind: str | None = None, timeout: float = 10):
        while True:
            message = self.inbox.get(timeout=timeout)
            if message is None:
                raise ConnectionError(f"stream ended: {self.error}")
            if kind is None or message.WhichOneof("body") == kind:
                return message

    def hello(self, version="dev", targets=("docker",), drivers=(("kata", True), ("runc", True))):
        self.send(
            hello=pb.Hello(
                version=version,
                os="linux",
                arch="amd64",
                hostname="edge",
                targets=list(targets),
                drivers=[pb.Driver(name=n, available=a) for n, a in drivers],
            )
        )
        return self.next("welcome").welcome

    def close(self):
        self.outbox.put(None)
        self.channel.close()


@pytest.fixture
def endpoint():
    server, port = nodes.serve(port=0, bind="127.0.0.1")
    yield port
    nodes.stop(server)


@pytest.fixture
def joined(client, alice):
    made = new_token(client, alice)
    key, res = join(client, made["token"])
    assert res.status_code == 200, res.text
    return SimpleNamespace(key=key, **res.json())


def connect(endpoint, joined) -> NodeClient:
    return NodeClient(endpoint, joined.key, joined.certificate)


def test_a_connected_node_reports_and_its_server_runs_linux(client, alice, endpoint, joined):
    node = connect(endpoint, joined)
    welcome = node.hello(version="1.2.3")
    assert welcome.api_version == nodes.VERSION and welcome.status_seconds == nodes.STATUS_SECONDS
    assert nodes.wait_connected(joined.server_id, 5)
    assert nodes.hub.targets(joined.server_id) == {"docker"}

    node.send(
        status=pb.Status(
            cpus=8,
            memory_total=32 << 30,
            memory_available=20 << 30,
            disk_total=500 << 30,
            disk_free=300 << 30,
            load=0.25,
            sandboxes=["a", "b"],
            checks=[pb.Check(name="kata", ok=True, detail="Docker 27"), pb.Check(name="disk", ok=True)],
        )
    )
    deadline, server = 50, {}
    while deadline:
        server = client.get("/servers", headers=alice).json()[0]
        if server["node"]["memory_available"]:
            break
        threading.Event().wait(0.1)
        deadline -= 1
    info = server["node"]
    assert info["connected"] and info["version"] == "1.2.3" and info["cpus"] == 8 and info["sandboxes"] == 2
    assert info["memory_available"] == 20 << 30 and info["checks"][0] == {
        "name": "kata",
        "ok": True,
        "detail": "Docker 27",
    }
    assert info["drivers"] == [
        {"name": "kata", "available": True, "detail": ""},
        {"name": "runc", "available": True, "detail": ""},
    ]
    assert server["capabilities"] == ["linux"]
    assert nodes.free_memory([joined.server_id]) == {joined.server_id: 20 << 30}
    node.close()


def test_a_node_without_docker_can_run_nothing_until_it_has_one(client, alice, endpoint, joined):
    node = connect(endpoint, joined)
    node.hello(targets=(), drivers=(("kata", False), ("runc", False)))
    assert client.get("/servers", headers=alice).json()[0]["capabilities"] == []
    node.close()


def test_tunnels_carry_bytes_both_ways(endpoint, joined):
    node = connect(endpoint, joined)
    node.hello()
    assert nodes.wait_connected(joined.server_id, 5)

    sock = nodes.hub.open(joined.server_id, "docker")
    opened = node.next("open").open
    assert opened.target == "docker"
    sock.sendall(b"GET /_ping HTTP/1.1\r\n\r\n")
    data = node.next("data").data
    assert data.id == opened.id and data.data == b"GET /_ping HTTP/1.1\r\n\r\n"
    node.send(data=pb.TunnelData(id=opened.id, data=b"HTTP/1.1 200 OK\r\n\r\nOK"))
    sock.settimeout(5)
    assert sock.recv(100) == b"HTTP/1.1 200 OK\r\n\r\nOK"
    # the node closes it: the API side reads EOF
    node.send(close=pb.TunnelClose(id=opened.id))
    assert sock.recv(100) == b""
    sock.close()

    # the API closes one: the node is told
    sock = nodes.hub.open(joined.server_id, "docker")
    second = node.next("open").open
    sock.close()
    assert node.next("close").close.id == second.id

    # targets the node didn't offer aren't opened
    with pytest.raises(nodes.Closed):
        nodes.hub.open(joined.server_id, "ssh")
    node.close()


def test_docker_reaches_the_host_through_the_node(endpoint, joined, fake):
    node = connect(endpoint, joined)
    node.hello()
    assert nodes.wait_connected(joined.server_id, 5)
    url = present(nodes.docker_url(joined.server_id))
    assert url.startswith("unix://")

    # what docker-py would do: connect to the bridge socket and talk HTTP
    def serve():
        opened = node.next("open").open
        request = node.next("data").data
        assert request.data.startswith(b"GET /_ping")
        node.send(data=pb.TunnelData(id=opened.id, data=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"))
        node.send(close=pb.TunnelClose(id=opened.id))

    threading.Thread(target=serve, daemon=True).start()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bridge:
        bridge.settimeout(5)
        bridge.connect(url.removeprefix("unix://"))
        bridge.sendall(b"GET /_ping HTTP/1.1\r\nHost: docker\r\n\r\n")
        reply = b""
        while chunk := bridge.recv(100):
            reply += chunk
    assert reply.endswith(b"\r\n\r\nOK")

    # the real docker.connect picks the bridge while the node is up, and the SSH URL once it is gone
    real_connect = fake.originals["connect"]
    made = []
    original = docker.docker.DockerClient
    docker.docker.DockerClient = lambda base_url, use_ssh_client, timeout: made.append(base_url) or base_url
    try:
        docker.remotes.pop(joined.server_id, None)
        assert real_connect(joined.server_id, f"node://{joined.server_id}") == url
        node.close()
        deadline = 50
        while nodes.hub.connected(joined.server_id) and deadline:
            threading.Event().wait(0.1)
            deadline -= 1
        with pytest.raises(RuntimeError, match="zoo-node is not connected"):
            real_connect(joined.server_id, f"node://{joined.server_id}")
        assert real_connect(joined.server_id, "ssh://zoo@box") == "ssh://zoo@box"
    finally:
        docker.docker.DockerClient = original
        docker.remotes.pop(joined.server_id, None)


def test_replaced_and_deleted_nodes_are_refused(client, alice, endpoint, joined):
    # a certificate from another CA doesn't get through TLS
    other = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, joined.node_id)])
    forged = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(other.public_key())
        .serial_number(1)
        .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC))
        .not_valid_after(datetime.datetime(2100, 1, 1, tzinfo=datetime.UTC))
        .sign(other, hashes.SHA256())
    )
    node = NodeClient(endpoint, other, forged.public_bytes(serialization.Encoding.PEM).decode())
    node.send(hello=pb.Hello(version="dev"))
    with pytest.raises(ConnectionError):
        node.next()

    # deleting the server revokes the node
    node = connect(endpoint, joined)
    node.hello()
    assert client.delete(f"/servers/{joined.server_id}", headers=alice).status_code == 204
    assert not nodes.hub.connected(joined.server_id)
    node = connect(endpoint, joined)
    node.send(hello=pb.Hello(version="dev"))
    with pytest.raises(ConnectionError, match="UNAUTHENTICATED"):
        node.next()


def test_nodes_get_the_apis_build_when_their_version_differs(endpoint, joined, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    build = os.urandom(nodes.UPDATE_CHUNK + 1000)
    (dist / "zoo-node-linux-amd64").write_bytes(build)
    (dist / "VERSION").write_text(nodes.VERSION + "\n")
    monkeypatch.setattr(nodes, "DIST", str(dist))

    node = connect(endpoint, joined)
    node.hello(version="0.0.1")
    first = node.next("update").update
    second = node.next("update").update
    assert (first.version, first.size, first.last, second.last) == (nodes.VERSION, len(build), False, True)
    assert first.data + second.data == build and first.sha256 == hashlib.sha256(build).hexdigest()
    node.close()

    for version in (nodes.VERSION, "dev"):
        assert nodes.update_for("linux", "amd64", version) is None
    # a build of another version isn't pushed
    (dist / "VERSION").write_text("9.9.9")
    assert nodes.update_for("linux", "amd64", "0.0.1") is None


def test_the_download_endpoint_serves_the_build(client, monkeypatch, tmp_path):
    (tmp_path / "zoo-node-darwin-arm64").write_bytes(b"binary")
    (tmp_path / "VERSION").write_text(nodes.VERSION)
    monkeypatch.setattr(nodes, "DIST", str(tmp_path))
    res = client.get("/nodes/download/darwin/arm64")
    assert res.status_code == 200 and res.content == b"binary"
    assert res.headers["x-sha256"] == hashlib.sha256(b"binary").hexdigest()
    assert client.get("/nodes/download/linux/arm64").status_code == 404
    assert client.get("/nodes/download/plan9/arm64").status_code == 422


def test_nodes_renew_their_certificate_on_the_stream(endpoint, joined):
    node = connect(endpoint, joined)
    node.hello()
    key, request = csr()
    node.send(renew=pb.Renew(csr=request.encode()))
    renewal = node.next("renewal").renewal
    assert renewal.error == ""
    cert = x509.load_pem_x509_certificate(renewal.certificate)
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == joined.node_id
    assert same_key(cert.public_key(), key.public_key())
    with db_manager.session() as db:
        assert present(db.get_node(id=joined.node_id)).serial == nodes.serial_of(cert)
    # the stream that renewed keeps reporting
    node.send(status=pb.Status(cpus=1))
    node.send(renew=pb.Renew(csr=b"junk"))
    assert node.next("renewal").renewal.error
    node.close()

    # the old certificate is now refused, the new one accepted
    old = connect(endpoint, joined)
    old.send(hello=pb.Hello(version="dev"))
    with pytest.raises(ConnectionError, match="UNAUTHENTICATED"):
        old.next()
    renewed = NodeClient(endpoint, key, renewal.certificate.decode())
    renewed.hello()
    renewed.close()


# --- placement ---


def fake_session(server_id: str, memory_available: int):
    """Marks a server's node connected, with a fresh report of its free memory."""
    with db_manager.session() as db:
        node = db.get_node_by_server(server_id=server_id)
        node_id = node.id if node else f"node-{server_id}"
    if node is None:
        sql(
            "INSERT INTO nodes (id, server_id, serial, cert_expires_at) VALUES (?, ?, 'x', '2100-01-01')",
            node_id,
            server_id,
        )
    sql(
        "UPDATE nodes SET memory_available = ?, seen_at = CURRENT_TIMESTAMP WHERE server_id = ?",
        memory_available,
        server_id,
    )
    session = nodes.Session(node_id=node_id, server_id=server_id, serial="x")
    session.ready.set()
    nodes.hub.attach(session)


def test_sandboxes_go_where_nodes_report_the_most_free_memory(client, alice, monkeypatch):
    from server import sandbox_api

    monkeypatch.setattr(sandbox_api, "local_memory", lambda: 3 << 30)
    small = add_server("alice@example.com", name="small")
    big = add_server("alice@example.com", name="big")
    ssh_only = add_server("alice@example.com", name="ssh")
    fake_session(small, 4 << 30)
    fake_session(big, 16 << 30)

    def placed() -> str | None:
        res = client.post("/sandboxes", json={"kind": "code", "server_id": "auto"}, headers=alice)
        assert res.status_code == 201, res.text
        return res.json()["server_id"]

    assert placed() == big
    # this machine wins when it has more room
    monkeypatch.setattr(sandbox_api, "local_memory", lambda: 64 << 30)
    assert placed() is None
    # a stale report doesn't count
    monkeypatch.setattr(sandbox_api, "local_memory", lambda: None)
    sql("UPDATE nodes SET seen_at = now() - interval '10 minutes' WHERE server_id = ?", big)
    assert placed() == small
    # with no host reporting room, placement falls back to counting sandboxes
    fake_session(small, 1 << 30)
    assert placed() == ssh_only


def test_vm_placement_prefers_free_memory_under_the_vm_limit(client, alice, monkeypatch):
    from server import sandbox_api, windows

    monkeypatch.setattr(windows, "MAX_VMS", 4)
    roomy = add_server("alice@example.com", "windows", name="roomy")
    tight = add_server("alice@example.com", "windows", name="tight")
    fake_session(roomy, 64 << 30)
    fake_session(tight, 9 << 30)
    with db_manager.session() as db:
        user = member()
        api = sandbox_api.SandboxApi.__new__(sandbox_api.SandboxApi)
        servers = list(db.list_servers_by_workspace(workspace_id=user.workspace_id))
        assert api.place_vm("windows", "auto", servers, db) == roomy


# --- converting SSH servers ---


def test_migrating_a_server_installs_the_node_over_its_connection(client, alice, monkeypatch):
    server_id = add_server("alice@example.com")
    installed = []

    def install(server, token):
        installed.append(server.id)
        # what `zoo-node join <token> --service` on the host does
        _, res = join(client, token)
        assert res.status_code == 200, res.text
        fake_session(server.id, 8 << 30)

    monkeypatch.setitem(nodes_api.MIGRATE, "linux", install)
    assert client.post(f"/servers/{server_id}/migrate", headers=alice).status_code == 204
    assert installed == [server_id]
    server = client.get("/servers", headers=alice).json()[0]
    assert server["docker_url"] == "ssh://zoo@box" and server["node"]["connected"]

    # installed but never connected
    monkeypatch.setattr(nodes_api, "JOIN_WAIT", 0.2)
    other = add_server("alice@example.com", name="other")
    monkeypatch.setitem(nodes_api.MIGRATE, "linux", lambda server, token: None)
    res = client.post(f"/servers/{other}/migrate", headers=alice)
    assert res.status_code == 504 and "hasn't connected" in res.json()["error"]["message"]

    # a failure on the host comes back to the caller
    def broken(server, token):
        raise RuntimeError("no space left on device")

    monkeypatch.setitem(nodes_api.MIGRATE, "linux", broken)
    res = client.post(f"/servers/{other}/migrate", headers=alice)
    assert res.status_code == 502 and "no space left" in res.json()["error"]["message"]


def test_only_ssh_servers_migrate(client, alice, bob):
    made = new_token(client, alice)
    server_id = join(client, made["token"])[1].json()["server_id"]
    assert client.post(f"/servers/{server_id}/migrate", headers=alice).status_code == 409
    assert client.post(f"/servers/{server_id}/migrate", headers=bob).status_code == 404


class Helper:
    """A docker-py container: records archives put into it and commands run in it."""

    def __init__(self, outputs: dict[str, str] | None = None):
        self.archives: list[tuple[str, bytes]] = []
        self.commands: list[list[str] | str] = []
        self.started = self.removed = False
        self.outputs = outputs or {}
        self.environments: list[dict | None] = []

    def start(self):
        self.started = True

    def put_archive(self, path, data):
        self.archives.append((path, data))
        return True

    def exec_run(self, cmd, user=None, environment=None):
        self.commands.append(cmd)
        self.environments.append(environment)
        text = cmd[-1] if isinstance(cmd, list) else cmd
        output = next((out for marker, out in self.outputs.items() if marker in text), "")
        return SimpleNamespace(exit_code=0, output=output.encode())

    def remove(self, force=False):
        self.removed = True


class DockerHost:
    def __init__(self, arch="x86_64", outputs=None):
        self.arch = arch
        self.created: list[tuple[str, dict]] = []
        self.helpers: list[Helper] = []
        self.outputs = outputs or {}
        self.removed_volumes: list[str] = []
        self.containers = SimpleNamespace(create=self.create)
        self.volumes = SimpleNamespace(
            get=lambda name: SimpleNamespace(remove=lambda force: self.removed_volumes.append(name))
        )

    def info(self):
        return {"Architecture": self.arch}

    def create(self, image, **kwargs):
        self.created.append((image, kwargs))
        helper = Helper(self.outputs)
        self.helpers.append(helper)
        return helper


def test_linux_migration_installs_through_a_host_container(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "zoo-node-linux-amd64").write_bytes(b"zoo-node build")
    (dist / "VERSION").write_text(nodes.VERSION)
    monkeypatch.setattr(nodes, "DIST", str(dist))
    host = DockerHost()
    monkeypatch.setattr(docker, "connect", lambda server_id, url: host)
    monkeypatch.setattr(docker, "ensure_image", lambda client, image: None)
    nodes_api.migrate_linux(cast(Server, SimpleNamespace(id="s1", docker_url="ssh://zoo@box")), "zn1.token")
    ((_, options),) = host.created
    assert options["privileged"] and options["pid_mode"] == "host" and options["runtime"] == "runc"
    assert options["volumes"] == {"/": {"bind": "/host", "mode": "rw"}}
    helper = host.helpers[0]
    assert helper.archives[0][0] == "/host/usr/local/bin" and b"zoo-node build" in helper.archives[0][1]
    assert helper.commands == [["chroot", "/host", "/usr/local/bin/zoo-node", "join", "zn1.token", "--service"]]
    assert helper.removed

    with pytest.raises(RuntimeError, match="amd64 and arm64"):
        monkeypatch.setattr(docker, "connect", lambda server_id, url: DockerHost(arch="riscv64"))
        nodes_api.migrate_linux(cast(Server, SimpleNamespace(id="s1", docker_url="ssh://zoo@box")), "zn1.token")


# --- Linux homes through object storage ---


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("ZOO_OBJECT_STORE", "s3://vms/zoo")
    monkeypatch.setenv("ZOO_S3_ENDPOINT", "https://minio.example:9000")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AK")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SK")
    deleted: list[str] = []
    monkeypatch.setattr(docker.objects, "delete", deleted.append)
    return deleted


def test_a_linux_home_moves_through_object_storage(fake, store, monkeypatch):
    source = DockerHost(outputs={"du -sb": f"{(1 << 30) + 5}\n", "split": "p0000\np0001\n"})
    target = DockerHost()
    monkeypatch.setattr(docker, "client_for", lambda server: source if server.id == "a" else target)
    monkeypatch.setattr(docker, "ensure_image", lambda client, image: None)
    removed = []
    monkeypatch.setattr(docker, "remove_volume", lambda sandbox_id, server: removed.append(server.id))
    fake.originals["copy_volume"]("s", SimpleNamespace(id="a"), SimpleNamespace(id="b"))

    reader, writer = source.helpers[0], target.helpers[0]
    assert source.created[0][1]["volumes"] == {"zoo-home-s": {"bind": "/from", "mode": "rw"}}
    assert target.created[0][1]["volumes"] == {"zoo-home-s": {"bind": "/to", "mode": "rw"}}
    # three parts were presigned for a home of 1 GB and a bit; two were used and downloaded, in order
    urls = reader.archives[0][1]
    assert urls.count(b"moves/s/home.000") == 3 and b"X-Amz-" in urls
    assert any("split -d -a 4" in c for c in reader.commands if isinstance(c, list) for c in c)
    assert reader.environments[-1] == {"PART": str(1 << 30)}
    downloads = writer.archives[0][1]
    assert downloads.count(b"/moves/s/home.") == 2 and downloads.index(b"home.0000") < downloads.index(b"home.0001")
    assert reader.removed and writer.removed
    assert store == ["moves/s/home.0000", "moves/s/home.0001", "moves/s/home.0002"] and removed == ["a"]


def test_docker_snapshots_go_through_object_storage(fake, monkeypatch):
    host = DockerHost(outputs={"du -sb /from": "8192\n"})
    monkeypatch.setattr(docker, "client_for", lambda server: host)
    monkeypatch.setattr(docker, "ensure_image", lambda client, image: None)
    assert fake.originals["snapshot"]("s", "snap1") == 8192
    # the host packs the home and uploads it itself
    assert host.created[0][1]["volumes"] == {"zoo-home-s": {"bind": "/from", "mode": "rw"}}
    assert "curl" in host.helpers[0].commands[-1][-1]
    with pytest.raises(RuntimeError, match="gone"):
        fake.originals["restore_snapshot"]("s", "snap1")
    STORE["snapshots/snap1/home.0000"] = b"part"
    fake.originals["restore_snapshot"]("s", "snap1")
    assert host.created[-1][1]["volumes"] == {"zoo-home-s": {"bind": "/to", "mode": "rw"}}
    assert "find /to -mindepth 1 -delete" in host.helpers[-1].commands[0][-1]
