"""zoo-node: the daemon on each host (node/), and this side of its link.

A node joins with a one-time token from the dashboard: it sends a CSR, and gets back a certificate signed by the
API's node CA (CN = its node id) and the API's gRPC endpoints. It then dials out to every endpoint and holds a
Connect stream to each (mTLS both ways), so hosts need no inbound ports and work behind NAT.

Over the stream the node reports its capacity, health and running sandboxes, and the API opens byte tunnels to
the host's Docker socket or SSH server. The runtime backends (server/docker.py, macos.py, windows.py) reach a
node's host through those tunnels exactly as they reach an SSH server, so every operation works the same; an SSH
server converted in place (`zoo node migrate`) falls back to plain SSH while its node is offline.

The API also pushes zoo-node builds matching its own version (node/dist, `make node-dist`) and renews node
certificates before they expire.
"""

import base64
import contextlib
import datetime
import hashlib
import ipaddress
import json
import logging
import os
import queue
import secrets
import socket
import tempfile
import threading
import time
import tomllib
import uuid
from collections.abc import Iterator
from concurrent import futures
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import grpc
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from db.connection import db_manager
from db.generated.models import Node, NodeToken
from db.generated.query import (
    CreateNodeTokenParams,
    SetNodeHelloParams,
    SetNodeStatusParams,
    UpsertNodeParams,
)
from server import gateway
from server.nodepb import node_pb2 as pb
from server.nodepb import node_pb2_grpc as pb_grpc
from server.security import SYSTEM, decrypt, encrypt

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.environ.get("ZOO_NODE_DIST") or os.path.join(ROOT, "node", "dist")
# "off": this process serves no node endpoint (server/server.py)
_PORT = os.environ.get("ZOO_NODE_PORT", "7443")
PORT = 0 if _PORT == "off" else int(_PORT)
TOKEN_PREFIX = "zn1."
TOKEN_TTL = 3600
CERT_DAYS = 365
STATUS_SECONDS = 15
# a node whose last report is older than this isn't counted on for capacity
FRESH_SECONDS = 90
UPDATE_CHUNK = 1 << 20
CHUNK = 64 * 1024


def api_version() -> str:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)["project"]["version"]


VERSION = api_version()
# the port this process's node endpoint listens on, once started (ZOO_NODE_PORT=0 picks a free one)
listening = 0


def endpoints(api_url: str = "") -> list[str]:
    """The gRPC endpoints nodes dial: ZOO_NODE_ENDPOINTS (host:port, comma-separated, one per API process), or the
    API URL's host on ZOO_NODE_PORT."""
    configured = [e.strip() for e in os.environ.get("ZOO_NODE_ENDPOINTS", "").split(",") if e.strip()]
    if configured:
        return configured
    host = urlparse(os.environ.get("ZOO_API_URL") or api_url).hostname or "localhost"
    return [f"{host}:{listening or PORT}"]


# --- the node CA ---


@dataclass
class Authority:
    certificate: x509.Certificate
    key: ec.EllipticCurvePrivateKey

    @property
    def pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)

    @property
    def fingerprint(self) -> str:
        return self.certificate.fingerprint(hashes.SHA256()).hex()


