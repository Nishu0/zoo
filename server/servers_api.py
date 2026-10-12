import asyncio
import json
import logging
import os
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field

from db.connection import db_manager
from db.generated.models import Node, Profile, ProfileVersion, Server
from db.generated.query import CreateProfileParams, CreateProfileVersionParams, CreateServerParams, Querier
from server import kube, macos, nodes, objects, tickets, windows
from server.auth_api import AuthApi, Member
from server.docker import RUNTIME, connect, prepull, remotes, runtime_for
from server.platforms import PLATFORMS, capabilities_of, install_command, parse
from server.pool import KINDS as POOL_KINDS
from server.pool import MAX_SIZE as POOL_MAX
from server.pool import pool_id
from server.runtime import VMS, app_running, export_dir
from server.sandbox_api import PROFILE_APPS, SandboxApi, VncTicketResponse, platform_of, profile_key, profile_path
from server.security import audit, encrypt_bytes
from server.ssh import trust
from utils.time import to_stamp

logger = logging.getLogger(__name__)
ADMINS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def prepull_images(servers: list[Server]) -> threading.Thread:
    """Pulls the Linux sandbox images onto every server that runs Linux sandboxes, in the background."""

    def run():
        for server in servers:
            if "linux" not in parse(server.capabilities):
                continue
            try:
                prepull(server)
            except Exception as e:
                logger.warning("image pre-pull failed", extra={"server_id": server.id, "error": str(e)})

    thread = threading.Thread(target=run, name="prepull", daemon=True)
    thread.start()
    return thread


class ServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    docker_url: str = Field(pattern=r"^((ssh|tcp)://.+|local://)$")
    bind_address: str = Field(min_length=1, max_length=255)
    platform: Literal["linux", "macos", "windows"] = "linux"
    # the host's SSH key, from the node installer's join line; without it the API's known_hosts must have it
    host_key: str | None = Field(default=None, max_length=2000)


