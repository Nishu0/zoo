"""The audit log: every write anyone makes, per workspace.

A middleware records each successful non-GET request made by a signed-in user or an API key: who (the user, and the
key in metadata), what (`METHOD /route/{template}`), on what (the route's first segment and first path id) and from
where. Request bodies are never recorded; they may hold secrets. Routes that write richer entries of their own
(vault secrets and profiles, server/security.py audit) are left to those. Work no request starts, like idle stops
and scheduled backups, records itself with `system` (actor null, metadata.source "system").

Admins read the log with GET /audit-logs, filtered and paged, and export it as CSV."""

import asyncio
import base64
import csv
import io
import json
import uuid
from collections.abc import Iterator
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db.connection import db_manager
from db.generated.query import CreateAuditLogParams, ListAuditLogsParams, ListAuditLogsRow, Querier
from logger.logger import logger
from server import pages
from server.auth_api import AuthApi, Member, require
from server.limits import client_ip
from utils.time import to_stamp

EXPORT_PAGE = 1000
# these write their own, richer entries (server/security.py audit)
SELF_AUDITED = (
    "/vault",
    "/profiles",
    "/sandboxes/{sandbox_id}/profiles",
    "/sandboxes/{sandbox_id}/vault-secrets",
)


class AuditLogResponse(BaseModel):
    id: str
    actor_id: str | None
    actor_email: str | None
    action: str
    resource_type: str
    resource_id: str | None
    sandbox_id: str | None
    metadata: dict
    created_at: str


class AuditPage(BaseModel):
    data: list[AuditLogResponse]
    # pass back as `cursor` for the next page; null on the last
    next_cursor: str | None


def system(workspace_id: str, action: str, resource_type: str, resource_id: str | None, **metadata):
    """Records work no request started (idle stops, scheduled backups)."""
    with db_manager.session() as db:
        db.create_audit_log(
            CreateAuditLogParams(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                actor_id=None,
                sandbox_id=resource_id if resource_type == "sandboxes" else None,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata=json.dumps({"source": "system", **metadata}),
            )
        )


def cursor_after(row: ListAuditLogsRow) -> str:
    return base64.urlsafe_b64encode(f"{row.created_at.isoformat()}|{row.id}".encode()).decode()


def entry(row: ListAuditLogsRow) -> AuditLogResponse:
    return AuditLogResponse(
        id=row.id,
        actor_id=row.actor_id,
        actor_email=row.actor_email,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        sandbox_id=row.sandbox_id,
        metadata=json.loads(row.metadata or "{}"),
        created_at=to_stamp(row.created_at),
    )


class AuditApi:
    def __init__(self, app: FastAPI, auth: AuthApi):
        self.app = app
        self.auth = auth
        self.app.middleware("http")(self.record)
        self._register_routes()

    async def record(self, request: Request, call_next):
        response = await call_next(request)
        member: Member | None = getattr(request.state, "member", None)
        route = getattr(request.scope.get("route"), "path", None)
        if (
            member is None
            or route is None
            or request.method in ("GET", "HEAD", "OPTIONS")
            or not 200 <= response.status_code < 300
            or route.startswith(SELF_AUDITED)
        ):
            return response
        params: dict[str, str] = request.scope.get("path_params") or {}
        # a route on a workspace is logged in that workspace, whichever one the request acted in
        workspace_id = params.get("workspace_id") or member.workspace_id
        metadata = {"ip": client_ip(request), "status": response.status_code}
        if member.key_id is not None:
            metadata["api_key_id"] = member.key_id
        row = CreateAuditLogParams(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            actor_id=member.id,
            sandbox_id=params.get("sandbox_id"),
            action=f"{request.method} {route}",
            resource_type=route.strip("/").split("/")[0],
            resource_id=next(iter(params.values()), None),
            metadata=json.dumps(metadata),
        )
        try:
            await asyncio.to_thread(self.write, row)
        except Exception as e:
            # the write already happened; a lost entry is logged rather than failing it after the fact
            logger.error("audit log write failed", extra={"action": row.action, "error": repr(e)})
        return response

    def write(self, row: CreateAuditLogParams):
        with db_manager.session() as db:
            # the workspace may be gone: the request deleted it
            if db.get_workspace(id=row.workspace_id) is not None:
                db.create_audit_log(row)

    def page(
        self,
        user: Member,
        db: Querier,
        limit: int,
        cursor: str | None,
        actor: str | None,
        action: str | None,
        resource_type: str | None,
        sandbox_id: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[ListAuditLogsRow]:
        before_at, before_id = None, None
        if cursor:
            try:
                at, _, before_id = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
                before_at = datetime.fromisoformat(at)
            except ValueError:
                raise HTTPException(status_code=422, detail="that cursor isn't one this API gave out")
        return list(
            db.list_audit_logs(
                ListAuditLogsParams(
                    workspace_id=user.workspace_id,
                    actor_id=actor,
                    action=action,
                    resource_type=resource_type,
                    sandbox_id=sandbox_id,
                    since=since,
                    until=until,
                    before_at=before_at,
                    before_id=before_id,
                    row_limit=limit,
                )
            )
        )

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/audit-logs", response_model=AuditPage)
        def list_audit_logs(
            page: pages.Params = Depends(),
            actor: str | None = None,
            action: str | None = None,
            resource_type: str | None = None,
            sandbox_id: str | None = None,
            since: datetime | None = None,
            until: datetime | None = None,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> AuditPage:
            require(user, "admin")
            rows = self.page(
                user, db, page.limit + 1, page.cursor, actor, action, resource_type, sandbox_id, since, until
            )
            last = rows[page.limit - 1] if len(rows) > page.limit else None
            return AuditPage(
                data=[entry(r) for r in rows[: page.limit]], next_cursor=cursor_after(last) if last else None
            )

        @self.app.get("/audit-logs/export")
        def export_audit_logs(
            actor: str | None = None,
            action: str | None = None,
            resource_type: str | None = None,
            sandbox_id: str | None = None,
            since: datetime | None = None,
            until: datetime | None = None,
            user: Member = Depends(current_user),
        ) -> StreamingResponse:
            require(user, "admin")

            def lines() -> Iterator[str]:
                out = io.StringIO()
                writer = csv.writer(out)
                columns = ["created_at", "actor_email", "actor_id", "action", "resource_type", "resource_id"]
                writer.writerow([*columns, "sandbox_id", "metadata"])
                cursor = None
                while True:
                    with db_manager.session() as db:
                        rows = self.page(
                            user, db, EXPORT_PAGE, cursor, actor, action, resource_type, sandbox_id, since, until
                        )
                    for r in rows:
                        writer.writerow(
                            [
                                to_stamp(r.created_at),
                                r.actor_email or "",
                                r.actor_id or "",
                                r.action,
                                r.resource_type,
                                r.resource_id or "",
                                r.sandbox_id or "",
                                r.metadata,
                            ]
                        )
                    yield out.getvalue()
                    out.seek(0)
                    out.truncate()
                    if len(rows) < EXPORT_PAGE:
                        return
                    cursor = cursor_after(rows[-1])

            return StreamingResponse(
                lines(),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
            )