_authority: Authority | None = None
_authority_lock = threading.Lock()


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def authority() -> Authority:
    """The node CA, created on first use. Every API process shares it through the database."""
    global _authority
    with _authority_lock:
        if _authority is not None:
            return _authority
        with db_manager.session() as db:
            row = db.get_node_authority()
            if row is None:
                key = ec.generate_private_key(ec.SECP256R1())
                name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Zoo node CA")])
                cert = (
                    x509.CertificateBuilder()
                    .subject_name(name)
                    .issuer_name(name)
                    .public_key(key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(now() - datetime.timedelta(minutes=5))
                    .not_valid_after(now() + datetime.timedelta(days=3650))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                    .add_extension(
                        x509.KeyUsage(
                            digital_signature=True,
                            key_cert_sign=True,
                            crl_sign=True,
                            content_commitment=False,
                            key_encipherment=False,
                            data_encipherment=False,
                            key_agreement=False,
                            encipher_only=False,
                            decipher_only=False,
                        ),
                        critical=True,
                    )
                    .sign(key, hashes.SHA256())
                )
                pem_key = key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
                # another process may have won the race; ON CONFLICT keeps its CA
                db.create_node_authority(
                    certificate=cert.public_bytes(serialization.Encoding.PEM).decode(),
                    key_ciphertext=encrypt(pem_key.decode(), db, SYSTEM),
                )
                row = db.get_node_authority()
            assert row is not None
        key = serialization.load_pem_private_key(decrypt(row.key_ciphertext).encode(), None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise TypeError("the node CA key is not an EC key")
        _authority = Authority(x509.load_pem_x509_certificate(row.certificate.encode()), key)
        return _authority


def issue(public_key, common_name: str, days: int, server_names: list[str] | None = None) -> x509.Certificate:
    ca = authority()
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca.certificate.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now() - datetime.timedelta(minutes=5))
        .not_valid_after(now() + datetime.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if server_names is None:
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    else:
        names: list[x509.GeneralName] = []
        for name in dict.fromkeys(server_names):
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(name)))
            except ValueError:
                names.append(x509.DNSName(name))
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False).add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
    return builder.sign(ca.key, hashes.SHA256())


def sign_csr(csr_pem: bytes, node_id: str) -> x509.Certificate:
    """A node certificate for the CSR's key. Only the key is taken from the CSR: the name is always the node id."""
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except ValueError as e:
        raise ValueError("not a PEM certificate request") from e
    if not csr.is_signature_valid:
        raise ValueError("the certificate request's signature is invalid")
    key = csr.public_key()
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise TypeError("node keys must be EC keys")
    return issue(key, node_id, CERT_DAYS)


def serial_of(cert: x509.Certificate) -> str:
    return format(cert.serial_number, "x")


def expiry_of(cert: x509.Certificate) -> datetime.datetime:
    return cert.not_valid_after_utc


def pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# --- join tokens ---


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_token(
    user_id: str, workspace_id: str, name: str, api_url: str, server_id: str | None = None
) -> tuple[str, NodeToken]:
    """A one-time join token. It carries the API URL and the CA's fingerprint, so the node can pin the CA it gets
    back from the join request."""
    secret = secrets.token_urlsafe(32)
    expires = now() + datetime.timedelta(seconds=TOKEN_TTL)
    with db_manager.session() as db:
        db.purge_node_tokens(expires_at=now() - datetime.timedelta(days=1))
        row = db.create_node_token(
            CreateNodeTokenParams(
                id=str(uuid.uuid4()),
                secret_hash=hash_secret(secret),
                created_by=user_id,
                server_id=server_id,
                name=name,
                expires_at=expires,
                workspace_id=workspace_id,
            )
        )
    if row is None:
        raise RuntimeError("the join token wasn't saved")
    body = json.dumps({"u": api_url, "s": secret, "f": authority().fingerprint}, separators=(",", ":"))
    return TOKEN_PREFIX + base64.urlsafe_b64encode(body.encode()).decode().rstrip("="), row


def decode_token(token: str) -> dict:
    if not token.startswith(TOKEN_PREFIX):
        raise ValueError("not a zoo-node join token")
    raw = token[len(TOKEN_PREFIX) :]
    try:
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError as e:
        raise ValueError("not a zoo-node join token") from e


def claim_token(secret: str) -> NodeToken | None:
    """Uses up a token; None when it is unknown, expired or used."""
    with db_manager.session() as db:
        return db.claim_node_token(secret_hash=hash_secret(secret))


def register(server_id: str, cert: x509.Certificate, os_name: str, arch: str, hostname: str) -> Node:
    node_id = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    with db_manager.session() as db:
        node = db.upsert_node(
            UpsertNodeParams(
                id=str(node_id),
                server_id=server_id,
                serial=serial_of(cert),
                cert_expires_at=expiry_of(cert),
                os=os_name,
                arch=arch,
                hostname=hostname,
            )
        )
    if node is None:
        raise RuntimeError("the node wasn't saved")
    hub.drop(server_id)
    return node


# --- updates ---