class NodeCheck(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class NodeDriver(BaseModel):
    name: str
    available: bool
    detail: str = ""


class NodeInfo(BaseModel):
    """The server's zoo-node and what it last reported."""

    id: str
    connected: bool
    version: str
    os: str
    arch: str
    hostname: str
    drivers: list[NodeDriver]
    cpus: int | None
    memory_total: int | None
    memory_available: int | None
    disk_total: int | None
    disk_free: int | None
    load: float | None
    sandboxes: int
    checks: list[NodeCheck]
    cert_expires_at: str
    seen_at: str | None


class ServerResponse(BaseModel):
    id: str
    name: str
    docker_url: str
    bind_address: str
    platform: str
    capabilities: list[str]
    created_at: str
    node: NodeInfo | None = None


class PlatformResponse(BaseModel):
    id: str
    name: str
    host: str
    kinds: list[str]
    runs: list[str]
    requirements: str
    servers: int  # this user's servers that can run it
    available: bool


class InstallCommand(BaseModel):
    platform: str
    command: str
    public_key: str
    requirements: str


class ServerStatus(BaseModel):
    online: bool
    error: str | None = None
    name: str | None = None
    os: str | None = None
    cpus: int | None = None
    memory_total: int | None = None
    docker_version: str | None = None
    microvm: bool | None = None
    containers_running: int | None = None
    sandboxes: int = 0


class BaseStatus(BaseModel):
    state: Literal["missing", "installing", "failed", "stopped", "running"]
    progress: float | None = None
    message: str | None = None
    ready: bool = False


class BaseInstallRequest(BaseModel):
    iso: str | None = Field(default=None, max_length=2000)
    edition: str | None = Field(default=None, max_length=100)


class PoolRequest(BaseModel):
    kind: Literal["desktop", "browser", "code"]
    server_id: str | None = None
    size: int = Field(ge=0, le=POOL_MAX)


class PoolEntry(BaseModel):
    kind: str
    server_id: str | None
    server_name: str
    size: int
    idle: int
    booting: int
    claimed: int
    error: str | None


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    app: str
    # save as a new version of this profile; without it, a profile with the same name and app gets a new version
    profile_id: str | None = None


class ProfileRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ProfileResponse(BaseModel):
    id: str
    name: str
    app: str
    platform: str
    size_bytes: int  # of the latest version
    version: int  # the latest
    versions: int
    created_at: str
    updated_at: str  # when the latest version was saved


class ProfileVersionResponse(BaseModel):
    version: int
    size_bytes: int
    sandbox_id: str | None
    created_at: str


# versions kept per profile; saving another removes the oldest
MAX_PROFILE_VERSIONS = 10
PROFILE_LABELS = {
    "firefox": "Firefox",
    "chromium": "Chromium",
    "chrome": "Chrome",
    "vscode": "VS Code",
    "safari": "Safari",
    "edge": "Edge",
}


def node_info(node: Node) -> NodeInfo:
    return NodeInfo(
        id=node.id,
        connected=nodes.hub.connected(node.server_id),
        version=node.version,
        os=node.os,
        arch=node.arch,
        hostname=node.hostname,
        drivers=[NodeDriver(**d) for d in json.loads(node.drivers)],
        cpus=node.cpus,
        memory_total=node.memory_total,
        memory_available=node.memory_available,
        disk_total=node.disk_total,
        disk_free=node.disk_free,
        load=node.load,
        sandboxes=len(json.loads(node.sandboxes)),
        checks=[NodeCheck(**c) for c in json.loads(node.checks)],
        cert_expires_at=str(node.cert_expires_at),
        seen_at=str(node.seen_at) if node.seen_at else None,
    )


def server_response(server: Server, node: Node | None = None) -> ServerResponse:
    fields = {
        k: getattr(server, k) for k in ServerResponse.model_fields if k not in ("capabilities", "node", "created_at")
    }
    return ServerResponse(
        **fields,
        created_at=to_stamp(server.created_at),
        capabilities=sorted(parse(server.capabilities)),
        node=node_info(node) if node else None,
    )


def profile_response(profile: Profile, db: Querier) -> ProfileResponse:
    versions = list(db.list_profile_versions(profile_id=profile.id))
    latest = versions[0] if versions else None
    return ProfileResponse(
        id=profile.id,
        name=profile.name,
        app=profile.app,
        platform=profile.platform,
        size_bytes=latest.size_bytes if latest else profile.size_bytes,
        version=latest.version if latest else 0,
        versions=len(versions),
        created_at=to_stamp(profile.created_at),
        updated_at=to_stamp(latest.created_at) if latest else to_stamp(profile.created_at),
    )


def version_response(version: ProfileVersion) -> ProfileVersionResponse:
    return ProfileVersionResponse(
        version=version.version,
        size_bytes=version.size_bytes,
        sandbox_id=version.sandbox_id,
        created_at=to_stamp(version.created_at),
    )


def remove_version_file(version_id: str):
    objects.delete(profile_key(version_id))


def probe(server: Server) -> dict:
    if server.platform in VMS:
        return VMS[server.platform].probe(server.id, server.docker_url)
    try:
        info = connect(server.id, server.docker_url).info()
    except Exception as e:
        remotes.pop(server.id, None)
        return {"online": False, "error": str(e)[:500]}
    return {
        "online": True,
        "name": info.get("Name"),
        "os": info.get("OperatingSystem"),
        "cpus": info.get("NCPU"),
        "memory_total": info.get("MemTotal"),
        "docker_version": info.get("ServerVersion"),
        "microvm": RUNTIME in info.get("Runtimes", {}),
        "containers_running": info.get("ContainersRunning"),
    }


def has_docker(server) -> bool:
    """Whether a Mac or Windows host also has a Docker that can run Linux sandboxes, reached over the same SSH."""
    if server.docker_url == macos.LOCAL:
        return False  # the API's own Docker on this Mac already runs Linux sandboxes
    try:
        runtime_for(connect(server.id, server.docker_url))
    except Exception:
        remotes.pop(server.id, None)
        return False
    return True


def capabilities(server) -> str:
    return capabilities_of(server.platform, server.platform == "linux" or has_docker(server))


class KubernetesCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class KubernetesStatus(BaseModel):
    enabled: bool
    namespace: str = ""
    runtime_class: str = ""
    checks: list[KubernetesCheck] = []


# the quick requirements check, at most this often; the probe (a pod per node) only runs when an admin asks
KUBE_CHECK_TTL = 30
_kube_checked: dict = {}


def kubernetes_status(probe: bool = False) -> KubernetesStatus:
    if not kube.enabled():
        return KubernetesStatus(enabled=False)
    if probe or not _kube_checked or time.monotonic() - _kube_checked["at"] > KUBE_CHECK_TTL:
        _kube_checked.update(at=time.monotonic(), checks=kube.check(probe=probe))
    return KubernetesStatus(
        enabled=True,
        namespace=kube.NAMESPACE,
        runtime_class=kube.RUNTIME_CLASS,
        checks=[KubernetesCheck(**c) for c in _kube_checked["checks"]],
    )


class ServersApi:
    def __init__(self, app: APIRouter, auth: AuthApi, sandboxes: SandboxApi):
        self.app = app
        self.auth = auth
        self.sandboxes = sandboxes
        self._register_routes()

    def owned(self, server_id: str, user: Member, db: Querier) -> Server:
        server = db.get_server(id=server_id)
        if server is None or server.workspace_id != user.workspace_id:
            raise HTTPException(status_code=404, detail="server not found")
        return server

    def pool_hosts(self, user: Member, db: Querier) -> dict[str | None, str]:
        """The hosts whose pools the user manages: their Linux-capable servers, and the API's own Docker for admins."""
        hosts: dict[str | None, str] = {None: "This machine"} if user.email.lower() in ADMINS else {}
        for server in db.list_servers_by_workspace(workspace_id=user.workspace_id):
            if "linux" in parse(server.capabilities):
                hosts[server.id] = server.name
        return hosts

    def pool_entries(self, hosts: dict[str | None, str]) -> list[PoolEntry]:
        keys = [(kind, server_id) for server_id in hosts for kind in POOL_KINDS]
        return [PoolEntry(**e, server_name=hosts[e["server_id"]]) for e in self.sandboxes.pool.status(keys)]

    def vm_server(self, server_id: str, user: Member):
        with db_manager.session() as db:
            server = self.owned(server_id, user, db)
        if server.platform not in VMS:
            raise HTTPException(status_code=400, detail="only macOS and Windows servers have a base VM")
        return server, VMS[server.platform]

    async def on_host(self, fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)[:500])

    def all_sandboxes(self):
        with db_manager.session() as db:
            return list(db.list_all_sandboxes())

    def profile(self, profile_id: str, user: Member, db: Querier) -> Profile:
        profile = db.get_profile(id=profile_id)
        if profile is None or profile.workspace_id != user.workspace_id:
            raise HTTPException(status_code=404, detail="profile not found")
        return profile

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/kubernetes", response_model=KubernetesStatus)
        def kubernetes(_: Member = Depends(current_user)) -> KubernetesStatus:
            """Whether this machine's Linux sandboxes run on Kubernetes, and what the cluster lacks for them."""
            return kubernetes_status()

        @self.app.post("/kubernetes/check", response_model=KubernetesStatus)
        def kubernetes_check(user: Member = Depends(current_user)) -> KubernetesStatus:
            """The requirements check with a probe pod under the RuntimeClass on every sandbox node (admins)."""
            admins = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
            if user.email.lower() not in admins:
                raise HTTPException(status_code=403, detail="admin only")
            return kubernetes_status(probe=True)

        @self.app.get("/servers", response_model=list[ServerResponse])
        def list_servers(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ServerResponse]:
            macos.ensure_local_server(user.id, user.workspace_id, db)
            by_server = {n.server_id: n for n in db.list_nodes()}
            return [
                server_response(s, by_server.get(s.id))
                for s in db.list_servers_by_workspace(workspace_id=user.workspace_id)
            ]

        @self.app.post("/servers", response_model=ServerResponse, status_code=201)
        async def add_server(payload: ServerRequest, user: Member = Depends(current_user)) -> ServerResponse:
            if payload.platform == "macos" and not payload.docker_url.startswith(("ssh://", macos.LOCAL)):
                raise HTTPException(status_code=422, detail="macOS servers use ssh://user@host or local://")
            if payload.platform == "windows" and not payload.docker_url.startswith("ssh://"):
                raise HTTPException(status_code=422, detail="Windows servers use ssh://user@host")
            if payload.platform == "linux" and payload.docker_url == macos.LOCAL:
                raise HTTPException(
                    status_code=422, detail="local:// is only for macOS; this machine's Docker is built in"
                )
            if payload.host_key and payload.docker_url.startswith("ssh://"):
                target = urlparse(payload.docker_url)
                try:
                    trust(target.hostname or "", target.port or 22, payload.host_key)
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=str(e))
            fields = payload.model_dump(exclude={"host_key"})
            server = SimpleNamespace(id=str(uuid.uuid4()), **fields)
            status = await asyncio.to_thread(probe, server)
            if not status["online"]:
                raise HTTPException(status_code=400, detail=f"cannot reach server: {status['error']}")
            if not status["microvm"]:
                if payload.platform == "windows":
                    raise HTTPException(status_code=400, detail="Hyper-V is not enabled on this server")
                raise HTTPException(
                    status_code=400, detail=f"docker runtime '{RUNTIME}' is not configured on this server"
                )
            caps = await asyncio.to_thread(capabilities, server)
            with db_manager.session() as db:
                created = db.create_server(
                    CreateServerParams(
                        id=server.id, created_by=user.id, workspace_id=user.workspace_id, capabilities=caps, **fields
                    )
                )
            if created is None:
                raise RuntimeError("server wasn't saved")
            prepull_images([created])
            return server_response(created)

        @self.app.get("/platforms", response_model=list[PlatformResponse])
        def list_platforms(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[PlatformResponse]:
            """Every sandbox OS, and whether this install can run it. The control plane's own Docker runs Linux."""
            macos.ensure_local_server(user.id, user.workspace_id, db)
            servers = list(db.list_servers_by_workspace(workspace_id=user.workspace_id))
            result = []
            for p in PLATFORMS.values():
                count = sum(1 for s in servers if p.id in parse(s.capabilities))
                result.append(
                    PlatformResponse(
                        **{k: getattr(p, k) for k in ("id", "name", "host", "requirements")},
                        kinds=list(p.kinds),
                        runs=list(p.runs),
                        servers=count,
                        available=p.id == "linux" or count > 0,
                    )
                )
            return result

        @self.app.get("/servers/install-command", response_model=InstallCommand)
        def server_install_command(
            platform: Literal["linux", "macos", "windows"], request: Request, user: Member = Depends(current_user)
        ) -> InstallCommand:
            try:
                key = macos.public_key()
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=f"{e}; set ZOO_SSH_DIR to a folder with an SSH key pair")
            api_url = os.environ.get("ZOO_API_URL") or str(request.base_url).rstrip("/")
            return InstallCommand(
                platform=platform,
                command=install_command(platform, key, api_url),
                public_key=key,
                requirements=PLATFORMS[platform].requirements,
            )

        @self.app.get("/servers/{server_id}/status", response_model=ServerStatus)
        async def server_status(server_id: str, user: Member = Depends(current_user)) -> ServerStatus:
            with db_manager.session() as db:
                server = self.owned(server_id, user, db)
                count = sum(1 for s in db.list_all_sandboxes() if s.server_id == server.id and s.status == "running")
            status = await asyncio.to_thread(probe, server)
            if status["online"]:
                # Docker may have been installed or removed since the server was added
                caps = await asyncio.to_thread(capabilities, server)
                if caps != server.capabilities:
                    with db_manager.session() as db:
                        db.update_server_capabilities(capabilities=caps, id=server.id)
            return ServerStatus(**status, sandboxes=count)

        @self.app.get("/servers/{server_id}/base", response_model=BaseStatus)
        async def base_status(server_id: str, user: Member = Depends(current_user)) -> BaseStatus:
            server, vms = self.vm_server(server_id, user)
            return BaseStatus(**await self.on_host(vms.base_status, server.id))

        @self.app.post("/servers/{server_id}/base/install", response_model=BaseStatus)
        async def base_install(
            server_id: str, payload: BaseInstallRequest | None = None, user: Member = Depends(current_user)
        ) -> BaseStatus:
            server, vms = self.vm_server(server_id, user)
            status = await self.on_host(vms.base_status, server.id)
            if status["state"] not in ("missing", "failed"):
                raise HTTPException(status_code=409, detail=f"base VM is {status['state']}")
            if vms is windows:
                if payload is None or not payload.iso:
                    raise HTTPException(status_code=422, detail="give the path or URL of a Windows ISO on the server")
                await self.on_host(windows.base_install, server.id, payload.iso.strip(), payload.edition)
            else:
                await self.on_host(macos.base_install, server.id)
            return BaseStatus(state="installing", message="starting")

        @self.app.post("/servers/{server_id}/base/start", response_model=BaseStatus)
        async def base_start(server_id: str, user: Member = Depends(current_user)) -> BaseStatus:
            server, vms = self.vm_server(server_id, user)
            status = await self.on_host(vms.base_status, server.id)
            if status["state"] != "stopped":
                raise HTTPException(status_code=409, detail=f"base VM is {status['state']}")
            # Windows sandboxes boot from frozen templates of the base, so only macOS needs them stopped.
            if vms is macos and any(
                s.server_id == server.id and s.status in ("running", "provisioning") for s in self.all_sandboxes()
            ):
                raise HTTPException(
                    status_code=409, detail="stop this server's macOS sandboxes before editing the base VM"
                )
            await self.on_host(vms.base_start, server.id)
            return BaseStatus(state="running")

        @self.app.post("/servers/{server_id}/base/stop", response_model=BaseStatus)
        async def base_stop(server_id: str, user: Member = Depends(current_user)) -> BaseStatus:
            server, vms = self.vm_server(server_id, user)
            await self.on_host(vms.base_stop, server.id)
            return BaseStatus(state="stopped")

        @self.app.post("/servers/{server_id}/base/setup", status_code=204)
        async def base_setup(server_id: str, user: Member = Depends(current_user)):
            server, vms = self.vm_server(server_id, user)
            if vms is not macos:
                raise HTTPException(status_code=400, detail="Windows base VMs set themselves up during install")
            await self.on_host(macos.base_setup, server.id)

        @self.app.post("/servers/{server_id}/base/vnc-ticket", response_model=VncTicketResponse)
        def base_vnc_ticket(server_id: str, user: Member = Depends(current_user)) -> VncTicketResponse:
            server, _ = self.vm_server(server_id, user)
            return VncTicketResponse(ticket=tickets.issue(user.id, f"server:{server.id}"))

        @self.app.websocket("/servers/{server_id}/base/ws")
        async def base_screen(websocket: WebSocket, server_id: str, ticket: str = ""):
            user_id = tickets.redeem(ticket, f"server:{server_id}")
            with db_manager.session() as db:
                server = db.get_server(id=server_id)
                member = (
                    db.get_workspace_member(workspace_id=server.workspace_id, user_id=user_id)
                    if server is not None and user_id is not None
                    else None
                )
            admin = member is not None and member.status == "active" and member.role in ("admin", "owner")
            if not admin or server is None or server.platform not in VMS:
                await websocket.close(code=1008, reason="server not available")
                return
            await websocket.accept()
            await self.sandboxes.proxy_vnc(websocket, VMS[server.platform].base_id(server.id))

        @self.app.delete("/servers/{server_id}", status_code=204)
        def delete_server(
            server_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ):
            server = self.owned(server_id, user, db)
            if any(s.server_id == server.id for s in db.list_all_sandboxes()):
                raise HTTPException(status_code=409, detail="move or delete the sandboxes on this server first")
            self.sandboxes.pool.drain(server)
            db.detach_server(server_id=server.id)
            db.delete_server(id=server.id)
            nodes.hub.drop(server.id)
            remotes.pop(server.id, None)
            macos.forget(server.id)
            windows.forget(server.id)

        @self.app.get("/pool", response_model=list[PoolEntry])
        def list_pool(user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)):
            return self.pool_entries(self.pool_hosts(user, db))

        @self.app.put("/pool", response_model=PoolEntry)
        def set_pool(
            payload: PoolRequest, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> PoolEntry:
            hosts = self.pool_hosts(user, db)
            if payload.server_id not in hosts:
                raise HTTPException(
                    status_code=403 if payload.server_id is None else 404,
                    detail="only admins set this machine's pool" if payload.server_id is None else "server not found",
                )
            key = pool_id(payload.kind, payload.server_id)
            db.set_pool_size(id=key, kind=payload.kind, server_id=payload.server_id, size=payload.size)
            audit(db, user, "pool.size", "pool", key, None, size=payload.size)
            self.sandboxes.pool.kick()
            entry = self.pool_entries({payload.server_id: hosts[payload.server_id]})[POOL_KINDS.index(payload.kind)]
            # the entry is read outside this request's transaction, which hasn't committed the new size yet
            return entry.model_copy(update={"size": payload.size})

        @self.app.get("/profiles", response_model=list[ProfileResponse])
        def list_profiles(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ProfileResponse]:
            return [profile_response(p, db) for p in db.list_profiles_by_workspace(workspace_id=user.workspace_id)]

        @self.app.get("/profile-apps")
        def profile_apps(platform: str = "linux", user: Member = Depends(current_user)) -> dict[str, str]:
            if platform not in PROFILE_APPS:
                raise HTTPException(status_code=422, detail=f"platform must be one of {', '.join(PROFILE_APPS)}")
            return PROFILE_APPS[platform]

        @self.app.post("/sandboxes/{sandbox_id}/profiles", response_model=ProfileResponse, status_code=201)
        async def capture_profile(
            sandbox_id: str, payload: ProfileRequest, user: Member = Depends(current_user)
        ) -> ProfileResponse:
            with db_manager.session() as db:
                sandbox = self.sandboxes.running(sandbox_id, user, db)
                platform = platform_of(sandbox.kind)
                if payload.app not in PROFILE_APPS[platform]:
                    raise HTTPException(
                        status_code=422, detail=f"app must be one of {', '.join(PROFILE_APPS[platform])}"
                    )
                if payload.profile_id is not None:
                    existing = self.profile(payload.profile_id, user, db)
                    if existing.app != payload.app or existing.platform != platform:
                        raise HTTPException(
                            status_code=400,
                            detail=f"that profile holds {existing.platform} {existing.app}, not {platform} {payload.app}",
                        )
                else:
                    existing = db.find_profile(
                        workspace_id=user.workspace_id, name=payload.name, app=payload.app, platform=platform
                    )
            try:
                data = await asyncio.to_thread(export_dir, sandbox.runtime_id, profile_path(platform, payload.app))
            except Exception:
                raise HTTPException(status_code=404, detail=f"no {payload.app} profile in this sandbox yet")
            version_id = str(uuid.uuid4())
            with db_manager.session() as db:
                token = encrypt_bytes(data, db, user.workspace_id)
            await asyncio.to_thread(objects.put, profile_key(version_id), token)
            with db_manager.session() as db:
                profile = existing
                if profile is None:
                    profile = db.create_profile(
                        CreateProfileParams(
                            id=str(uuid.uuid4()),
                            user_id=user.id,
                            name=payload.name,
                            app=payload.app,
                            size_bytes=len(data),
                            encrypted=True,
                            platform=platform,
                            workspace_id=user.workspace_id,
                        )
                    )
                    assert profile is not None
                version = db.create_profile_version(
                    CreateProfileVersionParams(
                        id=version_id,
                        profile_id=profile.id,
                        profile_id_2=profile.id,
                        size_bytes=len(data),
                        encrypted=True,
                        sandbox_id=sandbox.id,
                        created_by=user.id,
                    )
                )
                assert version is not None
                db.set_profile_latest(size_bytes=len(data), encrypted=True, id=profile.id)
                for old in list(db.list_profile_versions(profile_id=profile.id))[MAX_PROFILE_VERSIONS:]:
                    db.delete_profile_version(id=old.id)
                    remove_version_file(old.id)
                audit(
                    db,
                    user,
                    "profile.capture",
                    "profile",
                    profile.id,
                    sandbox.id,
                    name=profile.name,
                    app=profile.app,
                    version=version.version,
                )
                return profile_response(profile, db)

        @self.app.post("/sandboxes/{sandbox_id}/profiles/{profile_id}", response_model=ProfileResponse)
        async def apply_profile(
            sandbox_id: str, profile_id: str, version: int | None = None, user: Member = Depends(current_user)
        ) -> ProfileResponse:
            with db_manager.session() as db:
                sandbox = self.sandboxes.running(sandbox_id, user, db)
                profile = self.profile(profile_id, user, db)
                chosen = (
                    db.get_latest_profile_version(profile_id=profile.id)
                    if version is None
                    else db.get_profile_version(profile_id=profile.id, version=version)
                )
            if chosen is None:
                raise HTTPException(status_code=404, detail="profile version not found")
            if profile.platform != platform_of(sandbox.kind):
                raise HTTPException(status_code=400, detail=f"this profile is from a {profile.platform} sandbox")
            label = PROFILE_LABELS.get(profile.app, profile.app)
            try:
                running = await asyncio.to_thread(app_running, sandbox.runtime_id or "", profile.app)
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"couldn't check whether {label} is running: {e}")
            if running:
                # the app holds its profile open and would overwrite or corrupt what's loaded under it
                raise HTTPException(
                    status_code=409, detail=f"{label} is running in this sandbox; quit it, then load the profile"
                )
            await asyncio.to_thread(self.sandboxes.apply_profile, sandbox.runtime_id, profile, chosen)
            with db_manager.session() as db:
                audit(
                    db,
                    user,
                    "profile.load",
                    "profile",
                    profile.id,
                    sandbox.id,
                    name=profile.name,
                    app=profile.app,
                    version=chosen.version,
                )
                return profile_response(profile, db)

        @self.app.get("/profiles/{profile_id}/versions", response_model=list[ProfileVersionResponse])
        def list_profile_versions(
            profile_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ProfileVersionResponse]:
            profile = self.profile(profile_id, user, db)
            return [version_response(v) for v in db.list_profile_versions(profile_id=profile.id)]

        @self.app.delete("/profiles/{profile_id}/versions/{version}", response_model=ProfileResponse)
        def delete_profile_version(
            profile_id: str,
            version: int,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> ProfileResponse:
            profile = self.profile(profile_id, user, db)
            found = db.get_profile_version(profile_id=profile.id, version=version)
            if found is None:
                raise HTTPException(status_code=404, detail="profile version not found")
            if len(list(db.list_profile_versions(profile_id=profile.id))) == 1:
                raise HTTPException(status_code=409, detail="that's the only version; delete the profile instead")
            db.delete_profile_version(id=found.id)
            latest = db.get_latest_profile_version(profile_id=profile.id)
            assert latest is not None
            db.set_profile_latest(size_bytes=latest.size_bytes, encrypted=latest.encrypted, id=profile.id)
            audit(db, user, "profile.delete_version", "profile", profile.id, name=profile.name, version=version)
            remove_version_file(found.id)
            return profile_response(profile, db)

        @self.app.patch("/profiles/{profile_id}", response_model=ProfileResponse)
        def rename_profile(
            profile_id: str,
            payload: ProfileRename,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> ProfileResponse:
            profile = self.profile(profile_id, user, db)
            renamed = db.rename_profile(name=payload.name.strip(), id=profile.id)
            assert renamed is not None
            audit(db, user, "profile.rename", "profile", profile.id, name=renamed.name, previous=profile.name)
            return profile_response(renamed, db)

        @self.app.delete("/profiles/{profile_id}", status_code=204)
        def delete_profile(
            profile_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ):
            profile = self.profile(profile_id, user, db)
            versions = list(db.list_profile_versions(profile_id=profile.id))
            db.delete_profile(id=profile.id)
            audit(db, user, "profile.delete", "profile", profile.id, name=profile.name, app=profile.app)
            for version in versions:
                remove_version_file(version.id)
