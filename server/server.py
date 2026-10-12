import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db.connection import db_manager
from logger.logger import logger
from mcp_tools.server import build_mcp
from server import (
    backups,
    errors,
    events,
    gateway,
    health,
    idempotency,
    metering,
    nodes,
    objects,
    webhooks,
    workers,
)
from server.admin_api import AdminApi
from server.audit_api import AuditApi
from server.auth_api import AuthApi
from server.events import EventsApi
from server.metering import UsageApi
from server.monitor import MonitoringApi
from server.nodes_api import NodesApi
from server.policy_api import SandboxPolicyApi
from server.sandbox_api import SandboxApi
from server.servers_api import ServersApi, prepull_images
from server.telemetry import setup_telemetry
from server.vault_api import VaultApi, remind
from server.viewer_api import ViewerApi
from server.webhooks import WebhooksApi
from server.workspaces_api import WorkspacesApi


async def housekeeping():
    """Hourly upkeep: logs vault secrets that are due for rotation or expiring."""
    while True:
        try:
            await asyncio.to_thread(remind)
        except Exception as e:
            logger.error("housekeeping failed", extra={"error": repr(e)})
        await asyncio.sleep(3600)


class Server:
    def __init__(self, port=8000):
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            with db_manager.session() as db:
                if workers.works():
                    prepull_images(list(db.list_all_servers()))
            # every process holds its own node streams, since each reaches hosts through them, unless a gateway
            # holds them for all (server/gateway.py)
            node_server = None
            if os.environ.get("ZOO_NODE_PORT") != "off" and not gateway.enabled():
                node_server, _ = await asyncio.to_thread(nodes.serve)
            background: list[asyncio.Task] = []
            if workers.works():
                background = [
                    asyncio.create_task(self.sandbox_api.watch()),
                    asyncio.create_task(self.sandbox_api.pool.run()),
                    asyncio.create_task(self.sandbox_api.expire_loop()),
                    asyncio.create_task(backups.loop(self.sandbox_api)),
                    asyncio.create_task(housekeeping()),
                    asyncio.create_task(events.purge_loop()),
                    asyncio.create_task(webhooks.loop()),
                    asyncio.create_task(metering.loop()),
                ]
                jobs = asyncio.create_task(self.sandbox_api.jobs.run())
            else:
                jobs = None
            async with self.mcp.session_manager.run():
                yield
            # drain: unfinished jobs are requeued and resume on the next start
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await self.sandbox_api.jobs.shutdown()
            if jobs is not None:
                jobs.cancel()
            if node_server is not None:
                await asyncio.to_thread(nodes.stop, node_server)

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

        # the versioned API; /healthz, /readyz, /embed, /mcp, /guest/connect and /internal/* stay unprefixed
        v1 = APIRouter(prefix="/v1")
        health.register(self.app)
        if workers.ROLE == "gateway":
            gateway.register(self.app)
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
        uvicorn.run(
            import_string,
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", self.port)),
            reload=os.environ.get("RELOAD", "1") == "1",
            # long-lived connections (desktop viewers, agent streams) get this long to close before shutdown
            timeout_graceful_shutdown=15,
        )