def build(os_name: str, arch: str) -> str | None:
    """This API version's zoo-node build for a platform, if node/dist has it."""
    path = os.path.join(DIST, f"zoo-node-{os_name}-{arch}" + (".exe" if os_name == "windows" else ""))
    try:
        with open(os.path.join(DIST, "VERSION")) as f:
            built = f.read().strip()
    except FileNotFoundError:
        return None
    return path if built == VERSION and os.path.exists(path) else None


def update_for(os_name: str, arch: str, version: str) -> str | None:
    """The build to push to a node, or None when it already runs this version (or a development build)."""
    if version in ("", "dev") or version == VERSION:
        return None
    return build(os_name, arch)


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(UPDATE_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- capacity ---


def fresh(node: Node) -> bool:
    if not node.seen_at:
        return False
    return (now() - node.seen_at).total_seconds() < FRESH_SECONDS


def free_memory(server_ids: list[str]) -> dict[str, int]:
    """Free memory, in bytes, of the servers whose node reported it recently."""
    wanted = set(server_ids)
    with db_manager.session() as db:
        nodes = [n for n in db.list_nodes() if n.server_id in wanted]
    return {
        n.server_id: int(n.memory_available)
        for n in nodes
        if fresh(n) and n.memory_available is not None and hub.connected(n.server_id)
    }


# --- the hub: one stream per connected node ---


class Closed(Exception):
    pass


@dataclass
class Session:
    node_id: str
    server_id: str
    serial: str
    outbox: "queue.Queue[pb.ApiMessage | None]" = field(default_factory=queue.Queue)
    tunnels: dict[int, socket.socket] = field(default_factory=dict)
    hello: pb.Hello | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_id: int = 0

    def send(self, message: pb.ApiMessage):
        if self.closed.is_set():
            raise Closed(f"node {self.node_id} disconnected")
        self.outbox.put(message)

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.outbox.put(None)
        with self.lock:
            tunnels, self.tunnels = list(self.tunnels.values()), {}
        for sock in tunnels:
            with contextlib.suppress(OSError):
                sock.close()


class Hub:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.bridges: dict[str, str] = {}
        self.bridge_dir: str | None = None
        self.listeners: list[socket.socket] = []

    def connected(self, server_id: str) -> bool:
        session = self.sessions.get(server_id)
        return session is not None and session.ready.is_set() and not session.closed.is_set()

    def session(self, server_id: str) -> Session:
        session = self.sessions.get(server_id)
        if session is None or not session.ready.is_set() or session.closed.is_set():
            raise Closed("the server's zoo-node is not connected")
        return session

    def targets(self, server_id: str) -> set[str]:
        session = self.sessions.get(server_id)
        return set(session.hello.targets) if session is not None and session.hello is not None else set()

    def attach(self, session: Session):
        with self.lock:
            old = self.sessions.get(session.server_id)
            self.sessions[session.server_id] = session
        if old is not None:
            old.close()

    def detach(self, session: Session):
        with self.lock:
            if self.sessions.get(session.server_id) is session:
                del self.sessions[session.server_id]
        session.close()

    def drop(self, server_id: str):
        """Ends a server's stream, as when its node is replaced or removed."""
        session = self.sessions.get(server_id)
        if session is not None:
            self.detach(session)

    def open(self, server_id: str, target: str) -> socket.socket:
        """A socket whose other end is `target` (docker or ssh) on the server's host, through its node."""
        session = self.session(server_id)
        if target not in self.targets(server_id):
            raise Closed(f"the server's zoo-node can't reach {target}")
        ours, theirs = socket.socketpair()
        with session.lock:
            session.next_id += 1
            tid = session.next_id
            session.tunnels[tid] = ours
        try:
            session.send(pb.ApiMessage(open=pb.TunnelOpen(id=tid, target=target)))
        except Closed:
            with session.lock:
                session.tunnels.pop(tid, None)
            ours.close()
            theirs.close()
            raise
        threading.Thread(target=self.pump, args=(session, tid, ours), daemon=True, name=f"tunnel-{tid}").start()
        return theirs

    def pump(self, session: Session, tid: int, sock: socket.socket):
        """Sends what the API side writes to the node, until either side closes."""
        error = ""
        try:
            while True:
                data = sock.recv(CHUNK)
                if not data:
                    break
                session.send(pb.ApiMessage(data=pb.TunnelData(id=tid, data=data)))
        except (OSError, Closed) as e:
            error = str(e)
        with session.lock:
            known = session.tunnels.pop(tid, None) is not None
        if known:
            with contextlib.suppress(Closed):
                session.send(pb.ApiMessage(close=pb.TunnelClose(id=tid, error=error)))
        with contextlib.suppress(OSError):
            sock.close()

    def deliver(self, session: Session, data: pb.TunnelData):
        sock = session.tunnels.get(data.id)
        if sock is None:
            return
        try:
            sock.sendall(data.data)
        except OSError:
            self.end(session, data.id)

    def end(self, session: Session, tid: int):
        with session.lock:
            sock = session.tunnels.pop(tid, None)
        if sock is not None:
            # the API side reads EOF; pump() then sees its socket closed
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()

    def docker_socket(self, server_id: str) -> str:
        """A local Unix socket that connects to the host's Docker through the node, for docker-py."""
        with self.lock:
            if server_id in self.bridges:
                return self.bridges[server_id]
            if self.bridge_dir is None:
                self.bridge_dir = tempfile.mkdtemp(prefix="zoo-nodes-")
            path = os.path.join(self.bridge_dir, hashlib.sha256(server_id.encode()).hexdigest()[:16] + ".sock")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(path)
            os.chmod(path, 0o600)
            listener.listen(64)
            self.listeners.append(listener)
            self.bridges[server_id] = path
        threading.Thread(target=self.serve_bridge, args=(server_id, listener), daemon=True).start()
        return path

    def serve_bridge(self, server_id: str, listener: socket.socket):
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            try:
                remote = self.open(server_id, "docker")
            except Closed:
                client.close()
                continue
            splice(client, remote)


def splice(a: socket.socket, b: socket.socket):
    """Copies bytes both ways between two sockets until both directions end."""

    def copy(src: socket.socket, dst: socket.socket):
        try:
            while data := src.recv(CHUNK):
                dst.sendall(data)
        except OSError:
            pass
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)

    def run():
        both = [threading.Thread(target=copy, args=pair, daemon=True) for pair in ((a, b), (b, a))]
        for t in both:
            t.start()
        for t in both:
            t.join()
        a.close()
        b.close()

    threading.Thread(target=run, daemon=True).start()


