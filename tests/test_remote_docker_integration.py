"""Real Docker HTTP over pinned SSH; opt in with ZOO_TEST_DOCKER_SOCKET=/path/to/docker.sock.

Only the gateway's tunnel socket is substituted with a loopback socket. Enrollment's
host key verification, control-plane identity, SSH dial-stdio and Docker SDK are real.
"""

import os
import select
import socket
import threading
import uuid
from contextlib import suppress

import paramiko
import pytest
from paramiko.common import AUTH_FAILED, AUTH_SUCCESSFUL, OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED, OPEN_SUCCEEDED

from apps.server import nodes, remote_docker, ssh
from db.connection import db_manager
from tests.conftest import present, sql
from tests.test_nodes import fake_session
from tests.test_sandboxes import add_server


@pytest.mark.e2e
def test_real_docker_engine_over_pinned_node_ssh(client, alice, monkeypatch):
    engine_socket = os.environ.get("ZOO_TEST_DOCKER_SOCKET")
    image = os.environ.get("ZOO_TEST_DOCKER_IMAGE", "alpine:3.23")
    if not engine_socket:
        pytest.skip("set ZOO_TEST_DOCKER_SOCKET to exercise a real Docker engine")
    server_id = add_server("alice@example.com", "linux")
    fake_session(server_id, 16 << 30)
    host_key = paramiko.RSAKey.generate(2048)
    sql(
        "UPDATE nodes SET ssh_host_key = ? WHERE server_id = ?",
        f"{host_key.get_name()} {host_key.get_base64()}",
        server_id,
    )
    identity = ssh.private_key()
    accepted_commands = []
    stopped = threading.Event()
    sessions = []
    transports = []
    errors = []

    def bridge(channel):
        engine = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            engine.connect(engine_socket)
            while not stopped.is_set():
                readable, _, _ = select.select([channel, engine], [], [], 0.1)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (engine if source is channel else channel).sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            engine.close()
            channel.close()

    class SSHServer(paramiko.ServerInterface):
        def check_auth_publickey(self, username, key):
            return AUTH_SUCCESSFUL if username == "zoo" and key == identity else AUTH_FAILED

        def get_allowed_auths(self, username):
            return "publickey"

        def check_channel_request(self, kind, chanid):
            return OPEN_SUCCEEDED if kind == "session" else OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_exec_request(self, channel, command):
            accepted_commands.append(command)
            if command != b"docker system dial-stdio":
                return False
            task = threading.Thread(target=bridge, args=(channel,), daemon=True)
            sessions.append(task)
            task.start()
            return True

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)

    def accept():
        while not stopped.is_set():
            try:
                incoming, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            transport = paramiko.Transport(incoming)
            transports.append(transport)
            transport.add_server_key(host_key)
            try:
                transport.start_server(server=SSHServer())
                while transport.is_active() and not stopped.is_set():
                    # Retain accepted channels: otherwise Paramiko garbage collects them.
                    channel = transport.accept(timeout=0.1)
                    if channel is not None:
                        channels.append(channel)
            except Exception as error:
                errors.append(error)

    channels = []
    worker = threading.Thread(target=accept, daemon=True)
    worker.start()
    monkeypatch.setattr(nodes, "ssh_socket", lambda _: socket.create_connection(listener.getsockname(), timeout=5))
    with db_manager.session() as db:
        server = present(db.get_server(id=server_id))
    engine = None
    container = None
    volume = None
    network = None
    resource = f"zoo-ssh-check-{uuid.uuid4().hex[:12]}"
    try:
        engine = remote_docker.new_client(server)
        assert engine.ping()
        assert engine.info()["OSType"] == "linux"
        engine.images.get(image)  # Use an existing image; this test never pulls.
        volume = engine.volumes.create(name=resource)
        network = engine.networks.create(name=resource)
        container = engine.containers.run(
            image,
            command=["sh", "-c", "sleep 120"],
            name=resource,
            detach=True,
            network=resource,
            volumes={resource: {"bind": "/test-home", "mode": "rw"}},
            entrypoint=[],
        )
        result = container.exec_run(["sh", "-c", "printf ssh-ok >/test-home/probe; cat /test-home/probe"])
        assert result.exit_code == 0 and result.output == b"ssh-ok"
        container.pause()
        container.reload()
        assert container.attrs["State"]["Paused"]
        container.unpause()
        # The real adapter should recover after the outbound SSH session drops.
        for transport in transports[:]:
            transport.close()
        assert engine.ping()
        container.reload()
        assert container.status == "running"
        assert accepted_commands and set(accepted_commands) == {b"docker system dial-stdio"}
        assert not errors
    finally:
        if container is not None:
            with suppress(Exception):
                container.remove(force=True)
        if volume is not None:
            with suppress(Exception):
                volume.remove(force=True)
        if network is not None:
            with suppress(Exception):
                network.remove()
        if engine is not None:
            engine.close()
        stopped.set()
        listener.close()
        for transport in transports:
            transport.close()
        worker.join(timeout=3)
        for task in sessions:
            task.join(timeout=3)
