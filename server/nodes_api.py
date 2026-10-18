"""Joining Linux, macOS and Windows zoo-nodes and serving their builds."""

import io
import os
import tarfile
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from db.connection import db_manager
from db.generated.query import CreateServerParams
from server import macos, nodes
from server.auth_api import AuthApi, Member
from server.platforms import capabilities_of
from server.ssh import validate_host_key
from utils.time import to_stamp

PLATFORM_OF = {"linux": "linux", "darwin": "macos", "windows": "windows"}


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TokenResponse(BaseModel):
    token: str
    join_command: str  # on a host that is already set up (zoovm or Hyper-V)
    install_command: str  # on a new host: sets it up, installs zoo-node and joins
    expires_at: str


class JoinRequest(BaseModel):
    token: str = Field(min_length=1, max_length=2000)
    csr: str = Field(min_length=1, max_length=10_000)
    hostname: str = Field(default="", max_length=255)
    os: Literal["linux", "darwin", "windows"]
    arch: str = Field(max_length=20)
    # macOS and Windows hosts: the account the API signs in as through the node's SSH tunnel, and the host's key
    ssh_user: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    ssh_host_key: str = Field(min_length=1, max_length=2000)


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

    key = macos.public_key()
    if platform == "windows":
        return setup(platform, key, url).removesuffix('"') + f" -Token '{token}'\""
    return setup(platform, key, url) + f" --token '{token}'"


class NodesApi:
    def __init__(self, app: APIRouter, auth: AuthApi):
        self.app = app
        self.auth = auth
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/nodes/bootstrap/ssh-key", include_in_schema=False)
        def bootstrap_ssh_key():
            return Response(macos.public_key().strip() + "\n", media_type="text/plain", headers={"Cache-Control": "no-store"})

        @self.app.get("/nodes/bootstrap/macos", include_in_schema=False)
        def macos_bootstrap():
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
                for name in (
                    "base-prepare.sh",
                    "firstboot.sh",
                    "configure-guest.sh",
                    "guest-reidentify.sh",
                    "prepare-base.sh",
                ):
                    bundle.add(os.path.join(nodes.ROOT, "macos", name), arcname=name)
            return Response(
                archive.getvalue(),
                media_type="application/gzip",
                headers={
                    "Content-Disposition": 'attachment; filename="zoo-macos-bootstrap.tar.gz"',
                },
            )

        @self.app.post("/nodes/tokens", response_model=TokenResponse, status_code=201)
        def create_token(
            payload: TokenRequest,
            request: Request,
            platform: Literal["linux", "macos", "windows"] = "macos",
            user: Member = Depends(current_user),
        ) -> TokenResponse:
            url = api_url(request)
            token, row = nodes.create_token(user.id, user.workspace_id, payload.name, url)
            join_command = f"zoo-node join '{token}' --service"
            if platform == "windows":
                join_command += " --ssh-user $env:USERNAME"
            return TokenResponse(
                token=token,
                join_command=join_command,
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
            if payload.arch not in (("amd64",) if payload.os == "windows" else ("amd64", "arm64")):
                raise HTTPException(status_code=422, detail=f"unsupported node platform: {payload.os}/{payload.arch}")
            try:
                payload.ssh_host_key = validate_host_key(payload.ssh_host_key)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error))
            token = nodes.claim_token(secret)
            if token is None:
                raise HTTPException(status_code=401, detail="the join token is unknown, used or expired")
            node_id = str(uuid.uuid4())
            try:
                cert = nodes.sign_csr(payload.csr.encode(), node_id)
            except (ValueError, TypeError) as e:
                raise HTTPException(status_code=422, detail=str(e))
            with db_manager.session() as db:
                server_id = str(uuid.uuid4())
                server = db.create_server(
                    CreateServerParams(
                        id=server_id,
                        name=token.name,
                        docker_url=f"node://{payload.ssh_user}@{server_id}",
                        bind_address=payload.hostname or server_id,
                        platform=platform,
                        capabilities="linux"
                        if payload.os == "darwin" and payload.arch != "arm64"
                        else capabilities_of(platform),
                        created_by=token.created_by,
                        workspace_id=token.workspace_id,
                    )
                )
                if server is None:
                    raise RuntimeError("the server wasn't saved")
            node = nodes.register(
                server.id, cert, payload.os, payload.arch, payload.hostname, ssh_host_key=payload.ssh_host_key
            )
            return JoinResponse(
                node_id=node.id,
                server_id=server.id,
                certificate=nodes.pem(cert),
                ca=nodes.authority().pem.decode(),
                endpoints=nodes.endpoints(api_url(request)),
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

        @self.app.get("/nodes/install/{name}", include_in_schema=False)
        def installer(name: Literal["install.sh", "install-node.ps1"]):
            """Serve the installers shipped with this API image, so node support matches its builds."""
            relative = "install.sh" if name == "install.sh" else os.path.join("windows", name)
            return FileResponse(os.path.join(nodes.ROOT, relative), media_type="text/plain", filename=name)