class RemoteNodeHub(Hub):
    """The nodes of a process that reaches them through the gateway (server/gateway.py): a tunnel is a websocket to
    the gateway, which carries it on the node's stream."""

    def __init__(self):
        super().__init__()
        self.status_of = gateway.Cached()

    def connected(self, server_id: str) -> bool:
        return bool(self.status_of(f"/internal/nodes/{server_id}").get("connected"))

    def targets(self, server_id: str) -> set[str]:
        return set(self.status_of(f"/internal/nodes/{server_id}").get("targets") or [])

    def drop(self, server_id: str):
        gateway.post(f"/internal/nodes/{server_id}/drop")
        self.status_of.answers.pop(f"/internal/nodes/{server_id}", None)

    def open(self, server_id: str, target: str) -> socket.socket:
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect

        try:
            connection = connect(
                gateway.ws_url(f"/internal/nodes/{server_id}/tunnel?target={target}"),
                additional_headers=gateway.headers(),
                max_size=None,
                open_timeout=10,
            )
        except (OSError, WebSocketException) as e:
            raise Closed(f"the server's zoo-node can't be reached through the gateway: {e}") from e
        ours, theirs = socket.socketpair()

        def down():
            try:
                for message in connection:
                    ours.sendall(message if isinstance(message, bytes) else message.encode())
            except (OSError, WebSocketException):
                pass
            with contextlib.suppress(OSError):
                ours.shutdown(socket.SHUT_WR)

        def up():
            try:
                while data := ours.recv(CHUNK):
                    connection.send(data)
            except (OSError, WebSocketException):
                pass
            connection.close()

        def run():
            both = [threading.Thread(target=f, daemon=True) for f in (down, up)]
            for t in both:
                t.start()
            for t in both:
                t.join()
            ours.close()

        threading.Thread(target=run, daemon=True, name=f"gateway-tunnel-{server_id[:8]}").start()
        return theirs


