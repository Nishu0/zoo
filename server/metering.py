"""Usage metering, so builders can bill their own customers: per sandbox, in hourly buckets, kept after the sandbox
is gone and grouped by its labels (a copy of them stays in usage_labels).

- cpu_minutes, memory_mb_minutes: what running sandboxes are allocated (their size, server/sizes.py), not what they
  use; sampled once a minute by the worker that holds the metering lease, so a minute no worker was up isn't counted
- storage_gb_hours: each sandbox's disk and snapshots, and the workspace's profiles (under sandbox ''), once an hour

GET /usage reports them by sandbox, day or a label's value; /usage/export as CSV. Admins see the workspace's; an
API key with a label selector sees its own sandboxes'."""

import asyncio
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db.connection import db_manager
from db.generated.query import Querier
from logger.logger import logger
from server import labels, sizes, workers
from server.auth_api import AuthApi, Member, require
from server.jobs import stamp
from server.writer import writer
from utils.time import to_stamp

METER_SECONDS = 60
METRICS = ("cpu_minutes", "memory_mb_minutes", "storage_gb_hours")
# a report spans at most this
MAX_DAYS = 366


def add(db: Querier, workspace_id: str, sandbox_id: str, metric: str, quantity: float, at: datetime | None = None):
    writer.usage(workspace_id, sandbox_id, metric, quantity, at or stamp())


def meter():
    """One minute of every running sandbox's CPUs and memory, and storage for workspaces not metered this hour."""
    minutes = METER_SECONDS / 60
    now = stamp()
    with db_manager.session() as db:
        for sandbox in db.list_sandboxes_by_status(status="running"):
            add(db, sandbox.workspace_id, sandbox.id, "cpu_minutes", sandbox.cpus * minutes, now)
            add(db, sandbox.workspace_id, sandbox.id, "memory_mb_minutes", sandbox.memory_mb * minutes, now)
        for workspace_id in list(db.claim_storage_metering()):
            for row in db.sandbox_storage(unsized_gb=sizes.UNSIZED_DISK_GB, workspace_id=workspace_id):
                add(db, workspace_id, row.id, "storage_gb_hours", row.gb, now)
            add(db, workspace_id, "", "storage_gb_hours", db.profile_storage(workspace_id=workspace_id) or 0, now)


async def loop():
    """Every minute, on the one worker that holds the lease."""
    while True:
        try:
            if await asyncio.to_thread(workers.hold, "metering", METER_SECONDS * 3):
                await asyncio.to_thread(meter)
        except Exception as e:
            logger.error("metering failed", extra={"error": repr(e)})
        await asyncio.sleep(METER_SECONDS)


class UsageRow(BaseModel):
    # the sandbox id, the day (YYYY-MM-DD) or the label's value; null for usage without one (a sandbox without the
    # label, or the workspace's own storage)
    group: str | None
    metric: str
    quantity: float


class UsageReport(BaseModel):
    since: str
    until: str
    group_by: str
    rows: list[UsageRow]


def report(
    db: Querier, user: Member, since: datetime, until: datetime, group_by: str, metric: str | None
) -> list[UsageRow]:
    if until <= since or until - since > timedelta(days=MAX_DAYS):
        raise HTTPException(status_code=422, detail=f"`to` must be after `from`, at most {MAX_DAYS} days later")
    if metric is not None and metric not in METRICS:
        raise HTTPException(status_code=422, detail=f"unknown metric; one of {', '.join(METRICS)}")
    kept: dict[str, dict[str, str]] = defaultdict(dict)
    for row in db.list_usage_labels(workspace_id=user.workspace_id):
        kept[row.sandbox_id][row.key] = row.value
    totals: dict[tuple[str | None, str], float] = defaultdict(float)
    for row in db.usage_report(workspace_id=user.workspace_id, since=since, until=until, metric=metric):
        if user.key_labels is not None and not labels.matches(kept.get(row.sandbox_id, {}), user.key_labels):
            continue
        if group_by == "sandbox":
            group = row.sandbox_id or None
        elif group_by == "day":
            group = row.day.strftime("%Y-%m-%d")
        else:
            group = kept.get(row.sandbox_id, {}).get(group_by.removeprefix("label:"))
        totals[(group, row.metric)] += row.quantity
    return [
        UsageRow(group=group, metric=metric, quantity=round(quantity, 6))
        for (group, metric), quantity in sorted(totals.items(), key=lambda item: (item[0][0] or "", item[0][1]))
    ]


class UsageApi:
    def __init__(self, app: APIRouter, auth: AuthApi):
        self.app = app
        self.auth = auth
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        def params(
            since: datetime = Query(alias="from"),
            until: datetime = Query(alias="to"),
            group_by: str = Query(default="sandbox", pattern=r"^(sandbox|day|label:[a-z0-9][a-z0-9_./-]{0,62})$"),
            metric: str | None = None,
        ) -> tuple[datetime, datetime, str, str | None]:
            return since, until, group_by, metric

        def allowed(user: Member):
            # a key for an end user's labels sees that end user's usage; otherwise it's the admins'
            if user.key_labels is None:
                require(user, "admin")

        @self.app.get("/usage", response_model=UsageReport)
        def usage(
            query: tuple[datetime, datetime, str, str | None] = Depends(params),
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> UsageReport:
            allowed(user)
            since, until, group_by, metric = query
            return UsageReport(
                since=to_stamp(since),
                until=to_stamp(until),
                group_by=group_by,
                rows=report(db, user, since, until, group_by, metric),
            )

        @self.app.get("/usage/export")
        def export(
            query: tuple[datetime, datetime, str, str | None] = Depends(params),
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> StreamingResponse:
            allowed(user)
            since, until, group_by, metric = query
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow([group_by.split(":")[0], "metric", "quantity"])
            for row in report(db, user, since, until, group_by, metric):
                writer.writerow([row.group or "", row.metric, row.quantity])
            return StreamingResponse(
                iter([out.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="zoo-usage.csv"'},
            )
