"""Liveness and readiness probes: /healthz says the process is up, /readyz checks what it depends on."""

import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from db.connection import db_manager
from server.requirements import CHECKS
from server.runtime import host_requirements

TIMEOUT = 5


async def check(fn, *args) -> str:
    try:
        await asyncio.wait_for(asyncio.to_thread(fn, *args), TIMEOUT)
        return "ok"
    except TimeoutError:
        return f"no answer within {TIMEOUT}s"
    except Exception as e:
        return str(e) or e.__class__.__name__


def register(app: FastAPI):
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(servers: bool = False):
        """Check service dependencies by default; servers=true also diagnoses enrolled hosts.

        A tenant host going offline must not remove the control plane from service.
        """
        names = list(CHECKS)
        probes = [check(fn) for fn in CHECKS.values()]
        found = []
        if servers:
            try:
                with db_manager.session() as db:
                    found = list(db.list_all_servers())
            except Exception:
                found = []
        for server in found:
            names.append(f"server:{server.name}")
            probes.append(check(host_requirements, server))
        checks = dict(zip(names, await asyncio.gather(*probes), strict=True))
        ready = all(result == "ok" for result in checks.values())
        return JSONResponse(
            {"status": "ok" if ready else "unavailable", "checks": checks}, status_code=200 if ready else 503
        )