hub = RemoteNodeHub() if gateway.enabled() else Hub()


def docker_url(server_id: str) -> str | None:
    """Where docker-py reaches the server's Docker through its node, or None while the node isn't connected."""
    if hub.connected(server_id) and "docker" in hub.targets(server_id):
        return "unix://" + hub.docker_socket(server_id)
    return None


def ssh_socket(server_id: str) -> socket.socket | None:
    """A connection to the host's SSH server through its node, or None while the node isn't connected."""
    if hub.connected(server_id) and "ssh" in hub.targets(server_id):
        return hub.open(server_id, "ssh")
    return None


# --- the gRPC service ---


def peer_certificate(context: grpc.ServicerContext) -> x509.Certificate | None:
    # a list of PEM certificates, the peer's first; grpc's types say otherwise
    found: Any = context.auth_context().get("x509_pem_cert")
    return x509.load_pem_x509_certificate(found[0]) if found else None


def common_name(cert: x509.Certificate) -> str:
    return str(cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)


def current(node_id: str, serial: str) -> Node | None:
    """The node, if this certificate is still its own: joining again or deleting the server revokes the old one."""
    with db_manager.session() as db:
        node = db.get_node(id=node_id)
    return node if node is not None and node.serial == serial else None


def on_hello(node: Node, hello: pb.Hello):
    from server.platforms import capabilities_of

    drivers = [{"name": d.name, "available": d.available, "detail": d.detail} for d in hello.drivers]
    with db_manager.session() as db:
        db.set_node_hello(
            SetNodeHelloParams(
                version=hello.version,
                os=hello.os,
                arch=hello.arch,
                hostname=hello.hostname,
                drivers=json.dumps(drivers),
                targets=json.dumps(list(hello.targets)),
                id=node.id,
            )
        )
        server = db.get_server(id=node.server_id)
        # a server the node created runs what the node can: its own OS, and Linux when it reaches a Docker
        if server is not None and server.docker_url.startswith("node://"):
            docker = "docker" in hello.targets and any(d.available for d in hello.drivers if d.name in RUNC_LIKE)
            # a Linux host only runs sandboxes through Docker
            caps = capabilities_of(server.platform, docker) if docker or server.platform != "linux" else ""
            if caps != server.capabilities:
                db.update_server_capabilities(capabilities=caps, id=server.id)


RUNC_LIKE = ("kata", "runc")


def on_status(node: Node, status: pb.Status):
    checks = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in status.checks]
    with db_manager.session() as db:
        db.set_node_status(
            SetNodeStatusParams(
                cpus=status.cpus,
                memory_total=status.memory_total,
                memory_available=status.memory_available,
                disk_total=status.disk_total,
                disk_free=status.disk_free,
                load=status.load,
                sandboxes=json.dumps(list(status.sandboxes)),
                checks=json.dumps(checks),
                id=node.id,
            )
        )


def prepull(server_id: str):
    """Pulls the Linux sandbox images onto a host whose node just connected, as the API does for SSH servers at
    startup, so its first sandbox doesn't wait on the pull. Only in processes that run the background work."""
    from server import workers
    from server.servers_api import prepull_images

    # the gateway holds every stream, so it pulls for the processes behind it
    if not (workers.works() or workers.ROLE == "gateway") or "docker" not in hub.targets(server_id):
        return
    with db_manager.session() as db:
        server = db.get_server(id=server_id)
    if server is not None:
        prepull_images([server]).join()


def send_update(session: Session, path: str, version: str):
    try:
        size = os.path.getsize(path)
        digest = file_sha256(path)
        logger.info("updating zoo-node", extra={"node_id": session.node_id, "version": version})
        with open(path, "rb") as f:
            while True:
                data = f.read(UPDATE_CHUNK)
                last = f.tell() >= size
                session.send(
                    pb.ApiMessage(
                        update=pb.UpdateChunk(version=version, sha256=digest, size=size, data=data, last=last)
                    )
                )
                if last:
                    return
    except (OSError, Closed) as e:
        logger.warning("zoo-node update failed", extra={"node_id": session.node_id, "error": str(e)})


