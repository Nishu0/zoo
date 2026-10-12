"""Joining zoo-nodes (server/nodes.py), converting SSH servers to them, and their builds."""

import asyncio
import os
import shlex
import uuid
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db.connection import db_manager
from db.generated.models import Server
from db.generated.query import CreateServerParams
from server import docker, macos, nodes, windows
from server.auth_api import AuthApi, Member
from server.platforms import capabilities_of
from server.ssh import trust
from utils.time import to_stamp

PLATFORM_OF = {"linux": "linux", "darwin": "macos", "windows": "windows"}
ARCHES = ("amd64", "arm64")
JOIN_WAIT = 60


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TokenResponse(BaseModel):
    token: str
    join_command: str  # on a host that is already set up (Docker, Kata, zoovm or Hyper-V)
    install_command: str  # on a new host: sets it up, installs zoo-node and joins
    expires_at: str


class JoinRequest(BaseModel):
    token: str = Field(min_length=1, max_length=2000)
    csr: str = Field(min_length=1, max_length=10_000)
    hostname: str = Field(default="", max_length=255)
    os: Literal["linux", "darwin", "windows"]
    arch: str = Field(max_length=20)
    # macOS and Windows hosts: the account the API signs in as through the node's SSH tunnel, and the host's key
    ssh_user: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    ssh_host_key: str | None = Field(default=None, max_length=2000)


class JoinResponse(BaseModel):
    node_id: str
    server_id: str
    certificate: str
    ca: str
    endpoints: list[str]


def api_url(request: Request) -> str:
    return os.environ.get("ZOO_API_URL") or str(request.base_url).rstrip("/")


def install_command(platform: str, token: str, url: str) -> str:
    from server.platforms import install_command as setup

    try:
        key = macos.public_key()
    except RuntimeError:
        key = ""
    if platform == "windows":
        return setup(platform, key, url).removesuffix('"') + f" -Token '{token}'\""
    return setup(platform, key, url) + f" --token '{token}'"


def node_binary(os_name: str, arch: str) -> str:
    path = nodes.build(os_name, arch)
    if path is None:
        raise RuntimeError(
            f"this API has no zoo-node build for {os_name}/{arch} at its version ({nodes.VERSION}); "
            "run `make node-dist` or use the release image"
        )
    return path


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def migrate_linux(server: Server, token: str):
    """Docker over SSH already gives the API root on the host, so it installs zoo-node through Docker: a one-shot
    runc container with the host's filesystem copies the binary in and runs the join on the host (chroot), which
    installs the systemd service."""
    client = docker.connect(server.id, server.docker_url)
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(client.info().get("Architecture", ""), "")
    if arch not in ARCHES:
        raise RuntimeError("zoo-node runs on amd64 and arm64 Linux hosts")
    binary = read(node_binary("linux", arch))
    docker.ensure_image(client, docker.IMAGE)
    helper = client.containers.create(
        docker.IMAGE,
        entrypoint=["sleep", "600"],
        user="root",
        runtime="runc",
        privileged=True,
        pid_mode="host",
        network_mode="host",
        volumes={"/": {"bind": "/host", "mode": "rw"}},
        labels={"zoo.node.install": server.id},
    )
    try:
        helper.start()
        if not helper.put_archive("/host/usr/local/bin", docker.tar_file("zoo-node", binary, 0o755)):
            raise RuntimeError("couldn't copy zoo-node to the host")
        result = helper.exec_run(["chroot", "/host", "/usr/local/bin/zoo-node", "join", token, "--service"])
        if result.exit_code != 0:
            output = result.output.decode(errors="replace") if isinstance(result.output, bytes) else ""
            raise RuntimeError(output.strip()[-500:] or "zoo-node join failed")
    finally:
        helper.remove(force=True)


def migrate_macos(server: Server, token: str):
    binary = read(node_binary("darwin", "arm64"))
    macos.connect(server.id, server.docker_url)
    macos.check(server.id, "cat > /tmp/zoo-node.install && chmod 755 /tmp/zoo-node.install", stdin=binary)
    macos.check(
        server.id,
        f'/tmp/zoo-node.install join {shlex.quote(token)} --service --ssh-user "$(id -un)"; s=$?; '
        "rm -f /tmp/zoo-node.install; exit $s",
        timeout=120,
    )


def migrate_windows(server: Server, token: str):
    binary = read(node_binary("windows", "amd64"))
    windows.connect(server.id, server.docker_url)
    windows.write_host_file(server.id, "zoo-node.exe", binary)
    user = urlparse(server.docker_url).username or windows.USER
    windows.check(
        server.id,
        "$exe = Join-Path $env:USERPROFILE '.zoovm\\zoo-node.exe'\n"
        f"& $exe join {windows.q(token)} --service --ssh-user {windows.q(user)}\n"
        "$code = $LASTEXITCODE; Remove-Item -Force $exe -ErrorAction SilentlyContinue\n"
        'if ($code -ne 0) { throw "zoo-node join failed ($code)" }',
        timeout=120,
    )


MIGRATE = {"linux": migrate_linux, "macos": migrate_macos, "windows": migrate_windows}


