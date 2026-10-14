"""Ordered, process-local log batches; state transitions never pass through this queue."""

import asyncio
import contextlib
import logging
import queue
import threading
from datetime import UTC, datetime
from typing import Any

from db.async_connection import async_db

FLUSH_SECONDS = 0.05
BATCH_ROWS = 500
MAX_PENDING = 10000
logger = logging.getLogger(__name__)

START_COLUMNS = ("id", "sandbox_id", "tool_name", "input", "started_at", "created_at")
FINISH_COLUMNS = ("id", "status", "output", "error_message", "completed_at")
AUDIT_COLUMNS = (
    "id",
    "workspace_id",
    "actor_id",
    "sandbox_id",
    "action",
    "resource_type",
    "resource_id",
    "metadata",
    "created_at",
)
USAGE_COLUMNS = ("workspace_id", "sandbox_id", "metric", "period_start", "quantity")


class Writer:
    def __init__(self):
        self.queue: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue(MAX_PENDING)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.wake: asyncio.Event | None = None
        self.task: asyncio.Task | None = None
        self.drained = threading.Event()
        self.drained.set()
        self.state_lock = threading.Lock()
        self.accepting = False

    async def start(self):
        if self.task is not None:
            return
        async_db.require()
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self.accepting = True
        self.task = asyncio.create_task(self.run(), name="batched-request-writer")

    def enqueue(self, kind: str, row: tuple[Any, ...]):
        if not self.accepting or self.loop is None or self.wake is None:
            raise RuntimeError("request writer is not running")
        with self.state_lock:
            try:
                self.queue.put_nowait((kind, row))
            except queue.Full:
                logger.error("request log queue is full; entry discarded", extra={"kind": kind})
                return
            self.drained.clear()
        if self.queue.qsize() >= BATCH_ROWS:
            self.loop.call_soon_threadsafe(self.wake.set)

    def execution(self, identity: str, sandbox_id: str, tool_name: str, input: str):
        now = datetime.now(UTC)
        self.enqueue("start", (identity, sandbox_id, tool_name, input, now, now))
        self.touch(sandbox_id, now)

    def finish(self, identity: str, status: str, output: str | None = None, error: str | None = None):
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid execution completion status")
        self.enqueue("finish", (identity, status, output, error, datetime.now(UTC)))

    def touch(self, sandbox_id: str, at: datetime | None = None):
        self.enqueue("touch", (sandbox_id, at or datetime.now(UTC)))

    def audit(self, row):
        self.enqueue("audit", tuple(getattr(row, column) for column in AUDIT_COLUMNS[:-1]) + (datetime.now(UTC),))

    def usage(self, workspace_id: str, sandbox_id: str, metric: str, quantity: float, at: datetime):
        if quantity:
            self.enqueue(
                "usage", (workspace_id, sandbox_id, metric, at.replace(minute=0, second=0, microsecond=0), quantity)
            )

    async def run(self):
        assert self.wake is not None
        while self.accepting or not self.queue.empty():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), FLUSH_SECONDS)
            self.wake.clear()
            await self.batch()
            if self.queue.qsize() >= BATCH_ROWS:
                self.wake.set()

    async def batch(self):
        rows: list[tuple[str, tuple[Any, ...]]] = []
        for _ in range(BATCH_ROWS):
            try:
                rows.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if not rows:
            with self.state_lock:
                if self.queue.unfinished_tasks == 0:
                    self.drained.set()
            return
        grouped: dict[str, list[tuple[Any, ...]]] = {}
        for kind, row in rows:
            grouped.setdefault(kind, []).append(row)
        try:
            await self.persist(grouped)
        except Exception:
            logger.exception("request log batch failed; log entries discarded", extra={"rows": len(rows)})
        finally:
            with self.state_lock:
                for _ in rows:
                    self.queue.task_done()
                if self.queue.unfinished_tasks == 0:
                    self.drained.set()

    async def persist(self, groups: dict[str, list[tuple[Any, ...]]]):
        async with async_db.require().acquire() as connection, connection.transaction():
            if rows := groups.get("start"):
                await connection.execute("""
                    CREATE TEMP TABLE writer_start (id text, sandbox_id text, tool_name text,
                        input text, started_at timestamptz, created_at timestamptz) ON COMMIT DROP
                """)
                await connection.copy_records_to_table("writer_start", records=rows, columns=START_COLUMNS)
                await connection.execute("""
                    INSERT INTO tool_executions (id, sandbox_id, tool_name, input, status, started_at, created_at)
                    SELECT w.id, w.sandbox_id, w.tool_name, w.input, 'running', w.started_at, w.created_at
                    FROM writer_start w JOIN sandboxes s ON s.id = w.sandbox_id
                    ON CONFLICT (id) DO NOTHING
                """)
            if rows := groups.get("finish"):
                await connection.execute("""
                    CREATE TEMP TABLE writer_finish (id text, status text, output text,
                        error_message text, completed_at timestamptz) ON COMMIT DROP
                """)
                await connection.copy_records_to_table("writer_finish", records=rows, columns=FINISH_COLUMNS)
                await connection.execute("""
                    UPDATE tool_executions t SET status = w.status, output = w.output,
                        error_message = w.error_message, completed_at = w.completed_at
                    FROM (SELECT DISTINCT ON (id) * FROM writer_finish ORDER BY id, completed_at DESC) w
                    WHERE t.id = w.id
                """)
            if rows := groups.get("touch"):
                await connection.execute("CREATE TEMP TABLE writer_touch (id text, at timestamptz) ON COMMIT DROP")
                await connection.copy_records_to_table("writer_touch", records=rows, columns=("id", "at"))
                await connection.execute("""
                    UPDATE sandboxes s SET last_activity_at = greatest(s.last_activity_at, w.at)
                    FROM (SELECT id, max(at) AS at FROM writer_touch GROUP BY id) w WHERE s.id = w.id
                """)
            if rows := groups.get("key_touch"):
                await connection.execute("CREATE TEMP TABLE writer_keys (id text, at timestamptz) ON COMMIT DROP")
                await connection.copy_records_to_table("writer_keys", records=rows, columns=("id", "at"))
                await connection.execute("""
                    UPDATE api_keys k SET last_used_at = greatest(k.last_used_at, w.at)
                    FROM (SELECT id, max(at) AS at FROM writer_keys GROUP BY id) w WHERE k.id = w.id
                """)
            if rows := groups.get("audit"):
                await connection.execute("CREATE TEMP TABLE writer_audit (LIKE audit_logs) ON COMMIT DROP")
                await connection.copy_records_to_table("writer_audit", records=rows, columns=AUDIT_COLUMNS)
                await connection.execute("""
                    INSERT INTO audit_logs (id, workspace_id, actor_id, sandbox_id, action,
                        resource_type, resource_id, metadata, created_at)
                    SELECT a.id, a.workspace_id, u.id, s.id, a.action, a.resource_type,
                        a.resource_id, a.metadata, a.created_at
                    FROM writer_audit a JOIN workspaces w ON w.id = a.workspace_id
                    LEFT JOIN users u ON u.id = a.actor_id LEFT JOIN sandboxes s ON s.id = a.sandbox_id
                    ON CONFLICT (id) DO NOTHING
                """)
            if rows := groups.get("usage"):
                await connection.execute("CREATE TEMP TABLE writer_usage (LIKE usage) ON COMMIT DROP")
                await connection.copy_records_to_table("writer_usage", records=rows, columns=USAGE_COLUMNS)
                await connection.execute("""
                    INSERT INTO usage (workspace_id, sandbox_id, metric, period_start, quantity)
                    SELECT u.workspace_id, u.sandbox_id, u.metric, u.period_start, sum(u.quantity)
                    FROM writer_usage u JOIN workspaces w ON w.id = u.workspace_id
                    GROUP BY u.workspace_id, u.sandbox_id, u.metric, u.period_start
                    ON CONFLICT (workspace_id, sandbox_id, metric, period_start)
                    DO UPDATE SET quantity = usage.quantity + excluded.quantity
                """)

    async def flush(self):
        while not self.drained.is_set():
            await asyncio.sleep(0.01)

    def flush_sync(self):
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is not None and current is self.loop:
            raise RuntimeError("await writer.flush() on the API event loop")
        self.drained.wait()

    async def close(self):
        self.accepting = False
        task, self.task = self.task, None
        if task is not None:
            assert self.wake is not None
            self.wake.set()
            await task
        while not self.queue.empty():
            await self.batch()
        self.loop, self.wake = None, None
        self.drained.set()


writer = Writer()
