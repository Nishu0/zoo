"""Node enrollment, gateway-backed placement and artifact downloads. Native stream coverage is in gateway/."""

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from apps.server import docker, nodes
from apps.server.errors import ZooError
from db.connection import db_manager
from tests.conftest import GATEWAY, STORE, member, present, sql
from tests.test_sandboxes import add_server

HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


@pytest.fixture(autouse=True)
def fresh_nodes(monkeypatch):
    # each test has its own database, so its own CA and streams
    monkeypatch.setattr(nodes, "_authority", None)
    monkeypatch.setattr(nodes, "hub", nodes.Hub())
    yield


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


def new_token(client, headers, name="edge-1", platform="macos") -> dict:
    res = client.post(f"/v1/nodes/tokens?platform={platform}", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def join(client, token: str, os_name="darwin", **extra):
    key, request = csr()
    res = client.post(
        "/v1/nodes/join",
        json={
            "token": token,
            "csr": request,
            "hostname": "edge",
            "os": os_name,
            "arch": "arm64" if os_name == "darwin" else "amd64",
            "ssh_user": "zoo",
            "ssh_host_key": HOST_KEY,
            **extra,
        },
    )
    return key, res


def test_a_node_joins_once_with_its_token_and_becomes_a_server(client, alice):
    made = new_token(client, alice)
    body = nodes.decode_token(made["token"])
    assert body["u"] == "http://testserver" and body["f"] == nodes.authority().fingerprint
    assert made["join_command"] == f"zoo-node join '{made['token']}' --service"
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

    servers = client.get("/v1/servers", headers=alice).json()
    assert [(s["id"], s["name"], s["docker_url"], s["platform"]) for s in servers] == [
        (joined["server_id"], "edge-1", f"node://zoo@{joined['server_id']}", "macos")
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
        "/v1/nodes/join",
        json={
            "token": new_token(client, alice)["token"],
            "csr": "nope",
            "os": "darwin",
            "arch": "arm64",
            "ssh_user": "zoo",
            "ssh_host_key": HOST_KEY,
        },
    )
    assert res.status_code == 422
    assert client.get("/v1/servers", headers=alice).json() == []


@pytest.mark.parametrize("platform,os_name", [("linux", "linux"), ("macos", "darwin"), ("windows", "windows")])
def test_nodes_on_all_platforms_bring_their_ssh_account_and_host_key(client, alice, platform, os_name):
    made = new_token(client, alice, platform=platform)
    for field in ("ssh_user", "ssh_host_key"):
        _, res = join(client, made["token"], os_name, **{field: None})
        assert res.status_code == 422
    _, res = join(client, made["token"], os_name)
    assert res.status_code == 200, res.text
    server_id = res.json()["server_id"]
    server = next(s for s in client.get("/v1/servers", headers=alice).json() if s["id"] == server_id)
    assert server["docker_url"] == f"node://zoo@{server_id}" and server["platform"] == platform
    with db_manager.session() as db:
        node = present(db.get_node_by_server(server_id=server_id))
    assert node.ssh_host_key == HOST_KEY
    assert (
        "--token" in made["install_command"]
        if platform != "windows"
        else f" -Token '{made['token']}'" in made["install_command"]
    )


# --- the stream ---


def test_the_download_endpoint_serves_the_build(client, monkeypatch, tmp_path):
    (tmp_path / "zoo-node-darwin-arm64").write_bytes(b"binary")
    (tmp_path / "VERSION").write_text(nodes.VERSION)
    monkeypatch.setattr(nodes, "DIST", str(tmp_path))
    res = client.get("/v1/nodes/download/darwin/arm64")
    assert res.status_code == 200 and res.content == b"binary"
    assert res.headers["x-sha256"] == hashlib.sha256(b"binary").hexdigest()
    assert client.get("/v1/nodes/download/plan9/arm64").status_code == 422


# --- placement ---


def fake_session(server_id: str, memory_available: int):
    """Marks a server's node connected, with a fresh report of its free memory."""
    with db_manager.session() as db:
        node = db.get_node_by_server(server_id=server_id)
        node_id = node.id if node else f"node-{server_id}"
    if node is None:
        _, request = csr()
        certificate = nodes.sign_csr(request.encode(), node_id)
        node = nodes.register(server_id, certificate, "windows", "amd64", server_id, HOST_KEY)
        node_id = node.id
    sql(
        "UPDATE nodes SET memory_available = ?, seen_at = CURRENT_TIMESTAMP WHERE server_id = ?",
        memory_available,
        server_id,
    )
    GATEWAY.nodes[server_id] = {"targets": ["ssh"], "memory_available": memory_available}


def test_vm_placement_obeys_caps_without_ranking_free_memory(alice, monkeypatch, zoo):
    from apps.server import windows
    from apps.server.sandbox_api import CreateSandboxRequest

    monkeypatch.setattr(windows, "MAX_VMS", 1)
    roomy = add_server("alice@example.com", "windows", name="roomy")
    tight = add_server("alice@example.com", "windows", name="tight")
    fake_session(roomy, 64 << 30)
    fake_session(tight, 9 << 30)
    with db_manager.session() as db:
        user = member()
        api = zoo.sandbox_api
        servers = list(db.list_servers_by_workspace(workspace_id=user.workspace_id))
        api.create(CreateSandboxRequest(kind="windows", server_id=roomy), user, db)
        assert api.place_vm("windows", "auto", servers, db) == tight
        api.create(CreateSandboxRequest(kind="windows", server_id=tight), user, db)
        with pytest.raises(HTTPException) as error:
            api.place_vm("windows", "auto", servers, db)
        assert error.value.status_code == 409


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


def test_docker_snapshots_go_through_object_storage(fake, monkeypatch):
    host = DockerHost(outputs={"du -sb /from": "8192\n"})
    monkeypatch.setattr(docker, "docker_client", host)
    assert fake.originals["snapshot"]("s", "snap1") == 8192
    # the host packs the home and uploads it itself
    assert host.created[0][1]["volumes"] == {"zoo-home-s": {"bind": "/from", "mode": "rw"}}
    assert "curl" in host.helpers[0].commands[-1][-1]
    with pytest.raises(ZooError, match="gone"):
        fake.originals["restore_snapshot"]("s", "snap1")
    STORE["snapshots/snap1/home.0000"] = b"part"
    fake.originals["restore_snapshot"]("s", "snap1")
    assert host.created[-1][1]["volumes"] == {"zoo-home-s": {"bind": "/to", "mode": "rw"}}
    assert "find /to -mindepth 1 -delete" in host.helpers[-1].commands[0][-1]


@pytest.mark.parametrize(
    "name,relative", [("install.sh", "install.sh"), ("install-node.ps1", "windows/install-node.ps1")]
)
def test_public_installers_match_the_running_api_image(client, name, relative):
    from pathlib import Path

    response = client.get(f"/v1/nodes/install/{name}")
    assert response.status_code == 200
    assert response.content == (Path(nodes.ROOT) / relative).read_bytes()
    assert response.headers["content-type"].startswith("text/plain")
    assert client.get("/v1/nodes/install/unknown.sh").status_code == 422


@pytest.mark.parametrize(
    "platform,name", [("linux", "install.sh"), ("macos", "install.sh"), ("windows", "install-node.ps1")]
)
def test_enrollment_commands_download_installers_from_the_control_plane(client, alice, monkeypatch, platform, name):
    from apps.server import platforms

    monkeypatch.setattr(platforms, "INSTALL_URL", None)
    made = new_token(client, alice, platform=platform)
    assert f"http://testserver/v1/nodes/install/{name}" in made["install_command"]
    assert made["token"] in made["install_command"]


def test_public_bootstrap_ssh_key_is_the_control_plane_public_identity_without_cache(client):
    from apps.server import ssh

    first = client.get("/v1/nodes/bootstrap/ssh-key")
    assert first.status_code == 200, first.text
    assert first.headers["content-type"].startswith("text/plain")
    assert first.headers["cache-control"] == "no-store"
    assert first.text == ssh.public_key() + "\n"
    assert ssh.validate_host_key(first.text) == first.text.strip()
    assert first.text.startswith("ssh-ed25519 ")
    assert "PRIVATE KEY" not in first.text
    # Existing installers resume without authenticating again or rotating the SSH identity.
    assert client.get("/v1/nodes/bootstrap/ssh-key").text == first.text