def migrate(server: Server, user: Member, url: str) -> bool:
    """Converts an SSH server in place: installs zoo-node over the existing connection and joins it to the same
    server row. Its sandboxes stay where they are; Docker over SSH remains the fallback while the node is offline.
    Returns whether the node connected in time."""
    if server.docker_url.startswith(("node://", macos.LOCAL)):
        raise ValueError("only servers added over SSH can be migrated")
    token, _ = nodes.create_token(user.id, server.workspace_id, server.name, url, server_id=server.id)
    MIGRATE[server.platform](server, token)
    return nodes.wait_connected(server.id, JOIN_WAIT)


class NodesApi:
    def __init__(self, app: APIRouter, auth: AuthApi):
        self.app = app
        self.auth = auth
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.post("/nodes/tokens", response_model=TokenResponse, status_code=201)
        def create_token(
            payload: TokenRequest,
            request: Request,
            platform: Literal["linux", "macos", "windows"] = "linux",
            user: Member = Depends(current_user),
        ) -> TokenResponse:
            url = api_url(request)
            token, row = nodes.create_token(user.id, user.workspace_id, payload.name, url)
            return TokenResponse(
                token=token,
                join_command=f"sudo zoo-node join '{token}' --service"
                if platform == "linux"
                else f"zoo-node join '{token}' --service",
                install_command=install_command(platform, token, url),
                expires_at=to_stamp(row.expires_at),
            )

        @self.app.post("/nodes/join", response_model=JoinResponse)
        def join(payload: JoinRequest, request: Request) -> JoinResponse:
            """Called by `zoo-node join`, with no other credentials than the one-time token."""
            try:
                secret = nodes.decode_token(payload.token)["s"]
            except (ValueError, KeyError, TypeError):
                raise HTTPException(status_code=401, detail="not a valid join token")
            platform = PLATFORM_OF[payload.os]
            if platform != "linux" and not (payload.ssh_user and payload.ssh_host_key):
                raise HTTPException(status_code=422, detail="macOS and Windows nodes send ssh_user and ssh_host_key")
            token = nodes.claim_token(secret)
            if token is None:
                raise HTTPException(status_code=401, detail="the join token is unknown, used or expired")
            node_id = str(uuid.uuid4())
            try:
                cert = nodes.sign_csr(payload.csr.encode(), node_id)
            except (ValueError, TypeError) as e:
                raise HTTPException(status_code=422, detail=str(e))
            with db_manager.session() as db:
                if token.server_id is not None:
                    server = db.get_server(id=token.server_id)
                    if server is None:
                        raise HTTPException(status_code=404, detail="the server to migrate is gone")
                    if server.platform != platform:
                        raise HTTPException(
                            status_code=409, detail=f"this token is for a {server.platform} server, not {platform}"
                        )
                else:
                    server_id = str(uuid.uuid4())
                    url = f"node://{server_id}" if platform == "linux" else f"node://{payload.ssh_user}@{server_id}"
                    server = db.create_server(
                        CreateServerParams(
                            id=server_id,
                            name=token.name,
                            docker_url=url,
                            bind_address=payload.hostname or server_id,
                            platform=platform,
                            capabilities=capabilities_of(platform, False),
                            created_by=token.created_by,
                            workspace_id=token.workspace_id,
                        )
                    )
                    if server is None:
                        raise RuntimeError("the server wasn't saved")
            if payload.ssh_host_key and platform != "linux":
                host = urlparse(server.docker_url)
                try:
                    trust(host.hostname or "", host.port or 22, payload.ssh_host_key)
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=str(e))
            node = nodes.register(server.id, cert, payload.os, payload.arch, payload.hostname)
            docker.remotes.pop(server.id, None)
            return JoinResponse(
                node_id=node.id,
                server_id=server.id,
                certificate=nodes.pem(cert),
                ca=nodes.authority().pem.decode(),
                endpoints=nodes.endpoints(api_url(request)),
            )

        @self.app.post("/servers/{server_id}/migrate", status_code=204)
        async def migrate_server(server_id: str, request: Request, user: Member = Depends(current_user)):
            with db_manager.session() as db:
                server = db.get_server(id=server_id)
            if server is None or server.workspace_id != user.workspace_id:
                raise HTTPException(status_code=404, detail="server not found")
            try:
                connected = await asyncio.to_thread(migrate, server, user, api_url(request))
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"migration failed: {str(e)[:500]}")
            if not connected:
                raise HTTPException(
                    status_code=504,
                    detail="zoo-node was installed but hasn't connected yet; check that the host reaches the API's "
                    f"node endpoint ({', '.join(nodes.endpoints(api_url(request)))})",
                )

        @self.app.get("/nodes/download/{os_name}/{arch}")
        def download(os_name: Literal["linux", "darwin", "windows"], arch: Literal["amd64", "arm64"]):
            """This API's zoo-node build, for installers. The binary is the same for everyone, so it's public."""
            path = nodes.build(os_name, arch)
            if path is None:
                raise HTTPException(status_code=404, detail=f"no zoo-node build for {os_name}/{arch}")
            return FileResponse(
                path,
                media_type="application/octet-stream",
                filename=os.path.basename(path),
                headers={"X-Zoo-Version": nodes.VERSION, "X-Sha256": nodes.file_sha256(path)},
            )
