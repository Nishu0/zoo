import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.server import (
    backups,
    errors,
    events,
    health,
    idempotency,
    metering,
    objects,
    remote_docker,
    requirements,
    webhooks,
    workers,
)
from apps.server.admin_api import AdminApi
from apps.server.audit_api import AuditApi
from apps.server.auth_api import AuthApi
from apps.server.cache import cache
from apps.server.events import EventsApi
from apps.server.gateway_client import client
from apps.server.guest import hub
from apps.server.logger import logger
from apps.server.mcp import build_mcp
from apps.server.metering import UsageApi
from apps.server.monitor import MonitoringApi
from apps.server.nodes import hub as node_hub
from apps.server.nodes_api import NodesApi
from apps.server.policy_api import SandboxPolicyApi
from apps.server.sandbox_api import SandboxApi
from apps.server.servers_api import ServersApi
from apps.server.telemetry import setup_telemetry
from apps.server.vault_api import VaultApi, remind
from apps.server.viewer_api import ViewerApi
from apps.server.webhooks import WebhooksApi
from apps.server.workspaces_api import WorkspacesApi
from apps.server.writer import writer
from db.async_connection import async_db
from db.connection import db_manager


async def housekeeping():
    """Hourly upkeep: logs vault secrets that are due for rotation or expiring."""
    while True:
        try:
            await asyncio.to_thread(remind)
        except Exception as e:
            logger.error("housekeeping failed", extra={"error": repr(e)})
        await asyncio.sleep(3600)


def processes() -> int:
    """How many API processes uvicorn runs. One by default: the API is async, and each extra process brings its own
    database pools; raise ZOO_API_WORKERS on a busy, many-core server. Always one for the worker role: every process
    would run its own copy of the background loops."""
    if workers.ROLE == "worker":
        return 1
    return int(os.environ.get("ZOO_API_WORKERS") or 1)


class Server:
    def __init__(self, port=8000):
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            background: list[asyncio.Task] = []
            jobs = None
            await client.start()
            try:
                await async_db.start()
                await writer.start()
                await cache.start()
                failed = await asyncio.to_thread(requirements.failures)
                if failed:
                    raise RuntimeError("\n".join(failed))
                if workers.works():
                    background = [
                        asyncio.create_task(self.sandbox_api.watch()),
                        asyncio.create_task(self.sandbox_api.pool.run()),
                        asyncio.create_task(self.sandbox_api.expire_loop()),
                        asyncio.create_task(self.sandbox_api.pause_loop()),
                        asyncio.create_task(backups.loop(self.sandbox_api)),
                        asyncio.create_task(housekeeping()),
                        asyncio.create_task(events.purge_loop()),
                        asyncio.create_task(webhooks.loop()),
                        asyncio.create_task(metering.loop()),
                    ]
                    jobs = asyncio.create_task(self.sandbox_api.jobs.run())
                async with self.mcp.session_manager.run():
                    yield
            finally:
                try:
                    await self.servers_api.shutdown()
                    for task in background:
                        task.cancel()
                    await asyncio.gather(*background, return_exceptions=True)
                    await self.sandbox_api.jobs.shutdown()
                    await asyncio.to_thread(remote_docker.shutdown)
                    if jobs is not None:
                        jobs.cancel()
                        await asyncio.gather(jobs, return_exceptions=True)
                finally:
                    try:
                        await asyncio.gather(hub.shutdown(), node_hub.shutdown())
                    finally:
                        try:
                            await writer.close()
                        finally:
                            try:
                                await client.shutdown()
                            finally:
                                try:
                                    await cache.close()
                                finally:
                                    await async_db.close()

        self.app = FastAPI(lifespan=lifespan)
        setup_telemetry(self.app)
        self.port = port

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(","),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-Id", "Idempotent-Replayed"],
        )

        errors.register(self.app)
        idempotency.register(self.app)

        db_manager.init_db()
        objects.require()

        # the versioned API; probes, embedded viewers and MCP stay unprefixed
        v1 = APIRouter(prefix="/v1")
        health.register(self.app)
        self.auth_api = AuthApi(v1)
        self.sandbox_api = SandboxApi(v1, self.auth_api, self.app)
        self.policy_api = SandboxPolicyApi(v1, self.auth_api, self.sandbox_api)
        self.monitoring_api = MonitoringApi(v1, self.auth_api)
        self.admin_api = AdminApi(v1, self.auth_api, self.sandbox_api)
        self.servers_api = ServersApi(v1, self.auth_api, self.sandbox_api)
        self.nodes_api = NodesApi(v1, self.auth_api)
        self.vault_api = VaultApi(v1, self.auth_api, self.sandbox_api)
        self.workspaces_api = WorkspacesApi(v1, self.auth_api)
        self.audit_api = AuditApi(v1, self.auth_api, self.app)
        self.viewer_api = ViewerApi(v1, self.auth_api, self.sandbox_api, self.app)
        self.events_api = EventsApi(v1, self.auth_api)
        self.webhooks_api = WebhooksApi(v1, self.auth_api)
        self.usage_api = UsageApi(v1, self.auth_api)
        self.app.include_router(v1)
        self.mcp = build_mcp(self.auth_api, self.sandbox_api)
        self.app.mount("/mcp", self.mcp.streamable_http_app(streamable_http_path="/"))

    def start(self, import_string: str = "main:app"):
        reload = os.environ.get("RELOAD", "1") == "1"
        uvicorn.run(
            import_string,
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", self.port)),
            reload=reload,
            workers=None if reload else processes(),
            # long-lived connections (desktop viewers, agent streams) get this long to close before shutdown
            timeout_graceful_shutdown=15,
        )
