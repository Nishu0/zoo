"""Node CA, enrollment and gateway-backed SSH tunnels to macOS and Windows hosts."""

import asyncio
import base64
import contextlib
import datetime
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from db.connection import db_manager
from db.generated.models import Node, NodeToken
from db.generated.query import (
    CreateNodeTokenParams,
    UpsertNodeParams,
)
from server.errors import ZooError
from server.gateway_client import OwnershipChanged, client
from server.security import SYSTEM, decrypt, encrypt

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.environ.get("ZOO_NODE_DIST") or os.path.join(ROOT, "node", "dist")
PORT = int(os.environ.get("ZOO_NODE_PORT", "7443"))
TOKEN_PREFIX = "zn1."
TOKEN_TTL = 3600
CERT_DAYS = 365
# a node whose last report is older than this isn't counted on for capacity
FRESH_SECONDS = 90
UPDATE_CHUNK = 1 << 20
CHUNK = 64 * 1024


def api_version() -> str:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)["project"]["version"]


VERSION = api_version()


def endpoints(api_url: str = "") -> list[str]:
    """The API URL's host on ZOO_NODE_PORT."""
    host = os.environ.get("ZOO_NODE_HOST") or urlparse(os.environ.get("ZOO_API_URL") or api_url).hostname
    if not host:
        raise ZooError("unavailable", "public node endpoint is missing", "Set ZOO_API_URL or ZOO_NODE_HOST.")
    return [f"[{host}]:{PORT}" if ":" in host else f"{host}:{PORT}"]


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


class EnrollmentProofError(ValueError):
    """The caller did not prove possession of its claimed node identity."""


def enrollment_status(certificate: str, timestamp: int, nonce: str, signature: str) -> dict[str, str]:
    """Read-only proof that local credentials still belong to this control plane.

    Only an authenticated holder gets a definitive re-enrollment result. Storage
    and availability failures propagate so an installer cannot mistake downtime
    for permission to replace a working registration.
    """
    instant = now()
    if abs(instant.timestamp() - timestamp) > 120:
        raise EnrollmentProofError("node enrollment proof has expired; check the host clock")
    message = f"zoo-node-enrollment-v1\n{timestamp}\n{nonce}\n".encode()
    try:
        cert = x509.load_pem_x509_certificate(certificate.encode())
        key = cert.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise TypeError("node keys must be EC keys")
        key.verify(base64.b64decode(signature, validate=True), message, ec.ECDSA(hashes.SHA256()))
        common_name = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        if not isinstance(common_name, str):
            raise TypeError("node identity must be text")
        node_id = str(uuid.UUID(common_name))
        if node_id != common_name or cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError("invalid node identity")
        if ExtendedKeyUsageOID.CLIENT_AUTH not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
            raise ValueError("not a node client certificate")
    except (ValueError, TypeError, IndexError, InvalidSignature, x509.ExtensionNotFound) as error:
        raise EnrollmentProofError("invalid node enrollment proof") from error

    try:
        cert.verify_directly_issued_by(authority().certificate)
    except (ValueError, TypeError, InvalidSignature):
        return {"status": "reenroll", "reason": "authority_changed"}
    if instant < cert.not_valid_before_utc or instant >= expiry_of(cert):
        return {"status": "reenroll", "reason": "expired"}
    with db_manager.session() as db:
        node = db.get_node(id=node_id)
        if node is None or db.get_server(id=node.server_id) is None:
            return {"status": "reenroll", "reason": "registration_missing"}
    if node.serial != serial_of(cert) or node.cert_expires_at != expiry_of(cert):
        return {"status": "reenroll", "reason": "registration_replaced"}
    return {"status": "current", "node_id": node.id, "server_id": node.server_id}


def pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# --- join tokens ---


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_token(user_id: str, workspace_id: str, name: str, api_url: str) -> tuple[str, NodeToken]:
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


def register(server_id: str, cert: x509.Certificate, os_name: str, arch: str, hostname: str, ssh_host_key: str) -> Node:
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
                ssh_host_key=ssh_host_key,
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


class Hub:
    def __init__(self):
        self.tunnels: dict[str, set[asyncio.Task]] = {}

    async def status_async(self, server_id: str) -> dict:
        status = await client.node_status(server_id)
        report = json.loads(status.report_json) if status.report_json else {}
        return {**report, "connected": status.online}

    def status(self, server_id: str) -> dict:
        return client.sync(self.status_async(server_id))

    def connected(self, server_id: str) -> bool:
        return bool(self.status(server_id)["connected"])

    def targets(self, server_id: str) -> set[str]:
        return set(self.status(server_id).get("targets") or [])

    async def open_async(self, server_id: str, target: str) -> socket.socket:
        if target != "ssh":
            raise ZooError("not_supported", "node tunnels only support ssh")
        status = await self.status_async(server_id)
        if not status["connected"] or target not in status.get("targets", []):
            raise ZooError("unavailable", "the host's zoo-node cannot reach ssh", "Restart zoo-node on the host.")
        ours, theirs = socket.socketpair()
        ours.setblocking(False)
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self.pump(server_id, target, ours, ready), name=f"node-tunnel-{server_id}")
        self.tunnels.setdefault(server_id, set()).add(task)
        task.add_done_callback(lambda done: self.tunnels.get(server_id, set()).discard(done))
        try:
            async with asyncio.timeout(15):
                await asyncio.shield(ready)
        except BaseException as error:
            task.cancel()
            try:
                async with asyncio.timeout(5):
                    await task
            except (asyncio.CancelledError, TimeoutError):
                pass
            ours.close()
            theirs.close()
            if ready.done() and not ready.cancelled():
                ready.exception()
            if isinstance(error, TimeoutError):
                raise ZooError("timeout", "node SSH tunnel did not open") from error
            raise
        return theirs

    def open(self, server_id: str, target: str) -> socket.socket:
        return client.sync(self.open_async(server_id, target))

    async def pump(self, server_id: str, target: str, sock: socket.socket, ready: asyncio.Future):
        loop = asyncio.get_running_loop()

        async def requests():
            while data := await loop.sock_recv(sock, CHUNK):
                yield data

        try:
            for attempt in range(2):
                try:
                    async with contextlib.aclosing(client.node_stream(server_id, target, requests())) as stream:
                        async for data in stream:
                            await loop.sock_sendall(sock, data)
                            if not ready.done():
                                ready.set_result(None)
                    return
                except OwnershipChanged as error:
                    if attempt or not error.before_dispatch:
                        raise
                    await client.refresh()
        except Exception as error:
            if not ready.done():
                ready.set_exception(error)
            logger.debug("node tunnel ended", exc_info=True)
        finally:
            if not ready.done():
                ready.set_exception(ZooError("unavailable", "node tunnel closed before the SSH banner"))
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()

    async def drop_async(self, server_id: str):
        tasks = self.tunnels.pop(server_id, set())
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                async with asyncio.timeout(5):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except TimeoutError:
                logger.warning("node tunnel cleanup timed out", extra={"server_id": server_id})

    def drop(self, server_id: str):
        client.sync(self.drop_async(server_id))

    async def shutdown(self):
        await asyncio.gather(*(self.drop_async(server_id) for server_id in list(self.tunnels)))


hub = Hub()


def ssh_socket(server_id: str) -> socket.socket:
    return hub.open(server_id, "ssh")


def wait_connected(server_id: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if hub.connected(server_id):
            return True
        time.sleep(0.25)
    return False
