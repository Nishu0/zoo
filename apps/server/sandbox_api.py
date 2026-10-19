import asyncio
import base64
import contextlib
import inspect
import json
import os
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from apps.server import (
    audit_api,
    labels,
    macos,
    metrics,
    objects,
    pages,
    quotas,
    runtime,
    sizes,
    tickets,
    vm_control,
    windows,
    workers,
)
from apps.server.auth_api import AuthApi, Member, require, require_sandbox
from apps.server.cache import cache
from apps.server.errors import ZooError
from apps.server.gateway_client import client
from apps.server.guest import Terminal, guest_env, hub
from apps.server.images import MEDIA_TYPES
from apps.server.jobs import WAITING, Jobs
from apps.server.logger import logger
from apps.server.platforms import PLATFORMS, os_of, parse
from apps.server.pool import Pool
from apps.server.registry import KINDS, TOOLS, has_screen, responsive, start_browser
from apps.server.runtime import (
    IMAGE,
    VMS,
    connect,
    default_image,
    export_home,
    import_dir,
    import_home,
    is_running,
    is_vm,
    remove_container,
    remove_snapshot,
    remove_volume,
    restore_snapshot,
    run_container,
    snapshot,
    write_secrets,
)
from apps.server.schema import ExecRequest, ExecResponse
from apps.server.security import (
    VNC_PASSWORD,
    _booted,
    audit,
    decrypt,
    decrypt_async,
    decrypt_bytes,
    enforce,
    ensure_vnc_password,
    forget_secrets,
    redact,
    remember_secrets,
    secret_env,
)
from apps.server.telemetry import tracer
from apps.server.tools import FileSystem, ShellTools
from apps.server.utils import to_stamp
from apps.server.writer import writer
from db.async_connection import async_db
from db.connection import db_manager
from db.generated.models import Job, Sandbox, SandboxImageVersion
from db.generated.query import (
    CreateSandboxImageParams,
    CreateSandboxImageVersionParams,
    CreateSandboxParams,
    CreateSnapshotParams,
    Querier,
)

IMAGE_SLUG = "zoo-sandbox"
IMAGE_VERSION = "latest"
AUTO = "auto"
# PUT /sandboxes/{id}/files/content holds the body in memory
MAX_UPLOAD_BYTES = 100 << 20
PLATFORM_NAMES = {"macos": "macOS", "windows": "Windows"}


def profile_key(version_id: str) -> str:
    """Where a profile version's archive (encrypted) lives in object storage."""
    return f"profiles/{version_id}.tar"


# where each app keeps its profile, under the sandbox user's home, per OS
PROFILE_APPS = {
    "linux": {
        "firefox": ".mozilla",
        "chromium": ".config/chromium",
        "chrome": ".config/google-chrome",
        "vscode": ".config/Code",
    },
    "macos": {
        # Safari's data is behind Full Disk Access (macos/README.md)
        "safari": "Library/Containers/com.apple.Safari",
        "chrome": "Library/Application Support/Google/Chrome",
        "edge": "Library/Application Support/Microsoft Edge",
        "firefox": "Library/Application Support/Firefox",
        "vscode": "Library/Application Support/Code",
    },
    "windows": {
        "chrome": "AppData/Local/Google/Chrome/User Data",
        "edge": "AppData/Local/Microsoft/Edge/User Data",
        "firefox": "AppData/Roaming/Mozilla/Firefox",
        "vscode": "AppData/Roaming/Code",
    },
}
PROFILE_HOMES = {"linux": "/home/zoo", "macos": macos.HOME, "windows": windows.HOME}


def platform_of(kind: str) -> str:
    return kind if kind in VMS else "linux"


def profile_path(platform: str, app: str) -> str:
    return f"{PROFILE_HOMES[platform]}/{PROFILE_APPS[platform][app]}"


BROWSER_HOME = os.environ.get("ZOO_BROWSER_HOME", "https://duckduckgo.com")
# how long a boot may take, retries included, before the sandbox fails
BOOT_DEADLINE = {"linux": 3 * 60, "vm": 10 * 60}
# a macOS or Windows VM that neither draws nor sends its guest heartbeat for this long is restarted
HANG_SECONDS = 60
# how long a create may spend handing warm sandboxes over before it gives up
WARM_CEILING = 10
# a provisioning sandbox with no job this long after its last change was left by a create that died
STRANDED_SECONDS = 60
# a stopped Linux sandbox stays paused this long, then is deleted down to its volume
PAUSE_MINUTES = float(os.environ.get("ZOO_PAUSE_MINUTES", "30"))
# below this share of free memory on a node, its paused sandboxes are deleted first, oldest first
PRESSURE_SHARE = 0.1
SWEEP_SECONDS = 10


async def relay_screen(websocket: WebSocket, identity: str, view_only: bool):
    """Relays an accepted viewer websocket to a VNC session in the gateway (a sandbox's, or "base:<server id>"),
    which plays the VNC server to the viewer and drops a view-only viewer's input."""

    async def received() -> AsyncIterator[bytes]:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            yield message.get("bytes") or (message.get("text") or "").encode()

    try:
        async for data in client.viewer_stream(identity, view_only, received()):
            await websocket.send_bytes(data)
    except Exception as e:
        logger.error("viewer error", extra={"screen": identity, "error": repr(e)})
    finally:
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close()


def boot_deadline(kind: str) -> int:
    return BOOT_DEADLINE["vm" if kind in VMS else "linux"]


class CreateSandboxRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    kind: Literal["desktop", "browser", "code", "macos", "windows"] = "desktop"
    server_id: str | None = None
    profile_ids: list[str] = []
    secret_ids: list[str] = []
    # macOS only: the guest user keeps admin rights and passwordless sudo, so an agent can undo the VM's own policy
    admin: bool = False
    # macOS only: when every Mac already runs Apple's limit of 2 macOS VMs, wait in the queue for one to stop
    # instead of failing with "Mac full"
    queue: bool = True
    # Linux only (server/sizes.py); no disk size: the host's usual disk
    cpus: float = Field(default=sizes.DEFAULT.cpus, gt=0, le=sizes.MAX_CPUS)
    memory_mb: int = Field(default=sizes.DEFAULT.memory_mb, ge=256, le=sizes.MAX_MEMORY_MB)
    disk_gb: int | None = Field(default=None, ge=1, le=sizes.MAX_DISK_GB)
    # stop after this many minutes without a tool call or an open viewer, and after this many minutes running at
    # all; null: the workspace's default
    idle_timeout_minutes: int | None = Field(default=None, gt=0)
    max_lifetime_minutes: int | None = Field(default=None, gt=0)
    # e.g. {"end_user": "alice"} (server/labels.py)
    labels: dict[str, str] = {}

    @field_validator("labels")
    @classmethod
    def check_labels(cls, value: dict[str, str]) -> dict[str, str]:
        return labels.validate(value)

    @property
    def size(self) -> sizes.Size:
        return sizes.Size(self.cpus, self.memory_mb, self.disk_gb)


class UpdateSandboxRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    # replaces every label; null leaves them as they are
    labels: dict[str, str] | None = None

    @field_validator("labels")
    @classmethod
    def check_labels(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return labels.validate(value) if value is not None else value


class LifecycleRequest(BaseModel):
    idle_timeout_minutes: int | None = Field(default=None, gt=0)
    max_lifetime_minutes: int | None = Field(default=None, gt=0)


class MoveSandboxRequest(BaseModel):
    server_id: str | None = None
    node_name: str | None = Field(default=None, min_length=1, max_length=253)


class JobResponse(BaseModel):
    kind: str
    state: str
    attempts: int
    max_attempts: int
    last_error: str | None
    deadline: str | None
    # waiting for room (a full Mac) rather than retrying after an error; last_error says what it waits for
    waiting: bool = False


class SandboxResponse(BaseModel):
    id: str
    name: str
    kind: str
    server_id: str | None
    status: str
    error_message: str | None
    started_at: str | None
    created_at: str
    # the runtime host didn't answer the last health check; the sandbox may still be fine
    unreachable: bool = False
    # stopped but frozen in place: the next start resumes it instantly, until the pause sweeper deletes it
    resumable: bool = False
    # the boot, stop, delete or move in progress, if any
    job: JobResponse | None = None
    # the image a Linux sandbox boots from; it stays on it across upgrades until restarted on the new one
    image: str | None = None
    image_outdated: bool = False
    # macOS and Windows: the base VM version it was cloned from, how long its last boot took, and when the API last restarted
    # it because it hung (no screen updates and no guest heartbeat)
    base_version: str | None = None
    boot_seconds: float | None = None
    recovered_at: str | None = None
    cpus: float = sizes.DEFAULT.cpus
    memory_mb: int = sizes.DEFAULT.memory_mb
    disk_gb: int | None = None
    idle_timeout_minutes: int | None = None
    max_lifetime_minutes: int | None = None
    last_activity_at: str | None = None
    labels: dict[str, str] = {}


class ToolExecutionResponse(BaseModel):
    id: str
    tool_name: str
    status: str
    input: str
    error_message: str | None
    created_at: str
    completed_at: str | None


class VncTicketResponse(BaseModel):
    ticket: str
    expires_in: int = tickets.TICKET_TTL


class DeleteSandboxResponse(BaseModel):
    deleted: bool = True
    id: str


def pinned_image(sandbox: Sandbox) -> str | None:
    """The default image this sandbox first booted from, or None if it hasn't booted or runs a custom image."""
    return json.loads(sandbox.config or "{}").get("image")


MAX_SNAPSHOTS = int(os.environ.get("ZOO_MAX_SNAPSHOTS", "10"))
# how often an open viewer counts as activity, and how often idle and old sandboxes are stopped
VIEWER_TOUCH_SECONDS = 60
EXPIRE_SECONDS = 60
# how soon a viewer on a revoked or expired link is disconnected
LINK_CHECK_SECONDS = 15


class SnapshotRequest(BaseModel):
    name: str = Field(default="", max_length=100)


class SnapshotResponse(BaseModel):
    id: str
    name: str
    size_bytes: int
    state: str
    error: str | None
    created_at: str


def snapshot_response(row) -> SnapshotResponse:
    return SnapshotResponse(
        **{k: getattr(row, k) for k in SnapshotResponse.model_fields if k != "created_at"},
        created_at=to_stamp(row.created_at),
    )


def to_response(
    sandbox: Sandbox, db: Querier | None = None, sandbox_labels: dict[str, str] | None = None
) -> SandboxResponse:
    """The API's view of a sandbox; with db, its job in progress, and its labels unless given."""
    job = db.get_active_job(sandbox_id=sandbox.id) if db is not None else None
    if sandbox_labels is None and db is not None:
        sandbox_labels = labels.of(db, sandbox.id)
    image = pinned_image(sandbox)
    return SandboxResponse(
        id=sandbox.id,
        name=sandbox.name,
        kind=sandbox.kind,
        server_id=sandbox.server_id,
        status=sandbox.status,
        error_message=sandbox.error_message,
        started_at=to_stamp(sandbox.started_at),
        created_at=to_stamp(sandbox.created_at),
        unreachable=sandbox.unreachable_since is not None,
        resumable=sandbox.status == "stopped" and sandbox.paused_at is not None,
        job=JobResponse(
            **{k: getattr(job, k) for k in JobResponse.model_fields if k not in ("waiting", "deadline")},
            deadline=to_stamp(job.deadline),
            waiting=(job.last_error or "").startswith(WAITING),
        )
        if job
        else None,
        image=image,
        image_outdated=image is not None and image != default_image(sandbox.kind),
        base_version=sandbox.base_version,
        boot_seconds=sandbox.boot_seconds,
        recovered_at=to_stamp(sandbox.recovered_at),
        cpus=sandbox.cpus,
        memory_mb=sandbox.memory_mb,
        disk_gb=sandbox.disk_gb,
        idle_timeout_minutes=sandbox.idle_timeout_minutes,
        max_lifetime_minutes=sandbox.max_lifetime_minutes,
        last_activity_at=to_stamp(sandbox.last_activity_at),
        labels=sandbox_labels or {},
    )


class SandboxApi:
    def __init__(self, app: APIRouter, auth: AuthApi, unversioned: FastAPI):
        self.logger = logger
        self.app = app
        # routes that are not part of the versioned API (/guest/connect)
        self.unversioned = unversioned
        self.auth = auth
        self.jobs = Jobs()
        # macOS sandbox id -> when it was first found hung, and when its screen was last checked
        self.suspects: dict[str, float] = {}
        # the activity tasks of open viewers (watch_viewer), held so they aren't collected mid-run
        self.viewers: set[asyncio.Task] = set()
        self.probed: dict[str, float] = {}
        self.pool = Pool()
        self.executions = writer
        self.jobs.register("boot", self.boot, attempts=3, backoff=(5, 15), failed=self.boot_failed)
        self.jobs.register("stop", self.stop, attempts=3, backoff=(5, 15), failed=self.lifecycle_failed("stop"))
        self.jobs.register(
            "delete", self.delete, attempts=5, backoff=(10, 30, 60, 120), failed=self.lifecycle_failed("delete")
        )
        self.jobs.register("move", self.move, attempts=2, backoff=(30,), failed=self.lifecycle_failed("move"))
        self.jobs.register("snapshot", self.take_snapshot, attempts=1, failed=self.snapshot_failed)
        self.jobs.register(
            "restore", self.restore_job, attempts=2, backoff=(15,), failed=self.lifecycle_failed("restore")
        )
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/sandboxes", response_model=pages.Page[SandboxResponse])
        def list_sandboxes(
            label: list[str] = Query(default=[], description="key=value; repeat to match all of them"),
            page: pages.Params = Depends(),
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> pages.Page[SandboxResponse]:
            wanted = labels.parse_filters(label)
            labelled = labels.of_workspace(db, user.workspace_id)
            rows, after = pages.collect(
                lambda at, id, limit: db.page_sandboxes(
                    workspace_id=user.workspace_id, before_at=at, before_id=id, row_limit=limit
                ),
                lambda s: (
                    user.key_sandbox_id in (None, s.id)
                    and labels.matches(labelled.get(s.id, {}), wanted)
                    and labels.matches(labelled.get(s.id, {}), user.key_labels)
                ),
                lambda s: (s.created_at, s.id),
                page,
            )
            return pages.Page(data=[to_response(s, db, labelled.get(s.id, {})) for s in rows], next_cursor=after)

        @self.app.post("/sandboxes", response_model=SandboxResponse, status_code=201)
        async def create_sandbox(
            payload: CreateSandboxRequest, user: Member = Depends(current_user)
        ) -> SandboxResponse:
            return await self.create_async(payload, user)

        @self.app.get("/sandboxes/{sandbox_id}", response_model=SandboxResponse)
        async def get_sandbox(sandbox_id: str, user: Member = Depends(current_user)) -> SandboxResponse:
            return await self.response_async(sandbox_id, user)

        @self.app.patch("/sandboxes/{sandbox_id}", response_model=SandboxResponse)
        def update_sandbox(
            sandbox_id: str,
            payload: UpdateSandboxRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> SandboxResponse:
            sandbox = self.owned(sandbox_id, user, db)
            if payload.name is not None:
                sandbox = db.rename_sandbox(name=payload.name, id=sandbox.id)
                assert sandbox is not None
            if payload.labels is not None:
                wanted = labels.scoped(payload.labels, user.key_labels)
                current = labels.of(db, sandbox.id)
                if sandbox.status in ("pending", "provisioning", "running"):
                    # what changes holds to the new values' quotas, as a start would
                    added = {k: v for k, v in wanted.items() if current.get(k) != v}
                    quotas.check(db, sandbox.workspace_id, sizes.of(sandbox), new=False, labels=added)
                labels.put(db, sandbox.id, wanted)
            return to_response(sandbox, db)

        @self.app.post("/sandboxes/{sandbox_id}/start", response_model=SandboxResponse)
        def start_sandbox(sandbox_id: str, user: Member = Depends(current_user)) -> SandboxResponse:
            with db_manager.session() as db:
                sandbox = self.idle(sandbox_id, user, db)
                if sandbox.status in ("running", "provisioning"):
                    raise HTTPException(status_code=409, detail=f"sandbox is {sandbox.status}")
                quotas.check(db, sandbox.workspace_id, sizes.of(sandbox), new=False, labels=labels.of(db, sandbox.id))
                sandbox = db.update_sandbox_status(status="provisioning", id=sandbox.id)
                self.jobs.enqueue(db, sandbox.id, "boot", boot_deadline(sandbox.kind))
                response = to_response(sandbox, db)
            self.jobs.kick()
            return response

        @self.app.post("/sandboxes/{sandbox_id}/stop", response_model=SandboxResponse)
        def stop_sandbox(sandbox_id: str, user: Member = Depends(current_user)) -> SandboxResponse:
            with db_manager.session() as db:
                sandbox = self.owned(sandbox_id, user, db)
                active = db.get_active_job(sandbox_id=sandbox.id)
                if active is not None and active.kind in ("stop", "delete"):
                    raise HTTPException(status_code=409, detail=f"sandbox is busy: {active.kind} in progress")
                # a boot that hasn't started yet is dropped; one already running finishes first, then this stops it
                db.cancel_sandbox_jobs(sandbox_id=sandbox.id)
                self.jobs.enqueue(db, sandbox.id, "stop")
                response = to_response(sandbox, db)
            self.jobs.kick()
            return response

        @self.app.put("/sandboxes/{sandbox_id}/lifecycle", response_model=SandboxResponse)
        def set_lifecycle(
            sandbox_id: str,
            payload: LifecycleRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> SandboxResponse:
            sandbox = self.owned(sandbox_id, user, db)
            db.set_sandbox_lifecycle(
                idle_timeout_minutes=payload.idle_timeout_minutes,
                max_lifetime_minutes=payload.max_lifetime_minutes,
                id=sandbox.id,
            )
            return to_response(self.owned(sandbox_id, user, db), db)

        @self.app.post("/sandboxes/{sandbox_id}/upgrade", response_model=SandboxResponse)
        def upgrade_sandbox(sandbox_id: str, user: Member = Depends(current_user)) -> SandboxResponse:
            """Restarts the sandbox on the current default image. Its home volume is kept."""
            with db_manager.session() as db:
                sandbox = self.idle(sandbox_id, user, db)
                if pinned_image(sandbox) is None:
                    raise HTTPException(status_code=400, detail="this sandbox doesn't run the default image")
                if sandbox.status not in ("running", "stopped", "failed"):
                    raise HTTPException(status_code=409, detail=f"sandbox is {sandbox.status}")
                config = {**json.loads(sandbox.config or "{}"), "image": default_image(sandbox.kind)}
                db.update_sandbox(
                    name=sandbox.name, resources=sandbox.resources, config=json.dumps(config), id=sandbox.id
                )
                if sandbox.status == "running":
                    self.jobs.enqueue(db, sandbox.id, "stop", restart=True)
                else:
                    db.update_sandbox_status(status="provisioning", id=sandbox.id)
                    self.jobs.enqueue(db, sandbox.id, "boot", boot_deadline(sandbox.kind), upgrade=True)
                response = to_response(self.owned(sandbox.id, user, db), db)
            self.jobs.kick()
            return response

        @self.app.delete("/sandboxes/{sandbox_id}", response_model=DeleteSandboxResponse)
        def delete_sandbox(sandbox_id: str, user: Member = Depends(current_user)) -> DeleteSandboxResponse:
            with db_manager.session() as db:
                sandbox = self.owned(sandbox_id, user, db)
                active = db.get_active_job(sandbox_id=sandbox.id)
                if active is None or active.kind != "delete":
                    db.cancel_sandbox_jobs(sandbox_id=sandbox.id)
                    db.update_sandbox_status(status="deleting", id=sandbox.id)
                    self.jobs.enqueue(db, sandbox.id, "delete")
            self.jobs.kick()
            return DeleteSandboxResponse(id=sandbox.id)

        @self.app.post("/sandboxes/{sandbox_id}/move", response_model=SandboxResponse)
        def move_sandbox(
            sandbox_id: str, payload: MoveSandboxRequest, user: Member = Depends(current_user)
        ) -> SandboxResponse:
            with db_manager.session() as db:
                sandbox = self.idle(sandbox_id, user, db)
                if sandbox.status in ("running", "provisioning"):
                    raise HTTPException(status_code=409, detail="stop the sandbox before moving it")
                if sandbox.kind not in VMS:
                    if sandbox.server_id or not runtime.is_kube():
                        raise ZooError("not_supported", "Docker sandboxes run on one host and cannot be moved.")
                    if payload.server_id is not None or payload.node_name is None:
                        raise ZooError("bad_request", "Kubernetes moves require node_name, not a registered server.")
                    self.jobs.enqueue(db, sandbox.id, "move", target=payload.node_name)
                else:
                    if payload.node_name is not None:
                        raise ZooError("bad_request", "VM moves require server_id.")
                    if any(True for _ in db.list_snapshots_by_sandbox(sandbox_id=sandbox.id)):
                        raise HTTPException(
                            status_code=409,
                            detail="delete this sandbox's snapshots before moving it: they stay on its current server",
                        )
                    target = self.place(payload.server_id, user, db, sandbox.kind, size=sizes.of(sandbox))
                    if target != sandbox.server_id:
                        self.jobs.enqueue(db, sandbox.id, "move", target=target)
                response = to_response(sandbox, db)
            self.jobs.kick()
            return response

        @self.app.get("/tools")
        def list_tools(kind: str | None = None, user: Member = Depends(current_user)) -> list[dict]:
            allowed = KINDS.get(kind or "desktop")
            return [t.schema() for t in TOOLS.values() if allowed is None or t.category in allowed]

        @self.app.post("/sandboxes/{sandbox_id}/tools/{name}")
        async def call_tool(
            sandbox_id: str, name: str, args: dict[str, Any] | None = None, user: Member = Depends(current_user)
        ) -> Any:
            return await self.run_tool(user, sandbox_id, name, args or {}, "api")

        @self.app.post("/sandboxes/{sandbox_id}/screenshot")
        async def screenshot(
            sandbox_id: str,
            format: Literal["png", "webp", "jpeg"] = "png",
            scale: float = 1.0,
            quality: int = 80,
            user: Member = Depends(current_user),
        ):
            args = {"format": format, "scale": scale, "quality": quality}
            data = await self.run_tool(user, sandbox_id, "screenshot", args, "api")
            return Response(content=base64.b64decode(data), media_type=MEDIA_TYPES[format])

        @self.app.post("/sandboxes/{sandbox_id}/exec", response_model=ExecResponse)
        async def execute(sandbox_id: str, exec_req: ExecRequest, user: Member = Depends(current_user)) -> ExecResponse:
            result = await self.run_tool(user, sandbox_id, "execute_command", exec_req.model_dump(), "api")
            return ExecResponse(sandbox_id=sandbox_id, **result)

        @self.app.post("/sandboxes/{sandbox_id}/exec/stream")
        async def execute_streamed(
            sandbox_id: str, exec_req: ExecRequest, user: Member = Depends(current_user)
        ) -> StreamingResponse:
            """Runs a command and streams its output as server-sent events: `stdout` and `stderr` ({"data": text})
            as it arrives, then `exit` ({"exit_code", "timed_out"}). Linux sandboxes only."""
            frames = await self.stream_exec(user, sandbox_id, exec_req)
            return StreamingResponse(frames, media_type="text/event-stream")

        @self.app.get("/sandboxes/{sandbox_id}/files/content")
        async def download_file(
            sandbox_id: str,
            path: str = Query(min_length=1, description="absolute, or relative to the home directory"),
            user: Member = Depends(current_user),
        ) -> Response:
            """A file's bytes (Linux sandboxes)."""
            runtime_id = self.linux_files(sandbox_id, user, "read")
            try:
                data = await asyncio.to_thread(FileSystem.read_bytes, runtime_id, path)
            except FileNotFoundError as e:
                raise ZooError("not_found", str(e))
            return Response(content=data, media_type="application/octet-stream")

        @self.app.put("/sandboxes/{sandbox_id}/files/content")
        async def upload_file(
            sandbox_id: str,
            request: Request,
            path: str = Query(min_length=1, description="absolute, or relative to the home directory"),
            user: Member = Depends(current_user),
        ) -> dict:
            """Writes the request body to a file, making its folder (Linux sandboxes)."""
            runtime_id = self.linux_files(sandbox_id, user, "write")
            data = await request.body()
            if len(data) > MAX_UPLOAD_BYTES:
                raise ZooError("too_large", f"files are at most {MAX_UPLOAD_BYTES >> 20} MB")
            return await asyncio.to_thread(FileSystem.write_bytes, runtime_id, path, data)

        @self.app.get("/sandboxes/{sandbox_id}/guest")
        async def guest_status(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> dict:
            return await hub.status_async(self.owned(sandbox_id, user, db).id)

        @self.app.get("/sandboxes/{sandbox_id}/executions", response_model=pages.Page[ToolExecutionResponse])
        def list_executions(
            sandbox_id: str,
            page: pages.Params = Depends(),
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> pages.Page[ToolExecutionResponse]:
            sandbox = self.owned(sandbox_id, user, db)
            self.executions.flush_sync()
            rows, after = pages.collect(
                lambda at, id, limit: db.page_tool_executions(
                    sandbox_id=sandbox.id, before_at=at, before_id=id, row_limit=limit
                ),
                lambda e: True,
                lambda e: (e.created_at, e.id),
                page,
            )
            return pages.Page(
                data=[
                    ToolExecutionResponse(
                        **{
                            k: getattr(e, k)
                            for k in ToolExecutionResponse.model_fields
                            if k not in ("created_at", "completed_at")
                        },
                        created_at=to_stamp(e.created_at),
                        completed_at=to_stamp(e.completed_at),
                    )
                    for e in rows
                ],
                next_cursor=after,
            )

        @self.app.get("/sandboxes/{sandbox_id}/backup")
        def backup(sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)):
            sandbox = self.running(sandbox_id, user, db)
            return StreamingResponse(
                export_home(sandbox.runtime_id),
                media_type="application/x-tar",
                headers={"Content-Disposition": f'attachment; filename="{sandbox.name}.tar"'},
            )

        @self.app.post("/sandboxes/{sandbox_id}/restore", response_model=SandboxResponse)
        async def restore(sandbox_id: str, request: Request, user: Member = Depends(current_user)) -> SandboxResponse:
            with db_manager.session() as db:
                sandbox = self.running(sandbox_id, user, db)
            await asyncio.to_thread(import_home, sandbox.runtime_id, await request.body())
            return to_response(sandbox)

        @self.app.get("/sandboxes/{sandbox_id}/snapshots", response_model=list[SnapshotResponse])
        def list_snapshots(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[SnapshotResponse]:
            sandbox = self.owned(sandbox_id, user, db)
            return [snapshot_response(s) for s in db.list_snapshots_by_sandbox(sandbox_id=sandbox.id)]

        @self.app.post("/sandboxes/{sandbox_id}/snapshots", response_model=SnapshotResponse, status_code=202)
        def create_snapshot(
            sandbox_id: str, payload: SnapshotRequest | None = None, user: Member = Depends(current_user)
        ) -> SnapshotResponse:
            """Snapshots the home disk on the sandbox's host. Linux sandboxes can be running; macOS and Windows VMs
            must be stopped, since their whole disk is copied."""
            with db_manager.session() as db:
                sandbox = self.idle(sandbox_id, user, db)
                if sandbox.kind in VMS and sandbox.status != "stopped":
                    raise HTTPException(status_code=409, detail="stop the sandbox before snapshotting its disk")
                if sandbox.status not in ("running", "stopped"):
                    raise HTTPException(status_code=409, detail=f"sandbox is {sandbox.status}")
                existing = list(db.list_snapshots_by_sandbox(sandbox_id=sandbox.id))
                if len(existing) >= MAX_SNAPSHOTS:
                    raise HTTPException(
                        status_code=409, detail=f"a sandbox keeps at most {MAX_SNAPSHOTS} snapshots; delete one first"
                    )
                name = (payload.name.strip() if payload else "") or f"Snapshot {len(existing) + 1}"
                row = db.create_snapshot(
                    CreateSnapshotParams(
                        id=str(uuid.uuid4()),
                        sandbox_id=sandbox.id,
                        server_id=sandbox.server_id,
                        name=name,
                        created_by=user.id,
                    )
                )
                if row is None:
                    raise RuntimeError("the snapshot wasn't saved")
                self.jobs.enqueue(db, sandbox.id, "snapshot", snapshot_id=row.id)
            self.jobs.kick()
            with db_manager.session() as db:
                return snapshot_response(db.get_snapshot(id=row.id) or row)

        @self.app.post("/sandboxes/{sandbox_id}/snapshots/{snapshot_id}/restore", response_model=SandboxResponse)
        def restore_from_snapshot(
            sandbox_id: str, snapshot_id: str, user: Member = Depends(current_user)
        ) -> SandboxResponse:
            with db_manager.session() as db:
                sandbox = self.idle(sandbox_id, user, db)
                found = self.snapshot_of(sandbox, snapshot_id, db)
                if found.state != "ready":
                    raise HTTPException(status_code=409, detail=f"the snapshot is {found.state}")
                if sandbox.status != "stopped":
                    raise HTTPException(status_code=409, detail="stop the sandbox before restoring a snapshot")
                self.jobs.enqueue(db, sandbox.id, "restore", snapshot_id=found.id)
                response = to_response(sandbox, db)
            self.jobs.kick()
            return response

        @self.app.delete("/sandboxes/{sandbox_id}/snapshots/{snapshot_id}", status_code=204)
        async def delete_snapshot(sandbox_id: str, snapshot_id: str, user: Member = Depends(current_user)):
            with db_manager.session() as db:
                sandbox = self.owned(sandbox_id, user, db)
                found = self.snapshot_of(sandbox, snapshot_id, db)
                active = db.get_active_job(sandbox_id=sandbox.id)
                if active is not None and active.kind in ("snapshot", "restore"):
                    raise HTTPException(status_code=409, detail=f"sandbox is busy: {active.kind} in progress")
                server = self.server_of(sandbox, db)
            try:
                await asyncio.to_thread(remove_snapshot, sandbox, found.id, server)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"couldn't remove the snapshot from its host: {e}"[:500])
            with db_manager.session() as db:
                db.delete_snapshot(id=found.id)

        @self.app.post("/sandboxes/{sandbox_id}/vnc-ticket", response_model=VncTicketResponse)
        def vnc_ticket(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> VncTicketResponse:
            sandbox = self.running(sandbox_id, user, db)
            if not has_screen(sandbox.kind):
                raise HTTPException(status_code=400, detail="this sandbox has no screen")
            return VncTicketResponse(ticket=tickets.issue(user.id, f"sandbox:{sandbox.id}"))

        self.app.websocket("/sandboxes/{sandbox_id}/ws")(self.proxy)

        @self.app.post("/sandboxes/{sandbox_id}/terminal-ticket", response_model=VncTicketResponse)
        async def terminal_ticket(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> VncTicketResponse:
            sandbox = self.allowed(sandbox_id, user, db, "shell", "exec")
            guest = await hub.for_sandbox_async(sandbox.id)
            if guest is None or not guest.has("pty"):
                raise HTTPException(
                    status_code=409, detail="the terminal needs the sandbox's guest agent; restart the sandbox"
                )
            return VncTicketResponse(ticket=tickets.issue(user.id, f"terminal:{sandbox.id}"))

        self.app.websocket("/sandboxes/{sandbox_id}/terminal")(self.terminal)

    def owned(self, sandbox_id: str, user: Member, db: Querier) -> Sandbox:
        """The sandbox, when it is in the user's workspace (and their API key's sandbox, or has its key's labels)."""
        sandbox = db.get_sandbox(id=sandbox_id)
        if sandbox is None or sandbox.workspace_id != user.workspace_id or sandbox.status == "deleted":
            raise HTTPException(status_code=404, detail="sandbox not found")
        if user.key_labels is not None and not labels.matches(labels.of(db, sandbox.id), user.key_labels):
            raise HTTPException(status_code=404, detail="sandbox not found")
        require_sandbox(user, sandbox.id)
        return sandbox

    def member_of(self, sandbox: Sandbox, user_id: str, db: Querier) -> bool:
        """Whether the user may use the sandbox: an active member, not a viewer, of its workspace."""
        member = db.get_workspace_member(workspace_id=sandbox.workspace_id, user_id=user_id)
        return member is not None and member.status == "active" and member.role != "viewer"

    async def state_async(self, sandbox_id: str) -> dict | None:
        async def load():
            async with async_db.session() as db:
                found = await db.get_sandbox(id=sandbox_id)
                if found is None:
                    return None
                sandbox = Sandbox.model_validate(found.model_dump())
                sandbox_labels = {row.key: row.value async for row in db.list_sandbox_labels(sandbox_id=sandbox_id)}
                permissions = {
                    (row.permission, row.action): row.effect
                    async for row in db.list_sandbox_permissions(sandbox_id=sandbox_id)
                }
                policy = await db.get_sandbox_network_policy(sandbox_id=sandbox_id)
                rules = [row async for row in db.list_sandbox_network_rules(policy_id=policy.id)] if policy else []
                job = await db.get_active_job(sandbox_id=sandbox_id)
                encrypted = [row.ciphertext async for row in db.list_sandbox_vault_secrets(sandbox_id=sandbox_id)]
                async for row in db.list_sandbox_secrets(sandbox_id=sandbox_id):
                    if row.enabled:
                        secret = await db.get_sandbox_secret(id=row.id)
                        if secret is not None:
                            encrypted.append(secret.secret_ref)
            password = json.loads(sandbox.config or "{}").get(VNC_PASSWORD)
            if password:
                encrypted.append(password)
            attached = await asyncio.gather(*(decrypt_async(value) for value in encrypted))
            return {
                "workspace_id": sandbox.workspace_id,
                "sandbox": sandbox,
                "labels": sandbox_labels,
                "permissions": permissions,
                "policy": policy,
                "rules": rules,
                "job": job,
                "secrets": [*_booted.get(sandbox_id, []), *attached],
            }

        return await cache.get("sandbox", sandbox_id, load)

    async def owned_async(self, sandbox_id: str, user: Member) -> tuple[Sandbox, dict]:
        state = await self.state_async(sandbox_id)
        if state is None or state["workspace_id"] != user.workspace_id or state["sandbox"].status == "deleted":
            raise HTTPException(status_code=404, detail="sandbox not found")
        require_sandbox(user, sandbox_id)
        if not labels.matches(state["labels"], user.key_labels):
            raise HTTPException(status_code=404, detail="sandbox not found")
        return state["sandbox"], state

    async def response_async(self, sandbox_id: str, user: Member) -> SandboxResponse:
        sandbox, state = await self.owned_async(sandbox_id, user)
        response = to_response(sandbox, sandbox_labels=state["labels"])
        job = state["job"]
        if job is not None:
            response.job = JobResponse(
                **{key: getattr(job, key) for key in JobResponse.model_fields if key not in {"waiting", "deadline"}},
                deadline=to_stamp(job.deadline),
                waiting=(job.last_error or "").startswith(WAITING),
            )
        return response

    async def allowed_async(self, sandbox_id: str, user: Member, permission: str, action: str) -> tuple[Sandbox, dict]:
        sandbox, state = await self.owned_async(sandbox_id, user)
        if sandbox.status != "running" or sandbox.runtime_id is None:
            raise HTTPException(status_code=409, detail=f"sandbox is {sandbox.status}")
        if state["permissions"].get((permission, action), "allow") != "allow":
            raise HTTPException(status_code=403, detail=f"{permission}.{action} is denied for this sandbox")
        return sandbox, state

    def allowed(self, sandbox_id: str, user: Member, db: Querier, permission: str, action: str) -> Sandbox:
        sandbox = self.running(sandbox_id, user, db)
        stored = db.get_sandbox_permission(sandbox_id=sandbox.id, permission=permission, action=action)
        if stored is not None and stored.effect != "allow":
            raise HTTPException(status_code=403, detail=f"{permission}.{action} is denied for this sandbox")
        return sandbox

    def idle(self, sandbox_id: str, user: Member, db: Querier) -> Sandbox:
        """An owned sandbox with no boot, stop, delete or move in progress."""
        sandbox = self.owned(sandbox_id, user, db)
        active = db.get_active_job(sandbox_id=sandbox.id)
        if active is not None:
            raise HTTPException(status_code=409, detail=f"sandbox is busy: {active.kind} in progress")
        return sandbox

    def running(self, sandbox_id: str, user: Member, db: Querier) -> Sandbox:
        sandbox = self.owned(sandbox_id, user, db)
        if sandbox.status != "running" or not sandbox.runtime_id:
            raise HTTPException(status_code=409, detail=f"sandbox is {sandbox.status}")
        return sandbox

    def server_of(self, sandbox: Sandbox, db: Querier):
        return db.get_server(id=sandbox.server_id) if sandbox.server_id else None

    def place(
        self,
        server_id: str | None,
        user: Member,
        db: Querier,
        kind: str = "desktop",
        queue: bool = False,
        size: sizes.Size = sizes.DEFAULT,
    ) -> str | None:
        platform = os_of(kind)
        servers = [
            s for s in db.list_servers_by_workspace(workspace_id=user.workspace_id) if platform in parse(s.capabilities)
        ]
        if platform in VMS:
            return self.place_vm(platform, server_id, servers, db, queue)
        from apps.server import remote_docker

        if server_id not in (None, AUTO):
            if server_id not in {s.id for s in servers}:
                raise HTTPException(status_code=404, detail="Linux-capable server not found")
            if not remote_docker.available(server_id):
                raise ZooError(
                    "unavailable",
                    "the server's Linux Docker runtime is not ready",
                    "Start Docker in Linux-container mode and reconnect zoo-node.",
                )
            return server_id
        if server_id == AUTO:
            ready = [s for s in servers if remote_docker.available(s.id)]
            if ready:
                load = {s.id: 0 for s in ready}
                for sandbox in db.list_all_sandboxes():
                    if sandbox.server_id in load and sandbox.status in ("running", "provisioning"):
                        load[sandbox.server_id] += 1
                return min(load, key=lambda server_id: load[server_id])
        return None

    def place_vm(self, platform: str, server_id: str | None, servers: list, db: Querier, queue: bool = False) -> str:
        name, limit = PLATFORM_NAMES[platform], VMS[platform].MAX_VMS
        if not servers:
            raise HTTPException(
                status_code=400, detail=f"add a {PLATFORMS[platform].host} under Servers to run {name} sandboxes"
            )
        if server_id is not None and server_id != AUTO:
            if server_id not in {s.id for s in servers}:
                raise HTTPException(status_code=404, detail=f"{name} server not found")
            return server_id
        load = {s.id: 0 for s in servers}
        for sb in db.list_all_sandboxes():
            if sb.server_id in load and sb.kind == platform and sb.status in ("running", "provisioning"):
                load[sb.server_id] += 1
        best = min(load, key=load.get)
        if load[best] >= limit:
            if platform == "macos" and queue:
                # it boots when a VM on that Mac stops (macos.start waits in the job queue)
                return best
            if platform == "macos":
                raise HTTPException(
                    status_code=409,
                    detail=f"Mac full: every Mac already runs {limit} macOS VMs, the most Apple allows per Mac. "
                    "Stop one, add a Mac, or create with queue to wait for room",
                )
            raise HTTPException(status_code=409, detail=f"every {name} server already runs {limit} VMs")
        return best

    def _default_image_version(self, user: Member, db: Querier) -> tuple[str, SandboxImageVersion]:
        workspace_id = user.workspace_id
        image = next((i for i in db.list_sandbox_images(workspace_id=workspace_id) if i.slug == IMAGE_SLUG), None)
        if image is None:
            image = db.create_sandbox_image(
                CreateSandboxImageParams(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    name="Zoo desktop",
                    slug=IMAGE_SLUG,
                    description=None,
                    is_public=False,
                    created_by=user.id,
                )
            )

        version = db.get_sandbox_image_version_by_tag(image_id=image.id, version=IMAGE_VERSION)
        if version is None:
            version = db.create_sandbox_image_version(
                CreateSandboxImageVersionParams(
                    id=str(uuid.uuid4()),
                    image_id=image.id,
                    version=IMAGE_VERSION,
                    image_uri=IMAGE,
                    image_digest=None,
                    build_config="{}",
                    default_resources="{}",
                    created_by=user.id,
                )
            )
        return workspace_id, version

    def create(self, payload: CreateSandboxRequest, user: Member, db: Querier) -> Sandbox:
        require(user, "member")
        if user.key_sandbox_id is not None:
            raise HTTPException(status_code=403, detail="this API key only reaches its own sandbox")
        workspace_id, version = self._default_image_version(user, db)
        if payload.admin and payload.kind != "macos":
            raise HTTPException(status_code=400, detail="admin sandboxes are for macOS")
        if payload.kind in VMS and payload.size != sizes.DEFAULT:
            raise HTTPException(status_code=400, detail="sizing is supported on Linux sandboxes only")
        sandbox_labels = labels.scoped(payload.labels, user.key_labels)
        quotas.check(db, user.workspace_id, payload.size, new=True, labels=sandbox_labels)
        server_id = self.place(payload.server_id, user, db, payload.kind, payload.queue, payload.size)
        for profile_id in payload.profile_ids:
            profile = db.get_profile(id=profile_id)
            if profile is None or profile.workspace_id != user.workspace_id:
                raise HTTPException(status_code=404, detail="profile not found")
            if profile.platform != platform_of(payload.kind):
                raise HTTPException(
                    status_code=400, detail=f"profile {profile.name} is from a {profile.platform} sandbox"
                )
        for secret_id in payload.secret_ids:
            secret = db.get_vault_secret(id=secret_id)
            if secret is None or secret.workspace_id != user.workspace_id:
                raise HTTPException(status_code=404, detail="secret not found")
        # a warm one takes the pooled container's id, which its name, volume and guest token already carry
        # pooled containers run at the default size
        pooled = self.pool.claim(db, payload.kind, server_id) if payload.size == sizes.DEFAULT else None
        sandbox_id = pooled.id if pooled else str(uuid.uuid4())
        config: dict[str, Any] = {"profiles": payload.profile_ids}
        if server_id and payload.kind not in VMS:
            config["remote_server_id"] = server_id
        if payload.kind not in VMS and pooled is None:
            config["home_claim"] = f"zoo-home-{sandbox_id}"
        if payload.admin:
            config["admin"] = True
        if pooled is not None:
            config = {**json.loads(pooled.config), **config, "image": pooled.image, "pooled": True}
        sandbox = db.create_sandbox(
            CreateSandboxParams(
                id=sandbox_id,
                workspace_id=workspace_id,
                image_version_id=version.id,
                created_by=user.id,
                name=payload.name or f"sandbox-{sandbox_id[:8]}",
                runtime=payload.kind if payload.kind in VMS else runtime.NAME,
                resources="{}",
                config=json.dumps(config),
            )
        )
        if sandbox is None:
            raise HTTPException(status_code=500, detail="failed to create sandbox")
        if pooled is not None:
            db.update_sandbox_runtime(
                runtime_id=pooled.runtime_id,
                runtime_host=pooled.runtime_host,
                id=sandbox.id,
            )
        db.set_sandbox_placement(server_id=server_id, kind=payload.kind, id=sandbox.id)
        size = payload.size
        db.set_sandbox_size(cpus=size.cpus, memory_mb=size.memory_mb, disk_gb=size.disk_gb, id=sandbox.id)
        db.set_sandbox_lifecycle(
            idle_timeout_minutes=payload.idle_timeout_minutes,
            max_lifetime_minutes=payload.max_lifetime_minutes,
            id=sandbox.id,
        )
        labels.put(db, sandbox.id, sandbox_labels)
        for secret_id in dict.fromkeys(payload.secret_ids):
            db.attach_vault_secret(sandbox_id=sandbox.id, secret_id=secret_id)
            name = db.get_vault_secret(id=secret_id).name
            audit(db, user, "secret.attach", "secret", secret_id, sandbox.id, name=name, sandbox=sandbox.name)
        sandbox = db.update_sandbox_status(status="provisioning", id=sandbox.id)
        if sandbox is None:
            raise HTTPException(status_code=500, detail="failed to create sandbox")
        # a warm one is handed over in the request (create_async); the caller kicks the queue once this commits
        if pooled is None:
            self.jobs.enqueue(db, sandbox.id, "boot", boot_deadline(sandbox.kind))
        self.logger.info("sandbox created", extra={"sandbox_id": sandbox.id, "user_id": user.id})
        return sandbox

    async def create_async(self, payload: CreateSandboxRequest, user: Member) -> SandboxResponse:
        """Creates a sandbox. A warm one is claimed and handed over here, running when this returns; a failed
        hand-off discards that pooled sandbox and claims another, until WARM_CEILING. With none pooled, the boot job
        starts one cold."""
        began = time.monotonic()
        deadline = began + WARM_CEILING
        while True:

            def created() -> tuple[Sandbox, SandboxResponse]:
                with db_manager.session() as db:
                    found = self.create(payload, user, db)
                    return found, to_response(found, db)

            sandbox, response = await asyncio.to_thread(created)
            if not json.loads(sandbox.config or "{}").get("pooled"):
                self.jobs.kick()
                return response
            warm = {"kind": sandbox.kind, "warm": "true"}
            try:
                async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                    await self.hand_off(sandbox)
                metrics.sandbox_ready.record(time.monotonic() - began, warm)
                metrics.sandbox_creates.add(1, {**warm, "outcome": "running"})
                return await asyncio.to_thread(self.response_of, sandbox.id)
            except Exception as e:
                self.logger.warning("warm hand-off failed", extra={"sandbox_id": sandbox.id, "error": repr(e)})
                await asyncio.to_thread(self.discard, sandbox)
                if time.monotonic() >= deadline:
                    metrics.sandbox_creates.add(1, {**warm, "outcome": "failed"})
                    raise ZooError("create_failed", "no warm sandbox could be handed over in time") from e

    async def hand_off(self, sandbox: Sandbox):
        """Gives a claimed pooled sandbox to its owner: secrets, network and app policy and profiles, in parallel."""
        runtime_id = sandbox.runtime_id
        if not runtime_id or not await asyncio.to_thread(runtime.is_running, runtime_id):
            raise RuntimeError("the pooled container is gone")
        config = json.loads(sandbox.config or "{}")

        def state():
            with db_manager.session() as db:
                secrets = secret_env(sandbox, db)
                profiles = [db.get_profile(id=p) for p in config.get("profiles", [])]
            stored = config.get(VNC_PASSWORD)
            remember_secrets(sandbox.id, [*secrets.values(), decrypt(stored)] if stored else [*secrets.values()])
            return secrets, profiles

        def policy():
            with db_manager.session() as db:
                enforce(sandbox.model_copy(update={"status": "running"}), db)

        secrets, profiles = await asyncio.to_thread(state)
        if runtime.is_kube() and not sandbox.server_id:
            await asyncio.to_thread(runtime.resume, runtime_id)
        steps = [asyncio.to_thread(policy)]
        if secrets:
            steps.append(asyncio.to_thread(write_secrets, runtime_id, secrets))
        steps += [asyncio.to_thread(self.apply_profile, runtime_id, p) for p in profiles if p is not None]
        await asyncio.gather(*steps)

        def started():
            with db_manager.session() as db:
                found = db.set_sandbox_started(id=sandbox.id)
                db.set_sandbox_reachable(id=sandbox.id)
            return found

        found = await asyncio.to_thread(started)
        self.logger.info("sandbox running", extra={"sandbox_id": sandbox.id, "warm": True})
        if runtime.is_kube() and not sandbox.server_id:
            try:
                await asyncio.to_thread(self.bind, sandbox.id)
            except Exception as e:
                self.logger.error("vm-control binding failed", extra={"sandbox_id": sandbox.id, "error": str(e)})
        if found is not None and found.kind == "browser":
            try:
                await asyncio.to_thread(start_browser, runtime_id, BROWSER_HOME)
            except Exception as e:
                self.logger.error("browser launch failed", extra={"sandbox_id": sandbox.id, "error": str(e)})

    def discard(self, sandbox: Sandbox):
        """Drops a sandbox whose warm hand-off failed: its owner never had it."""
        if sandbox.runtime_id:
            try:
                remove_container(sandbox.runtime_id)
            except Exception as e:
                self.logger.error(
                    "discarding a pooled sandbox failed", extra={"sandbox_id": sandbox.id, "error": str(e)}
                )
        forget_secrets(sandbox.id)
        with db_manager.session() as db:
            db.delete_sandbox(id=sandbox.id)

    def response_of(self, sandbox_id: str) -> SandboxResponse:
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=sandbox_id)
            if sandbox is None:
                raise ZooError("sandbox_not_found", "sandbox not found")
            return to_response(sandbox, db)

    def halt(self, sandbox: Sandbox, db: Querier) -> Sandbox:
        if sandbox.runtime_id:
            remove_container(sandbox.runtime_id)
        db.clear_sandbox_runtime(id=sandbox.id)
        db.set_sandbox_reachable(id=sandbox.id)
        return db.set_sandbox_stopped(id=sandbox.id)

    # Jobs. Each one may run again after a failure or a restart, so each step is safe to repeat.

    def boot(self, job: Job):
        sandbox_id = job.sandbox_id
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=sandbox_id)
            if sandbox is None or sandbox.status != "provisioning":
                return
        if sandbox.paused_at is not None and sandbox.runtime_id and json.loads(job.args).get("upgrade"):
            # an upgrade boots the new image: a paused one would resume on its old one
            self.to_volume(sandbox)
            sandbox = sandbox.model_copy(update={"runtime_id": None, "paused_at": None})
        if sandbox.paused_at is not None and sandbox.runtime_id:
            # still frozen in place: it resumes where it stopped, with the secrets changed meanwhile
            self.thaw(sandbox)
            with db_manager.session() as db:
                secrets = secret_env(sandbox, db)
                db.clear_sandbox_paused(id=sandbox.id)
            write_secrets(sandbox.runtime_id, secrets)
            remember_secrets(sandbox.id, [*_booted.get(sandbox.id, []), *secrets.values()])
            self.mark_started(job)
            return
        with db_manager.session() as db:
            version = db.get_sandbox_image_version(id=sandbox.image_version_id)
            if version is None:
                raise RuntimeError("the sandbox's image version is missing")
            default = version.version == IMAGE_VERSION and db.get_sandbox_image(id=version.image_id).slug == IMAGE_SLUG
            secrets = secret_env(sandbox, db)
            # VMs source their secrets from ~/.zoo/env; containers read them from env.json, written once up
            env = secrets if sandbox.kind in VMS else {}
            server = self.server_of(sandbox, db)
            desktop = sandbox.kind != "code"
            if desktop and sandbox.kind not in VMS:
                env = {**env, "ZOO_VNC_PASSWORD": ensure_vnc_password(sandbox, db)}
            if sandbox.kind not in VMS:
                env = {**env, **guest_env(sandbox.id, remote=server is not None)}
                if sandbox.kind == "browser":
                    env["ZOO_BROWSER_HOME"] = BROWSER_HOME
                    env["ZOO_BROWSER_WAIT"] = "1"
            if sandbox.kind in VMS:
                image = ""
            elif default or not desktop:
                image = self.pin_image(sandbox.id, db)
            else:
                image = version.image_uri
            profiles = [db.get_profile(id=p) for p in json.loads(sandbox.config or "{}").get("profiles", [])]
            stored = json.loads(sandbox.config or "{}").get(VNC_PASSWORD)
            known = [*{**secrets, **env}.values()]
            remember_secrets(sandbox_id, [*known, decrypt(stored)] if stored else known)
        if sandbox.kind in VMS:
            admin = bool(json.loads(sandbox.config or "{}").get("admin"))
            return self.boot_vm(job, sandbox.kind, server, env, profiles, admin)
        # the container is named after the sandbox, so a retry adopts the one an earlier attempt started
        container_id, host = run_container(
            f"zoo-sandbox-{sandbox_id}", image, sandbox_id, env, server, desktop, sizes.of(sandbox)
        )
        if container_id is not None:
            hub.bind(container_id, sandbox_id)
        if not self.record_runtime(job, container_id, host):
            return
        if secrets:
            write_secrets(container_id, secrets)
        for profile in profiles:
            if profile is not None:
                self.apply_profile(container_id, profile)
        if desktop and not hub.wait_for_desktop(sandbox_id):
            raise RuntimeError("desktop did not come up in time")
        sandbox = self.mark_started(job)
        if sandbox is not None and sandbox.kind == "browser":
            try:
                start_browser(container_id, BROWSER_HOME)
            except Exception as e:
                self.logger.error("browser launch failed", extra={"sandbox_id": sandbox_id, "error": str(e)})

    def pin_image(self, sandbox_id: str, db: Querier) -> str:
        """The image the sandbox boots from: the default image of its first boot, so upgrading Zoo doesn't swap
        the image under an existing sandbox. POST /sandboxes/{id}/upgrade moves it to the current one."""
        # read again: ensure_vnc_password may have just rewritten the config
        sandbox = db.get_sandbox(id=sandbox_id)
        if sandbox is None:
            raise RuntimeError(f"sandbox {sandbox_id} is gone")
        config = json.loads(sandbox.config or "{}")
        if "image" not in config:
            config["image"] = default_image(sandbox.kind)
            db.update_sandbox(name=sandbox.name, resources=sandbox.resources, config=json.dumps(config), id=sandbox.id)
        return config["image"]

    def boot_vm(self, job: Job, kind: str, server, env: dict[str, str], profiles: list, admin: bool):
        name = PLATFORM_NAMES[kind]
        if server is None:
            raise RuntimeError(f"{name} sandboxes need a {name} server")
        options = {"admin": admin} if kind == "macos" else {}
        started = time.monotonic()
        runtime_id = VMS[kind].start(job.sandbox_id, server, env, **options)
        seconds = round(time.monotonic() - started, 1)
        if self.record_runtime(job, runtime_id, server.bind_address):
            with db_manager.session() as db:
                db.set_sandbox_boot(base_version=VMS[kind].base_of(runtime_id), boot_seconds=seconds, id=job.sandbox_id)
            self.logger.info(f"{name} VM booted", extra={"sandbox_id": job.sandbox_id, "boot_seconds": seconds})
            for profile in profiles:
                if profile is not None:
                    self.apply_profile(runtime_id, profile)
            self.mark_started(job)

    def still_booting(self, job: Job, db: Querier) -> Sandbox | None:
        """The sandbox, if this boot still owns it: not stopped, deleted or timed out meanwhile."""
        sandbox = db.get_sandbox(id=job.sandbox_id)
        if sandbox is None or sandbox.status != "provisioning" or not self.jobs.owns(job):
            return None
        return sandbox

    def record_runtime(self, job: Job, runtime_id: str, host: str | None) -> bool:
        with db_manager.session() as db:
            sandbox = self.still_booting(job, db)
            if sandbox is not None:
                db.update_sandbox_runtime(runtime_id=runtime_id, runtime_host=host, id=sandbox.id)
                return True
        remove_container(runtime_id)
        return False

    def mark_started(self, job: Job) -> Sandbox | None:
        with db_manager.session() as db:
            booting = self.still_booting(job, db)
            if booting is None:
                return None
            sandbox = db.set_sandbox_started(id=job.sandbox_id)
            if sandbox is None:
                return None
            db.set_sandbox_reachable(id=sandbox.id)
            # a sandbox's first start is its create's; later ones are restarts
            if booting.started_at is None:
                cold = {"kind": sandbox.kind, "warm": "false"}
                metrics.sandbox_ready.record((datetime.now(UTC) - sandbox.created_at).total_seconds(), cold)
                metrics.sandbox_creates.add(1, {**cold, "outcome": "running"})
            try:
                enforce(sandbox, db)
            except Exception as e:
                self.logger.error("policy enforcement failed", extra={"sandbox_id": sandbox.id, "error": str(e)})
        self.logger.info("sandbox running", extra={"sandbox_id": sandbox.id, "server_id": sandbox.server_id})
        if runtime.is_kube() and sandbox.kind not in VMS and not sandbox.server_id:
            try:
                self.bind(sandbox.id)
            except Exception as e:
                self.logger.error("vm-control binding failed", extra={"sandbox_id": sandbox.id, "error": str(e)})
        return sandbox

    def boot_failed(self, job: Job, error: str):
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=job.sandbox_id)
        if sandbox is None or sandbox.status != "provisioning":
            return
        if sandbox.runtime_id:
            try:
                remove_container(sandbox.runtime_id)
            except Exception as e:
                self.logger.error("cleanup after failed boot failed", extra={"sandbox_id": sandbox.id, "error": str(e)})
        with db_manager.session() as db:
            db.clear_sandbox_runtime(id=sandbox.id)
            db.set_sandbox_failed(error_message=error, id=sandbox.id)
        if sandbox.started_at is None:
            metrics.sandbox_creates.add(1, {"kind": sandbox.kind, "warm": "false", "outcome": "failed"})

    def lifecycle_failed(self, kind: str):
        def failed(job: Job, error: str):
            with db_manager.session() as db:
                sandbox = db.get_sandbox(id=job.sandbox_id)
                if sandbox is not None and sandbox.status != "deleted":
                    db.set_sandbox_failed(error_message=f"{kind} failed: {error}", id=sandbox.id)

        return failed

    def stop(self, job: Job):
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=job.sandbox_id)
        if sandbox is None or sandbox.status == "deleted":
            return
        restart = json.loads(job.args).get("restart")
        # a Linux sandbox is frozen in place first; the pause sweeper deletes it down to its volume later
        if sandbox.runtime_id and sandbox.status == "running" and sandbox.kind not in VMS and not restart:
            self.freeze(sandbox)
            with db_manager.session() as db:
                db.set_sandbox_reachable(id=sandbox.id)
                db.set_sandbox_paused(id=sandbox.id)
            return
        if sandbox.runtime_id:
            remove_container(sandbox.runtime_id)
        forget_secrets(sandbox.id)
        with db_manager.session() as db:
            db.clear_sandbox_runtime(id=sandbox.id)
            db.set_sandbox_reachable(id=sandbox.id)
            db.set_sandbox_stopped(id=sandbox.id)
            if restart:
                db.update_sandbox_status(status="provisioning", id=sandbox.id)
                self.jobs.enqueue(db, sandbox.id, "boot", boot_deadline(sandbox.kind))

    def delete(self, job: Job):
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=job.sandbox_id)
            if sandbox is None or sandbox.status == "deleted":
                return
            server = self.server_of(sandbox, db)
        if sandbox.runtime_id:
            remove_container(sandbox.runtime_id)
        with db_manager.session() as db:
            snapshots = list(db.list_snapshots_by_sandbox(sandbox_id=sandbox.id))
        for found in snapshots:
            remove_snapshot(sandbox, found.id, server)
            with db_manager.session() as db:
                db.delete_snapshot(id=found.id)
        remove_volume(sandbox, server)
        forget_secrets(sandbox.id)
        with db_manager.session() as db:
            db.soft_delete_sandbox(id=sandbox.id)
        self.logger.info("sandbox deleted", extra={"sandbox_id": sandbox.id})

    def snapshot_of(self, sandbox: Sandbox, snapshot_id: str, db: Querier):
        found = db.get_snapshot(id=snapshot_id)
        if found is None or found.sandbox_id != sandbox.id:
            raise HTTPException(status_code=404, detail="snapshot not found")
        return found

    def take_snapshot(self, job: Job):
        snapshot_id = json.loads(job.args)["snapshot_id"]
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=job.sandbox_id)
            if sandbox is None or sandbox.status == "deleted" or db.get_snapshot(id=snapshot_id) is None:
                return
            server = self.server_of(sandbox, db)
        size = snapshot(sandbox, snapshot_id, server)
        with db_manager.session() as db:
            db.set_snapshot_ready(size_bytes=size, id=snapshot_id)

    def snapshot_failed(self, job: Job, error: str):
        with db_manager.session() as db:
            db.set_snapshot_failed(error=error[:500], id=json.loads(job.args)["snapshot_id"])

    def restore_job(self, job: Job):
        snapshot_id = json.loads(job.args)["snapshot_id"]
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=job.sandbox_id)
            if sandbox is None or sandbox.status != "stopped" or db.get_snapshot(id=snapshot_id) is None:
                return
            server = self.server_of(sandbox, db)
        if sandbox.paused_at is not None:
            self.to_volume(sandbox)
        restore_snapshot(sandbox, snapshot_id, server)

    def move(self, job: Job):
        target = json.loads(job.args)["target"]
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=job.sandbox_id)
            if sandbox is None or sandbox.status in ("running", "provisioning", "deleting", "deleted"):
                return
            source = self.server_of(sandbox, db)
            dest = db.get_server(id=target) if target else None
        if sandbox.kind not in VMS:
            if sandbox.paused_at is not None:
                self.to_volume(sandbox)
            runtime.move_volume(sandbox.id, target)
            return
        runtime.move(sandbox, source, dest)
        with db_manager.session() as db:
            db.set_sandbox_placement(server_id=target, kind=sandbox.kind, id=sandbox.id)

    def apply_profile(self, container_id: str, profile, version=None):
        """Loads a profile into the sandbox: the given version, or the latest."""
        if version is None:
            with db_manager.session() as db:
                version = db.get_latest_profile_version(profile_id=profile.id)
            if version is None:
                raise RuntimeError(f"profile {profile.name} has no saved version")
        parent = os.path.dirname(profile_path(profile.platform, profile.app))
        data = objects.get(profile_key(version.id))
        if version.encrypted:
            data = decrypt_bytes(data)
        import_dir(container_id, parent, data)

    def alive(self, sandbox: Sandbox) -> bool | None:
        """Whether the sandbox's runtime is running, or None when its host couldn't be asked."""
        if not sandbox.runtime_id:
            return False
        try:
            return is_running(sandbox.runtime_id)
        except Exception as e:
            self.logger.warning("sandbox host unreachable", extra={"sandbox_id": sandbox.id, "error": str(e)})
            return None

    def reconcile(self, startup: bool = False, server_id: str | None = None):
        with db_manager.session() as db:
            if startup:
                for server in db.list_all_servers():
                    try:
                        connect(server)
                    except Exception as e:
                        self.logger.error("server unreachable", extra={"server_id": server.id, "error": str(e)})
            sandboxes = [s for s in list(db.list_all_sandboxes()) if db.get_active_job(sandbox_id=s.id) is None]
            if startup:
                # sandboxes left mid-boot or mid-delete by a version without the job queue
                for sandbox in sandboxes:
                    if sandbox.status == "provisioning":
                        self.jobs.enqueue(db, sandbox.id, "boot", boot_deadline(sandbox.kind))
                    elif sandbox.status == "deleting":
                        self.jobs.enqueue(db, sandbox.id, "delete")
        if startup:
            self.jobs.kick()
        for sandbox in sandboxes:
            if sandbox.kind not in VMS and not sandbox.server_id:
                continue
            if server_id is not None and sandbox.server_id != server_id:
                continue
            if sandbox.status != "running":
                continue
            alive = self.alive(sandbox)
            # outside the session: the screen check can take a while
            hung = sandbox.kind in VMS and alive is True and self.hung(sandbox)
            with db_manager.session() as db:
                current = db.get_sandbox(id=sandbox.id)
                if current.status != "running" or current.runtime_id != sandbox.runtime_id:
                    continue
                if alive is None:
                    db.set_sandbox_unreachable(id=sandbox.id)
                elif alive:
                    db.set_sandbox_reachable(id=sandbox.id)
                    if hung and db.get_active_job(sandbox_id=sandbox.id) is None:
                        db.set_sandbox_recovered(id=sandbox.id)
                        self.jobs.enqueue(db, sandbox.id, "stop", restart=True)
                        self.suspects.pop(sandbox.id, None)
                        self.logger.warning("VM hung, restarting it", extra={"sandbox_id": sandbox.id})
                elif db.get_active_job(sandbox_id=sandbox.id) is None:
                    self.halt(current, db)
                    self.logger.info("sandbox container gone", extra={"sandbox_id": sandbox.id})

    def hung(self, sandbox: Sandbox) -> bool:
        """Whether a running macOS or Windows VM has hung: its guest went quiet (no heartbeat) and its screen stopped
        answering, on checks HANG_SECONDS apart. Without a heartbeat (a VM with no guest) the screen is checked at most
        every half HANG_SECONDS."""
        if sandbox.kind not in VMS:
            return False
        now = time.monotonic()
        if hub.heartbeat(sandbox.id):
            self.suspects.pop(sandbox.id, None)
            return False
        if sandbox.id not in self.suspects and now - self.probed.get(sandbox.id, 0) < HANG_SECONDS / 2:
            return False
        self.probed[sandbox.id] = now
        if responsive(sandbox.runtime_id or ""):
            self.suspects.pop(sandbox.id, None)
            return False
        return now - self.suspects.setdefault(sandbox.id, now) >= HANG_SECONDS

    def watch_viewer(self, websocket: WebSocket, sandbox_id: str):
        """Counts an open viewer or terminal as activity, once a minute until it closes, so idle auto-stop leaves a
        sandbox someone is watching alone."""

        async def touch():
            while websocket.client_state.name == "CONNECTED":
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.touch, sandbox_id)
                await asyncio.sleep(VIEWER_TOUCH_SECONDS)

        task = asyncio.create_task(touch())
        self.viewers.add(task)
        task.add_done_callback(self.viewers.discard)

    def watch_gate(self, websocket: WebSocket, sandbox_id: str, live: Callable[[], bool] | None):
        """Every LINK_CHECK_SECONDS: a viewer whose link was revoked or ran out is disconnected."""

        async def watch():
            checked = time.monotonic()
            while websocket.client_state.name == "CONNECTED":
                await asyncio.sleep(1)
                with contextlib.suppress(Exception):
                    if live is not None and time.monotonic() - checked >= LINK_CHECK_SECONDS:
                        checked = time.monotonic()
                        if not await asyncio.to_thread(live):
                            await websocket.close(code=1008, reason="the link was revoked or has expired")
                            return

        task = asyncio.create_task(watch())
        self.viewers.add(task)
        task.add_done_callback(self.viewers.discard)

    def touch(self, sandbox_id: str):
        writer.touch(sandbox_id)

    def expire(self):
        """Stops running sandboxes past their maximum lifetime or idle past their timeout, as POST /stop would."""
        with db_manager.session() as db:
            expired = list(db.list_expired_sandboxes())
            for row in expired:
                db.cancel_sandbox_jobs(sandbox_id=row.id)
                self.jobs.enqueue(db, row.id, "stop")
            # a warm create whose API process died mid-hand-off left its sandbox provisioning with no job; a boot
            # adopts the pooled container and finishes it
            horizon = datetime.now(UTC) - timedelta(seconds=STRANDED_SECONDS)
            stranded = [
                sandbox
                for sandbox in db.list_all_sandboxes()
                if sandbox.status == "provisioning"
                and sandbox.updated_at < horizon
                and db.get_active_job(sandbox_id=sandbox.id) is None
            ]
            for sandbox in stranded:
                self.jobs.enqueue(db, sandbox.id, "boot", boot_deadline(sandbox.kind))
        for sandbox in stranded:
            self.logger.warning("stranded create resumed", extra={"sandbox_id": sandbox.id})
        for row in expired:
            self.logger.info("sandbox expired", extra={"sandbox_id": row.id, "reason": row.reason})
            audit_api.system(row.workspace_id, f"stop ({row.reason})", "sandboxes", row.id, reason=row.reason)
        if expired or stranded:
            self.jobs.kick()

    async def expire_loop(self):
        """Every minute, on the one worker that holds the lease."""
        while True:
            try:
                if await asyncio.to_thread(workers.hold, "expire", EXPIRE_SECONDS * 3):
                    await asyncio.to_thread(self.expire)
            except Exception as e:
                self.logger.error("expiring sandboxes failed", extra={"error": repr(e)})
            await asyncio.sleep(EXPIRE_SECONDS)

    def freeze(self, sandbox: Sandbox):
        """Pauses a running Linux sandbox whole: its Kata VM through vm-control (Kubernetes) or its container."""
        assert sandbox.runtime_id is not None
        if runtime.is_kube() and not sandbox.server_id:
            config = json.loads(sandbox.config or "{}")
            state = vm_control.current_state(sandbox.id, sandbox.runtime_id, config)
            vm_control.pause(
                sandbox.id,
                sandbox.runtime_id,
                config,
                operation_id=secrets.token_hex(16),
                expected_revision=state.revision,
            )
        else:
            runtime.pause(sandbox.runtime_id)

    def thaw(self, sandbox: Sandbox):
        assert sandbox.runtime_id is not None
        if runtime.is_kube() and not sandbox.server_id:
            config = json.loads(sandbox.config or "{}")
            state = vm_control.current_state(sandbox.id, sandbox.runtime_id, config)
            vm_control.resume(
                sandbox.id,
                sandbox.runtime_id,
                config,
                operation_id=secrets.token_hex(16),
                expected_revision=state.revision,
            )
        else:
            runtime.resume(sandbox.runtime_id)

    def bind(self, sandbox_id: str):
        """Records a Kubernetes sandbox's Kata VM generation for vm-control, once per pod it boots in."""
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=sandbox_id)
        if sandbox is None or not sandbox.runtime_id or sandbox.server_id or sandbox.kind in VMS:
            return
        config = json.loads(sandbox.config or "{}")
        if (config.get("vm_control") or {}).get("runtime_id") == sandbox.runtime_id:
            return
        config.pop("vm_control", None)
        config["vm_control"] = vm_control.binding(sandbox.id, sandbox.runtime_id, config)
        with db_manager.session() as db:
            db.update_sandbox(name=sandbox.name, resources=sandbox.resources, config=json.dumps(config), id=sandbox.id)

    async def pause_loop(self):
        """Every SWEEP_SECONDS: deletes paused sandboxes past PAUSE_MINUTES, and under memory pressure the oldest
        paused one on each pressured node, down to their volumes."""
        while True:
            try:
                await asyncio.to_thread(self.sweep_paused)
            except Exception as e:
                self.logger.error("sweeping paused sandboxes failed", extra={"error": repr(e)})
            await asyncio.sleep(SWEEP_SECONDS)

    def sweep_paused(self):
        with db_manager.session() as db:
            paused = list(db.list_paused_sandboxes())
        if not paused:
            return
        pressured = runtime.pressured_nodes(PRESSURE_SHARE)
        horizon = datetime.now(UTC) - timedelta(minutes=PAUSE_MINUTES)
        for sandbox in paused:
            node = runtime.node_of(sandbox)
            assert sandbox.paused_at is not None
            if sandbox.paused_at <= horizon or node in pressured:
                pressured.discard(node)
                self.unpause_delete(sandbox.id)

    def unpause_delete(self, sandbox_id: str):
        """Deletes a paused sandbox's container or pod; it stays stopped on its volume, and starts cold."""
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=sandbox_id)
            if sandbox is None or sandbox.status != "stopped" or sandbox.paused_at is None:
                return
            if db.get_active_job(sandbox_id=sandbox.id) is not None:
                return
        self.to_volume(sandbox)

    def to_volume(self, sandbox: Sandbox):
        """Deletes a paused sandbox's container or pod, leaving its volume, for the sweeper and for jobs that need
        the volume free (restore, move)."""
        if sandbox.runtime_id:
            remove_container(sandbox.runtime_id)
        forget_secrets(sandbox.id)
        with db_manager.session() as db:
            db.clear_sandbox_runtime(id=sandbox.id)
        self.logger.info("paused sandbox deleted to its volume", extra={"sandbox_id": sandbox.id})

    def runtime_event(self, event: dict):
        cleanup = []
        with db_manager.session() as db:
            states = {item["runtime_id"]: item for item in event.get("sandboxes", [])}
            if event.get("type") == "reconcile":
                running = set(event["running_ids"])
                rows = [
                    sandbox
                    for sandbox in db.list_all_sandboxes()
                    if sandbox.kind not in VMS and not sandbox.server_id and sandbox.runtime_id not in running
                ]
            else:
                sandbox = db.get_sandbox_by_runtime_id(runtime_id=event["runtime_id"])
                rows = [sandbox] if sandbox is not None else []
            for sandbox in rows:
                if sandbox.status != "running" or db.get_active_job(sandbox_id=sandbox.id) is not None:
                    continue
                state = states.get(sandbox.runtime_id, event)
                if sandbox.runtime_id:
                    cleanup.append(sandbox.runtime_id)
                db.clear_sandbox_runtime(id=sandbox.id)
                db.set_sandbox_reachable(id=sandbox.id)
                if state.get("status") == "failed":
                    db.set_sandbox_failed(error_message=state.get("error") or "sandbox runtime failed", id=sandbox.id)
                else:
                    db.set_sandbox_stopped(id=sandbox.id)
        for runtime_id in cleanup:
            try:
                remove_container(runtime_id)
            except Exception as error:
                self.logger.error("runtime cleanup failed", extra={"runtime_id": runtime_id, "error": str(error)})

    async def watch_vms(self):
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(db_manager.url, autocommit=True) as conn:
                    await conn.execute("LISTEN zoo_node_report")
                    await asyncio.to_thread(self.reconcile)
                    async for notice in conn.notifies():
                        await asyncio.to_thread(self.reconcile, False, notice.payload)
            except Exception as error:
                self.logger.error("node report stream failed", extra={"error": str(error)})
                await asyncio.sleep(1)

    async def watch(self):
        await asyncio.to_thread(self.reconcile, True)
        vm_task = asyncio.create_task(self.watch_vms())
        try:
            async for event in runtime.events():
                await asyncio.to_thread(self.runtime_event, event)
        finally:
            vm_task.cancel()
            await asyncio.gather(vm_task, return_exceptions=True)

    async def run_tool(self, user: Member, sandbox_id: str, name: str, args: dict, channel: str) -> Any:
        tool = TOOLS.get(name)
        if tool is None:
            raise HTTPException(status_code=404, detail=f"unknown tool {name}")
        # MCP calls come here without the request checks in AuthApi.current_user
        if name != "screenshot":
            require(user, "member")
        try:
            inspect.signature(tool.fn).bind("", **args)
        except TypeError as e:
            raise HTTPException(status_code=422, detail=str(e))
        sandbox, state = await self.allowed_async(sandbox_id, user, tool.permission, tool.action)
        allowed = KINDS.get(sandbox.kind)
        if allowed is not None and tool.category not in allowed:
            raise HTTPException(status_code=400, detail=f"{name} is not available in {sandbox.kind} sandboxes")
        secrets = state["secrets"]
        execution_id = str(uuid.uuid4())

        writer.execution(execution_id, sandbox.id, name, json.dumps(args)[:4000])
        began = time.monotonic()
        try:
            with tracer.start_as_current_span(
                f"tool {name}",
                attributes={"zoo.sandbox_id": sandbox.id, "zoo.channel": channel, "zoo.user_id": user.id},
            ):
                fn = tool.impl(sandbox.runtime_id)
                if inspect.iscoroutinefunction(fn):
                    result = await fn(sandbox.runtime_id, **args)
                else:
                    result = await asyncio.to_thread(fn, sandbox.runtime_id, **args)
        except Exception as e:
            metrics.tool_duration.record(
                time.monotonic() - began, {"tool": name, "channel": channel, "outcome": "error"}
            )
            metrics.error("tool", tool=name)
            error = redact(str(e), secrets)
            writer.finish(execution_id, "failed", error=error[:4000])
            # the sandbox failed it, not Zoo: its (redacted) error is the message
            raise ZooError("upstream_error", error)
        metrics.tool_duration.record(time.monotonic() - began, {"tool": name, "channel": channel, "outcome": "ok"})
        result = redact(result, secrets)
        if name == "screenshot":
            output = f"<{len(result)} bytes>"
        elif name == "screen_diff" and result.get("image"):
            output = json.dumps({**result, "image": f"<{len(result['image'])} bytes>"}, default=str)
        else:
            output = json.dumps(result, default=str)[:4000]
        writer.finish(execution_id, "completed", output=output)
        return result

    def linux_files(self, sandbox_id: str, user: Member, action: str) -> str:
        require(user, "member")
        with db_manager.session() as db:
            sandbox = self.allowed(sandbox_id, user, db, "files", action)
        if is_vm(sandbox.runtime_id) or sandbox.runtime_id is None:
            raise ZooError("not_supported", f"file transfer isn't available in {sandbox.kind} sandboxes")
        return sandbox.runtime_id

    async def stream_exec(self, user: Member, sandbox_id: str, exec_req: ExecRequest) -> Iterator[str]:
        tool = TOOLS["execute_command"]
        require(user, "member")
        sandbox, state = await self.allowed_async(sandbox_id, user, tool.permission, tool.action)
        if is_vm(sandbox.runtime_id) or KINDS.get(sandbox.kind) is not None and "shell" not in KINDS[sandbox.kind]:
            raise ZooError("not_supported", f"streamed exec isn't available in {sandbox.kind} sandboxes")
        secrets = state["secrets"]
        runtime_id = sandbox.runtime_id
        assert runtime_id is not None
        execution_id = str(uuid.uuid4())

        writer.execution(
            execution_id, sandbox.id, "execute_command", json.dumps({**exec_req.model_dump(), "stream": True})[:4000]
        )
        lines = ShellTools.stream_command(runtime_id, exec_req.command, exec_req.timeout)

        def frames() -> Iterator[str]:
            try:
                for stream, value in lines:
                    if stream == "exit":
                        output = json.dumps({"exit_code": value, "timed_out": value == 124})
                        writer.finish(execution_id, "completed", output=output)
                        yield f"event: exit\ndata: {output}\n\n"
                    else:
                        yield f"event: {stream}\ndata: {json.dumps({'data': redact(value, secrets)})}\n\n"
            except Exception as e:
                error = redact(str(e), secrets)
                writer.finish(execution_id, "failed", error=error[:4000])
                yield f"event: error\ndata: {json.dumps({'message': error})}\n\n"

        return frames()

    async def proxy(self, websocket: WebSocket, sandbox_id: str, ticket: str = ""):
        user_id = tickets.redeem(ticket, f"sandbox:{sandbox_id}")
        if user_id is None:
            await websocket.close(code=1008, reason="unauthorized")
            return
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=sandbox_id)
            usable = sandbox is not None and self.member_of(sandbox, user_id, db)
        if sandbox is None or not usable or sandbox.status != "running" or not has_screen(sandbox.kind):
            await websocket.close(code=1008, reason="sandbox not available")
            return
        await self.screen(websocket, sandbox, view_only=False)

    async def screen(
        self, websocket: WebSocket, sandbox: Sandbox, view_only: bool, live: Callable[[], bool] | None = None
    ):
        """Relays a viewer to the sandbox's VNC session in the gateway, which plays the VNC server to it and drops a
        view-only viewer's input. When `live` stops returning True (a revoked or expired link), the viewer is
        disconnected."""
        await websocket.accept()
        self.watch_viewer(websocket, sandbox.id)
        self.watch_gate(websocket, sandbox.id, live)
        await relay_screen(websocket, sandbox.id, view_only)

    async def terminal(self, websocket: WebSocket, sandbox_id: str, ticket: str = "", cols: int = 80, rows: int = 24):
        """A shell in the sandbox for a browser terminal. The client sends keystrokes as binary frames and
        {"type": "resize", "cols", "rows"} as text; it gets output as binary frames and {"type": "exit", "code"}."""
        user_id = tickets.redeem(ticket, f"terminal:{sandbox_id}")
        if user_id is None:
            await websocket.close(code=1008, reason="unauthorized")
            return
        with db_manager.session() as db:
            sandbox = db.get_sandbox(id=sandbox_id)
            usable = sandbox is not None and self.member_of(sandbox, user_id, db)
        if sandbox is None or not usable or sandbox.status != "running":
            await websocket.close(code=1008, reason="sandbox not available")
            return
        try:
            guest = await hub.for_sandbox_async(sandbox_id)
        except ZooError as error:
            await websocket.close(code=1011, reason=str(error)[:120])
            return
        if guest is None or not guest.has("pty"):
            await websocket.close(code=1011, reason="the sandbox's guest agent is not connected")
            return
        await websocket.accept()
        self.watch_viewer(websocket, sandbox_id)
        terminal = Terminal(guest)
        try:
            await terminal.open(max(1, min(cols, 1000)), max(1, min(rows, 1000)))
        except Exception as e:
            await websocket.close(code=1011, reason=str(e)[:120])
            return

        async def keystrokes():
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    await terminal.write(message["bytes"])
                elif message.get("text"):
                    try:
                        control = json.loads(message["text"])
                        if control.get("type") == "resize":
                            await terminal.resize(
                                max(1, min(int(control["cols"]), 1000)), max(1, min(int(control["rows"]), 1000))
                            )
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue

        async def output():
            while True:
                kind, value = await terminal.events.get()
                if kind == "data":
                    await websocket.send_bytes(value)
                else:
                    await websocket.send_text(json.dumps({"type": "exit", "code": value}))
                    return

        tasks = [asyncio.create_task(keystrokes()), asyncio.create_task(output())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await terminal.close()
            if websocket.client_state.name != "DISCONNECTED":
                await websocket.close()
