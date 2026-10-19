"""SSH command execution shared by the macOS and Windows backends."""

import base64
import io
import re
import time
from urllib.parse import urlparse

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from paramiko.pkey import UnknownKeyType

from apps.server.errors import ZooError
from db.connection import db_manager

HOST_KEY = re.compile(
    r"^(ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|ssh-rsa) [A-Za-z0-9+/=]+$"
)


def validate_host_key(host_key: str) -> str:
    key = " ".join(host_key.split()[:2])
    if not HOST_KEY.match(key):
        raise ValueError("not an SSH host key")
    name, encoded = key.split()
    try:
        paramiko.PKey.from_type_string(name, base64.b64decode(encoded, validate=True))
    except (ValueError, paramiko.SSHException, UnknownKeyType) as error:
        raise ValueError("not an SSH host key") from error
    return key


def private_key() -> paramiko.Ed25519Key:
    from apps.server.security import SYSTEM, decrypt, encrypt

    with db_manager.session() as db:
        row = db.get_ssh_identity()
        if row is None:
            key = ed25519.Ed25519PrivateKey.generate()
            encoded = key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
            )
            db.create_ssh_identity(key_ciphertext=encrypt(encoded.decode(), db, SYSTEM))
            row = db.get_ssh_identity()
        if row is None:
            raise ZooError("unavailable", "the host SSH identity could not be stored", "Check the database.")
    return paramiko.Ed25519Key.from_private_key(io.StringIO(decrypt(row.key_ciphertext)))


def public_key() -> str:
    key = private_key()
    return f"{key.get_name()} {key.get_base64()}"


def open_client(server_id: str, url: str) -> paramiko.SSHClient:
    """Connects through zoo-node, verifying the host key supplied during its authenticated join."""
    from apps.server import nodes

    target = urlparse(url)
    if target.scheme != "node" or target.hostname != server_id or not target.username:
        raise ZooError("not_supported", "VM hosts must join with zoo-node", "Use the host's node join command.")
    with db_manager.session() as db:
        node = db.get_node_by_server(server_id=server_id)
    if node is None:
        raise ZooError("unavailable", "the host has no registered node", "Join the host with zoo-node.")
    name, encoded = validate_host_key(node.ssh_host_key).split()
    key = paramiko.PKey.from_type_string(name, base64.b64decode(encoded, validate=True))
    identity = private_key()
    sock = nodes.ssh_socket(server_id)
    client = paramiko.SSHClient()
    client.get_host_keys().add(server_id, name, key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            server_id,
            port=22,
            username=target.username,
            pkey=identity,
            timeout=15,
            sock=sock,
            allow_agent=False,
            look_for_keys=False,
        )
    except BaseException:
        client.close()
        sock.close()
        raise
    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(30)
    return client


def alive(client: paramiko.SSHClient | None) -> bool:
    return client is not None and client.get_transport() is not None and client.get_transport().is_active()


def execute(client: paramiko.SSHClient, command: str, stdin: bytes | None, timeout: float) -> tuple[int, bytes, bytes]:
    channel = client.get_transport().open_session(timeout=15)
    try:
        channel.exec_command(command)
        if stdin is not None:
            channel.sendall(stdin)
        channel.shutdown_write()
        out, err = bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        while True:
            if channel.recv_ready():
                out += channel.recv(65536)
            elif channel.recv_stderr_ready():
                err += channel.recv_stderr(65536)
            elif channel.exit_status_ready() and channel.eof_received:
                break
            elif time.monotonic() > deadline:
                raise TimeoutError(f"command timed out after {timeout}s")
            else:
                time.sleep(0.01)
        return channel.recv_exit_status(), bytes(out), bytes(err)
    finally:
        channel.close()


def output_of(result: tuple[int, bytes, bytes]) -> str:
    code, out, err = result
    if code != 0:
        raise RuntimeError((err or out).decode(errors="replace").strip() or f"exit code {code}")
    return out.decode(errors="replace")
