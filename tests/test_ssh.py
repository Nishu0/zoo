import socket
from unittest.mock import Mock

import paramiko
import pytest

from apps.server import macos, nodes, ssh
from apps.server.errors import ZooError
from apps.server.security import decrypt
from db.connection import db_manager
from tests.conftest import present
from tests.test_nodes import HOST_KEY, csr
from tests.test_sandboxes import add_server


def test_host_keys_are_validated_and_comments_are_removed():
    assert ssh.validate_host_key(HOST_KEY + " zoo@host") == HOST_KEY


@pytest.mark.parametrize("key", ["not-a-key", "ssh-ed25519 !!!!", "ssh-ed25519 AAAA"])
def test_invalid_host_keys_are_rejected(key):
    with pytest.raises(ValueError, match="not an SSH host key"):
        ssh.validate_host_key(key)


def test_control_plane_identity_is_encrypted_and_reused_from_the_database():
    identity = ssh.private_key()
    public_key = ssh.public_key()
    assert public_key == f"{identity.get_name()} {identity.get_base64()}"
    with db_manager.session() as db:
        stored = present(db.get_ssh_identity())
    assert "-----BEGIN OPENSSH PRIVATE KEY-----" not in stored.key_ciphertext
    assert decrypt(stored.key_ciphertext).startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert ssh.private_key().get_base64() == identity.get_base64()
    with db_manager.session() as db:
        assert present(db.get_ssh_identity()).key_ciphertext == stored.key_ciphertext


@pytest.fixture
def registered_host(alice, monkeypatch):
    monkeypatch.setattr(nodes, "_authority", None)
    server_id = add_server("alice@example.com", "macos")
    _, request = csr()
    certificate = nodes.sign_csr(request.encode(), "ssh-host-node")
    nodes.register(server_id, certificate, "darwin", "arm64", "host", HOST_KEY)
    return server_id


@pytest.fixture
def ssh_connection(monkeypatch):
    client = Mock(spec=paramiko.SSHClient)
    client.get_host_keys.return_value = paramiko.HostKeys()
    tunnel = Mock(spec=socket.socket)
    monkeypatch.setattr(ssh.paramiko, "SSHClient", lambda: client)
    monkeypatch.setattr(nodes, "ssh_socket", lambda server_id: tunnel)
    return client, tunnel


def test_host_ssh_uses_the_node_tunnel_pinned_key_and_database_identity(registered_host, ssh_connection):
    client, tunnel = ssh_connection
    identity = ssh.private_key()
    assert ssh.open_client(registered_host, f"node://zoo@{registered_host}") is client
    client.connect.assert_called_once()
    args, options = client.connect.call_args
    assert args == (registered_host,)
    assert options["sock"] is tunnel and options["username"] == "zoo"
    assert options["pkey"].get_base64() == identity.get_base64()
    assert options["allow_agent"] is False and options["look_for_keys"] is False
    assert client.get_host_keys()[registered_host]["ssh-ed25519"].get_base64() == HOST_KEY.split()[1]
    assert isinstance(client.set_missing_host_key_policy.call_args.args[0], paramiko.RejectPolicy)
    client.load_system_host_keys.assert_not_called()
    client.get_transport.return_value.set_keepalive.assert_called_once_with(30)


def test_failed_host_ssh_closes_the_client_and_tunnel(registered_host, ssh_connection):
    client, tunnel = ssh_connection
    client.connect.side_effect = paramiko.SSHException("handshake failed")
    with pytest.raises(paramiko.SSHException, match="handshake failed"):
        ssh.open_client(registered_host, f"node://zoo@{registered_host}")
    client.close.assert_called_once()
    tunnel.close.assert_called_once()


def test_an_offline_node_prevents_host_ssh(registered_host, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(ssh.paramiko, "SSHClient", factory)
    with pytest.raises(ZooError) as error:
        ssh.open_client(registered_host, f"node://zoo@{registered_host}")
    assert error.value.code == "unavailable"
    factory.assert_not_called()


@pytest.mark.parametrize("name,username", [("guest_client", macos.USER), ("root_client", "root")])
def test_macos_guest_ssh_uses_the_database_identity(monkeypatch, name, username):
    identity = ssh.private_key()
    client = Mock(spec=paramiko.SSHClient)
    tunnel = Mock(spec=socket.socket)
    monkeypatch.setattr(macos, "guests", {})
    monkeypatch.setattr(macos, "roots", {})
    monkeypatch.setattr(macos, "check", lambda server_id, command: "192.168.64.2")
    monkeypatch.setattr(macos, "open_socket", lambda server_id, address: tunnel)
    monkeypatch.setattr(macos, "private_key", lambda: identity)
    monkeypatch.setattr(macos.paramiko, "SSHClient", lambda: client)
    assert getattr(macos, name)("macos:srv:zoo-sb") is client
    options = client.connect.call_args.kwargs
    assert options["pkey"] is identity and options["sock"] is tunnel
    assert options["username"] == username
    assert options["allow_agent"] is False and options["look_for_keys"] is False


def test_missing_macos_root_bootstrap_key_fails_clearly(monkeypatch):
    client = Mock(spec=paramiko.SSHClient)
    client.connect.side_effect = paramiko.AuthenticationException("root key rejected")
    monkeypatch.setattr(macos, "roots", {})
    monkeypatch.setattr(macos, "check", lambda server_id, command: "192.168.64.2")
    monkeypatch.setattr(macos, "open_socket", lambda server_id, address: Mock(spec=socket.socket))
    monkeypatch.setattr(macos.paramiko, "SSHClient", lambda: client)
    with pytest.raises(ZooError) as error:
        macos.root_client("macos:srv:zoo-sb")
    assert error.value.code == "guest_unavailable"
    assert "macos:srv:zoo-sb" not in macos.roots
    client.close.assert_called_once()
