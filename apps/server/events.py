"""Events: what happened in a workspace, for apps built on Zoo to follow without polling.

Database triggers (db/migrations, zoo_event) write each event in the transaction that made the change, and notify
the zoo_event channel with the workspace's id:

- sandbox.state: a sandbox's status changed ({status, previous, error})
- server.state: a server changed, its node reported, or its connection changed ({server_id})
- pool.state: pool settings or inventory changed ({server_id, kind})
- snapshot.state / job.state: a sandbox's snapshot or job changed
- webhook.delivery: a delivery was created or attempted ({webhook_id, delivery_id, status, attempts})
- kubernetes.state: a requirements check completed

GET /events pages through them; GET /events/stream follows them as server-sent events, each with its seq as the
event id, so a client that reconnects with Last-Event-ID gets what it missed. Webhooks (server/webhooks.py) get the
same events except webhook.delivery, which is only for SSE to avoid recursive deliveries. API keys for one
sandbox, or for some labels, see only their sandboxes' events. Events are kept
ZOO_EVENT_RETENTION_DAYS (7 by default)."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import psycopg
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from apps.server import idempotency, labels, metrics, workers
from apps.server.auth_api import AuthApi, Member
from apps.server.jobs import stamp
from apps.server.logger import logger
from apps.server.utils import to_stamp
from db.connection import db_manager
from db.generated.models import Event, Server
from db.generated.query import ListEventsParams, ListLateEventsParams, Querier

RETENTION = timedelta(days=float(os.environ.get("ZOO_EVENT_RETENTION_DAYS", "7")))
PAGE = 200
# events commit in whatever order their transactions end, so one can appear with a lower seq than one already
# streamed; a live stream rereads this far back and skips what it has sent
LATE_SECONDS = 60
KEEPALIVE_SECONDS = 15
PURGE_SECONDS = 3600


class EventResponse(BaseModel):
    id: str
    seq: int
    type: str
    sandbox_id: str | None
    data: dict
    created_at: str


class EventPage(BaseModel):
    events: list[EventResponse]
    # pass back as `after` for the next page
    after: int


def event_response(event: Event) -> EventResponse:
    return EventResponse(
        id=event.id,
        seq=event.seq,
        type=event.type,
        sandbox_id=event.sandbox_id,
        data=json.loads(event.data),
        created_at=to_stamp(event.created_at),
    )


def server_state(db: Querier, server: Server, online: bool | None = None) -> None:
    data: dict = {"server_id": server.id}
    if online is not None:
        data["online"] = online
    db.emit_event(
        workspace_id=server.workspace_id,
        sandbox_id=None,
        type="server.state",
        data=json.dumps(data),
    )


class Scope:
    """Which events a member may see: all of their workspace's, or only their API key's sandboxes'."""

    def __init__(self, user: Member):
        self.user = user
        # sandbox id -> its labels, for a key with a label selector
        self.known: dict[str, dict[str, str]] = {}

    def allows(self, event: Event, db: Querier) -> bool:
        if self.user.key_sandbox_id is not None:
            return event.sandbox_id == self.user.key_sandbox_id
        if self.user.key_labels is not None:
            if event.sandbox_id is None:
                return False
            if event.sandbox_id not in self.known:
                self.known[event.sandbox_id] = labels.of(db, event.sandbox_id)
            return labels.matches(self.known[event.sandbox_id], self.user.key_labels)
        return True


def page(db: Querier, user: Member, scope: Scope, after: int, sandbox_id: str | None, type: str | None):
    """The next events after `after`: all the rows read (for the next `after`), and those the member may see."""
    rows = list(
        db.list_events(
            ListEventsParams(
                workspace_id=user.workspace_id,
                after_seq=after,
                sandbox_id=sandbox_id,
                type=type,
                row_limit=PAGE,
            )
        )
    )
    return rows, [row for row in rows if scope.allows(row, db)]


