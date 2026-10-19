import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.server import runtime
from apps.server.auth_api import AuthApi, Member
from apps.server.guest import hub
from db.connection import db_manager
from db.generated.query import Querier


def cpu(stats):
    cpu = stats.get("cpu_stats", {})
    precpu = stats.get("precpu_stats", {})

    current = cpu.get("cpu_usage", {}).get("total_usage", 0)
    previous = precpu.get("cpu_usage", {}).get("total_usage", 0)

    system = cpu.get("system_cpu_usage", 0)
    previous_system = precpu.get("system_cpu_usage", 0)

    cpu_delta = current - previous
    system_delta = system - previous_system

    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0

    online_cpus = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage", []) or [1])

    return round((cpu_delta / system_delta) * online_cpus * 100, 2)


def collect_container(stats: dict):
    result = {
        "status": "running",
        "cpu_percent": 0,
        "memory_usage": 0,
        "memory_limit": 0,
        "memory_percent": 0,
        "network_rx": 0,
        "network_tx": 0,
        "block_read": 0,
        "block_write": 0,
        "pids": 0,
        "collected_at": datetime.now(UTC).isoformat(),
    }

    try:
        memory = stats.get("memory_stats", {})
        memory_usage = memory.get("usage", 0)
        cache = memory.get("stats", {}).get("inactive_file", 0)

        # Linux cgroup v1 compatibility
        if not cache:
            cache = memory.get("stats", {}).get("cache", 0)

        used = max(0, memory_usage - cache)
        limit = memory.get("limit", 0)

        result["cpu_percent"] = cpu(stats)
        result["memory_usage"] = used
        result["memory_limit"] = limit
        result["memory_percent"] = round((used / limit) * 100, 2) if limit else 0

        networks = stats.get("networks", {}).values()

        result["network_rx"] = sum(n.get("rx_bytes", 0) for n in networks)
        result["network_tx"] = sum(n.get("tx_bytes", 0) for n in networks)

        blkio = stats.get("blkio_stats", {}).get("io_service_bytes_recursive") or []

        result["block_read"] = sum(item.get("value", 0) for item in blkio if item.get("op", "").lower() == "read")

        result["block_write"] = sum(item.get("value", 0) for item in blkio if item.get("op", "").lower() == "write")

        result["pids"] = stats.get("pids_stats", {}).get("current", 0) or 0

    except Exception as exc:
        result["metrics_error"] = str(exc)

    return result


def guest_usage(sandbox_id: str) -> dict | None:
    guest = hub.for_sandbox(sandbox_id)
    if guest is None or not guest.metrics:
        return None
    usage = {"cpu_percent": 0.0, "memory_usage": 0, "memory_limit": 0, "memory_percent": 0.0}
    usage |= {"network_rx": 0, "network_tx": 0, "pids": 0}
    return {**usage, **{k: v for k, v in guest.metrics.items() if v is not None}, "status": "running"}


class HostResponse(BaseModel):
    name: str
    os: str
    architecture: str
    docker_version: str
    cpus: int
    memory_total: int
    containers_running: int
    images: int


class SandboxUsage(BaseModel):
    sandbox_id: str
    name: str
    status: str
    cpu_percent: float
    memory_usage: int
    memory_limit: int
    memory_percent: float
    network_rx: int
    network_tx: int
    pids: int


class MonitoringResponse(BaseModel):
    host: HostResponse
    sandboxes: dict[str, int]
    cpu_percent: float
    memory_usage: int
    usage: list[SandboxUsage]
    collected_at: str


class MonitoringApi:
    def __init__(self, app: APIRouter, auth: AuthApi):
        self.app = app
        self.auth = auth
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/monitoring", response_model=MonitoringResponse)
        async def monitoring(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> MonitoringResponse:
            sandboxes = list(db.list_sandboxes_by_workspace(workspace_id=user.workspace_id))
            try:
                info = await asyncio.to_thread(runtime.local_info)
                running = [
                    s
                    for s in sandboxes
                    if s.status == "running" and s.runtime_id and s.kind not in ("macos", "windows")
                ]
                containers = await asyncio.gather(*[asyncio.to_thread(self._collect, s.runtime_id) for s in running])
            except runtime.RUNTIME_ERRORS as exc:
                name = "kubernetes" if runtime.is_kube() else "docker"
                raise HTTPException(status_code=503, detail=f"{name} unavailable: {exc}")

            # docker stats only reach containers on this host; a sandbox's guest reports from anywhere
            containers = [c if c is not None else guest_usage(s.id) for s, c in zip(running, containers)]
            usage = [
                SandboxUsage(
                    **{**{k: c[k] for k in SandboxUsage.model_fields if k in c}, "sandbox_id": s.id, "name": s.name}
                )
                for s, c in zip(running, containers)
                if c is not None
            ]
            counts: dict[str, int] = {}
            for s in sandboxes:
                counts[s.status] = counts.get(s.status, 0) + 1

            return MonitoringResponse(
                host=HostResponse(
                    name=info.get("Name", "local"),
                    os=info.get("OperatingSystem", "unknown"),
                    architecture=info.get("Architecture", "unknown"),
                    docker_version=info.get("ServerVersion", "unknown"),
                    cpus=info.get("NCPU", 0),
                    memory_total=info.get("MemTotal", 0),
                    containers_running=info.get("ContainersRunning", 0),
                    images=info.get("Images", 0),
                ),
                sandboxes={"total": len(sandboxes), **counts},
                cpu_percent=round(sum(u.cpu_percent for u in usage), 2),
                memory_usage=sum(u.memory_usage for u in usage),
                usage=usage,
                collected_at=datetime.now(UTC).isoformat(),
            )

    def _collect(self, container_id: str):
        try:
            stats = runtime.local_stats(container_id)
            return stats if runtime.is_kube() or stats is None else collect_container(stats)
        except runtime.NOT_FOUND_ERRORS:
            return None