def renew(session: Session, request: pb.Renew) -> pb.Renewal:
    try:
        cert = sign_csr(request.csr, session.node_id)
    except (ValueError, TypeError) as e:
        return pb.Renewal(error=str(e))
    with db_manager.session() as db:
        db.set_node_certificate(serial=serial_of(cert), cert_expires_at=expiry_of(cert), id=session.node_id)
    session.serial = serial_of(cert)
    return pb.Renewal(certificate=pem(cert).encode())


class Servicer(pb_grpc.NodeServicer):
    def Connect(self, request_iterator: Iterator[pb.NodeMessage], context: grpc.ServicerContext):
        cert = peer_certificate(context)
        node = current(common_name(cert), serial_of(cert)) if cert is not None else None
        if cert is None or node is None:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "unknown or replaced node certificate")
            return
        session = Session(node_id=node.id, server_id=node.server_id, serial=serial_of(cert))
        threading.Thread(target=self.receive, args=(session, node, request_iterator), daemon=True).start()
        context.add_callback(session.close)
        while True:
            message = session.outbox.get()
            if message is None:
                break
            yield message
        hub.detach(session)

    def receive(self, session: Session, node: Node, messages: Iterator[pb.NodeMessage]):
        try:
            for message in messages:
                kind = message.WhichOneof("body")
                if kind == "data":
                    hub.deliver(session, message.data)
                elif kind == "close":
                    hub.end(session, message.close.id)
                elif kind == "hello":
                    session.hello = message.hello
                    on_hello(node, message.hello)
                    session.send(
                        pb.ApiMessage(
                            welcome=pb.Welcome(
                                api_version=VERSION, endpoints=endpoints(), status_seconds=STATUS_SECONDS
                            )
                        )
                    )
                    hub.attach(session)
                    session.ready.set()
                    logger.info(
                        "zoo-node connected",
                        extra={"node_id": node.id, "server_id": node.server_id, "version": message.hello.version},
                    )
                    threading.Thread(target=prepull, args=(node.server_id,), daemon=True).start()
                    path = update_for(message.hello.os, message.hello.arch, message.hello.version)
                    if path is not None:
                        threading.Thread(target=send_update, args=(session, path, VERSION), daemon=True).start()
                elif kind == "status":
                    latest = current(session.node_id, session.serial)
                    if latest is None:
                        break
                    on_status(latest, message.status)
                elif kind == "renew":
                    session.send(pb.ApiMessage(renewal=renew(session, message.renew)))
        except grpc.RpcError:
            pass
        except Exception as e:
            logger.error("zoo-node stream failed", extra={"node_id": session.node_id, "error": str(e)})
        finally:
            hub.detach(session)


def server_credentials(names: list[str]) -> grpc.ServerCredentials:
    key = ec.generate_private_key(ec.SECP256R1())
    cert = issue(key.public_key(), "Zoo API", CERT_DAYS, names)
    pem_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return grpc.ssl_server_credentials(
        [(pem_key, cert.public_bytes(serialization.Encoding.PEM))],
        root_certificates=authority().pem,
        require_client_auth=True,
    )


def serve(port: int = PORT, bind: str | None = None) -> tuple[grpc.Server, int]:
    """Starts the node endpoint. Returns the server and the port it listens on (for port 0)."""
    names = [e.rsplit(":", 1)[0].strip("[]") for e in endpoints()] + ["localhost", "127.0.0.1"]
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=int(os.environ.get("ZOO_NODE_MAX", "256"))),
        options=[("grpc.keepalive_time_ms", 30_000), ("grpc.http2.max_pings_without_data", 0)],
    )
    pb_grpc.add_NodeServicer_to_server(Servicer(), server)
    bound = server.add_secure_port(
        f"{bind or os.environ.get('ZOO_NODE_BIND', '[::]')}:{port}", server_credentials(names)
    )
    server.start()
    global listening
    listening = bound
    logger.info("zoo-node endpoint listening", extra={"port": bound})
    return server, bound


def stop(server: grpc.Server):
    for session in list(hub.sessions.values()):
        hub.detach(session)
    server.stop(grace=2)


def wait_connected(server_id: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if hub.connected(server_id):
            return True
        time.sleep(0.5 if gateway.enabled() else 0.2)
    return False