def late(db: Querier, user: Member, scope: Scope, upto: int, sandbox_id: str | None, type: str | None) -> list[Event]:
    """Recent events with a seq at most `upto`, which may have committed after the stream read past them."""
    rows = db.list_late_events(
        ListLateEventsParams(
            workspace_id=user.workspace_id,
            upto_seq=upto,
            since=stamp(-LATE_SECONDS),
            sandbox_id=sandbox_id,
            type=type,
            row_limit=PAGE,
        )
    )
    return [row for row in rows if scope.allows(row, db)]


async def follow(user: Member, after: int, sandbox_id: str | None, type: str | None) -> AsyncIterator[str]:
    """The workspace's events after `after`, then each new one as it is written, as SSE frames."""
    scope = Scope(user)
    # seq -> when it was written, of what was sent in the last LATE_SECONDS
    sent: dict[int, datetime] = {}
    # events at or before the starting point are the client's already; only later ones can be late
    floor = after

    def frame(row: Event) -> str:
        sent[row.seq] = row.created_at
        metrics.event_delivery.record((datetime.now(UTC) - row.created_at).total_seconds(), {"channel": "sse"})
        return f"id: {row.seq}\nevent: {row.type}\ndata: {event_response(row).model_dump_json()}\n\n"

    async with await psycopg.AsyncConnection.connect(db_manager.url, autocommit=True) as conn:
        # listening before the first read, so nothing written in between is missed
        await conn.execute("LISTEN zoo_event")
        while True:
            with db_manager.session() as db:
                stragglers = late(db, user, scope, after, sandbox_id, type) if after > floor else []
                rows, visible = page(db, user, scope, after, sandbox_id, type)
            for row in stragglers:
                if row.seq > floor and row.seq not in sent:
                    yield frame(row)
            for row in visible:
                if row.seq not in sent:
                    yield frame(row)
            after = max([after, *(row.seq for row in rows)])
            horizon = stamp(-LATE_SECONDS)
            sent = {seq: at for seq, at in sent.items() if at >= horizon}
            if len(rows) == PAGE:
                continue
            woke = False
            async for notice in conn.notifies(timeout=KEEPALIVE_SECONDS):
                if notice.payload == user.workspace_id:
                    woke = True
                    break
            if not woke:
                yield ": keepalive\n\n"


class EventsApi:
    def __init__(self, app: APIRouter, auth: AuthApi):
        self.logger = logger
        self.app = app
        self.auth = auth
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/events", response_model=EventPage)
        def list_events(
            after: int = Query(default=0, ge=0, description="the seq of the last event seen"),
            sandbox_id: str | None = None,
            type: str | None = None,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> EventPage:
            rows, visible = page(db, user, Scope(user), after, sandbox_id, type)
            return EventPage(
                events=[event_response(row) for row in visible], after=max([after, *(row.seq for row in rows)])
            )

        @self.app.get("/events/stream")
        def stream(
            after: int | None = Query(default=None, ge=0, description="start after this seq; default: from now"),
            sandbox_id: str | None = None,
            type: str | None = None,
            last_event_id: str | None = Header(default=None),
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> StreamingResponse:
            # a reconnecting EventSource sends the id of the last event it got
            start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
            if start is None:
                start = db.latest_event_seq(workspace_id=user.workspace_id) or 0
            return StreamingResponse(
                follow(user, start, sandbox_id, type),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )


def purge():
    with db_manager.session() as db:
        db.purge_events(created_at=stamp(-RETENTION.total_seconds()))
        db.purge_webhook_deliveries(created_at=stamp(-RETENTION.total_seconds()))
    idempotency.purge()


async def purge_loop():
    """Hourly, on the one worker that holds the lease: forgets events (and their deliveries) past retention."""
    while True:
        try:
            if await asyncio.to_thread(workers.hold, "events", PURGE_SECONDS * 2):
                await asyncio.to_thread(purge)
        except Exception as e:
            logger.error("purging events failed", extra={"error": repr(e)})
        await asyncio.sleep(PURGE_SECONDS)
