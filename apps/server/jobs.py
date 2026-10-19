"""A persistent queue for sandbox lifecycle work (boot, stop, delete, move).

Jobs live in the `jobs` table, so a restart resumes them instead of losing them. Each job retries with backoff up to
its attempt limit and fails for good once it runs out of attempts or passes its deadline. Work for one sandbox runs one
job at a time, and handlers are written to be safe to run again.
"""

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from apps.server import metrics
from apps.server.logger import logger
from db.connection import db_manager
from db.generated.models import Job
from db.generated.query import CreateJobParams, Querier

PARALLEL = 8
POLL_SECONDS = 1
# how long a job may wait for room (Wait) before it fails, and how often it looks again
MAX_WAIT = 2 * 3600
WAIT_POLL = 15
WAITING = "Waiting: "


class Wait(Exception):
    """Raised by a handler that can't run yet, such as a boot on a Mac already running Apple's limit of macOS VMs.
    The job goes back in the queue without using an attempt and gets a fresh deadline of `window` seconds, for up to
    MAX_WAIT in all."""

    def __init__(self, reason: str, window: int = 10 * 60):
        super().__init__(reason)
        self.window = window


def stamp(seconds: float = 0) -> datetime:
    """UTC now, `seconds` from now."""
    return datetime.now(UTC) + timedelta(seconds=seconds)


@dataclass
class Handler:
    run: Callable[[Job], None]
    attempts: int
    backoff: tuple[int, ...]
    # called once when the job fails for good, with the reason
    failed: Callable[[Job, str], None] | None = None


class Jobs:
    def __init__(self):
        self.handlers: dict[str, Handler] = {}
        # run jobs in the thread that enqueued them, right away (tests)
        self.inline = False
        # sandbox id -> job id, for every job whose thread is still running
        self.live: dict[str, str] = {}
        self.lock = threading.Lock()
        self.closing = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.event: asyncio.Event | None = None

    def register(
        self,
        kind: str,
        run: Callable[[Job], None],
        attempts: int = 3,
        backoff: tuple[int, ...] = (5, 15, 30),
        failed=None,
    ):
        self.handlers[kind] = Handler(run, attempts, backoff, failed)

    def enqueue(self, db: Querier, sandbox_id: str, kind: str, deadline_seconds: int | None = None, **args) -> Job:
        """Queues a job inside the caller's transaction; call `kick()` once it has committed."""
        return db.create_job(
            CreateJobParams(
                id=str(uuid.uuid4()),
                sandbox_id=sandbox_id,
                kind=kind,
                args=json.dumps(args),
                max_attempts=self.handlers[kind].attempts,
                deadline=stamp(deadline_seconds) if deadline_seconds else None,
            )
        )

    def kick(self):
        if self.inline:
            self.drain()
        elif self.loop is not None and self.event is not None:
            self.loop.call_soon_threadsafe(self.event.set)

    def owns(self, job: Job) -> bool:
        """Whether `job` is still the running job; a handler checks this before committing a result."""
        with db_manager.session() as db:
            current = db.get_job(id=job.id)
        return current is not None and current.state == "running"

    # Running jobs

    def drain(self):
        """Runs every queued job to completion in this thread, retries included, ignoring backoff."""
        while True:
            with db_manager.session() as db:
                queued = [j for j in db.list_jobs_by_state(state="queued") if j.sandbox_id not in self.live]
            if not queued:
                return
            for job in queued:
                claimed = self.claim(job)
                if claimed is not None:
                    self.execute(claimed)

    def claim(self, job: Job) -> Job | None:
        with self.lock:
            if job.sandbox_id in self.live:
                return None
            with db_manager.session() as db:
                claimed = db.claim_job(id=job.id)
            if claimed is not None:
                self.live[claimed.sandbox_id] = claimed.id
            return claimed

    def execute(self, job: Job):
        handler = self.handlers[job.kind]
        started = time.monotonic()
        waited = (datetime.now(UTC) - job.run_after).total_seconds()
        metrics.job_wait.record(max(waited, 0.0), {"kind": job.kind})
        outcome = "succeeded"
        try:
            handler.run(job)
        except Wait as e:
            outcome = "waiting"
            self.defer(job, str(e), e.window)
        except Exception as e:
            outcome = "failed"
            metrics.error("job", kind=job.kind)
            self.retry_or_fail(job, handler, str(e) or e.__class__.__name__)
        else:
            with self.lock, db_manager.session() as db:
                db.finish_job(state="succeeded", last_error=None, id=job.id)
        finally:
            metrics.job_duration.record(time.monotonic() - started, {"kind": job.kind, "outcome": outcome})
            with self.lock:
                if self.live.get(job.sandbox_id) == job.id:
                    del self.live[job.sandbox_id]
            if not self.inline:
                self.kick()

    def retry_or_fail(self, job: Job, handler: Handler, error: str):
        delay = handler.backoff[min(job.attempts - 1, len(handler.backoff) - 1)]
        with self.lock, db_manager.session() as db:
            current = db.get_job(id=job.id)
            if current is None or current.state != "running":
                return
            if current.attempts < current.max_attempts and (
                current.deadline is None or stamp(delay) < current.deadline
            ):
                logger.warning("job failed, retrying", extra={"job_id": job.id, "kind": job.kind, "error": error})
                db.retry_job(last_error=error, run_after=stamp(delay), id=job.id)
                return
            db.finish_job(state="failed", last_error=error, id=job.id)
        self.give_up(job, error)

    def defer(self, job: Job, reason: str, window: int):
        with self.lock, db_manager.session() as db:
            current = db.get_job(id=job.id)
            if current is None or current.state != "running":
                return
            if current.created_at >= stamp(-MAX_WAIT):
                logger.info("job waiting", extra={"job_id": job.id, "kind": job.kind, "reason": reason})
                db.defer_job(
                    last_error=WAITING + reason,
                    run_after=stamp(WAIT_POLL),
                    deadline=stamp(WAIT_POLL + window),
                    id=job.id,
                )
                return
            reason = f"waited {MAX_WAIT // 3600} hours: {reason}"
            db.finish_job(state="failed", last_error=reason, id=job.id)
        self.give_up(job, reason)

    def give_up(self, job: Job, error: str):
        logger.error(
            "job failed", extra={"job_id": job.id, "kind": job.kind, "sandbox_id": job.sandbox_id, "error": error}
        )
        handler = self.handlers[job.kind]
        if handler.failed is not None:
            try:
                handler.failed(job, error)
            except Exception as e:
                logger.error("job failure handler failed", extra={"job_id": job.id, "error": repr(e)})

    def expire(self):
        """Fails jobs that passed their deadline. A thread still working on one is left to notice it lost the job."""
        now = stamp()
        expired = []
        with self.lock, db_manager.session() as db:
            for state in ("running", "queued"):
                for job in list(db.list_jobs_by_state(state=state)):
                    if job.deadline is not None and job.deadline < now:
                        minutes = round((job.deadline - job.created_at).total_seconds() / 60)
                        reason = f"timed out after {minutes} minutes" + (
                            f": {job.last_error}" if job.last_error else ""
                        )
                        db.finish_job(state="failed", last_error=reason, id=job.id)
                        expired.append((job, reason))
        for job, reason in expired:
            self.give_up(job, reason)

    def recover(self):
        """Requeues jobs a previous process was running when it stopped."""
        with db_manager.session() as db:
            for job in list(db.list_jobs_by_state(state="running")):
                db.requeue_job(last_error="interrupted by a restart", id=job.id)
                logger.info("job resumed", extra={"job_id": job.id, "kind": job.kind, "sandbox_id": job.sandbox_id})

    async def run(self):
        if self.inline:
            return
        self.loop = asyncio.get_running_loop()
        self.event = asyncio.Event()
        await asyncio.to_thread(self.recover)
        while not self.closing:
            await asyncio.to_thread(self.expire)
            with db_manager.session() as db:
                due = list(db.list_due_jobs())
            for job in due:
                if self.closing or len(self.live) >= PARALLEL:
                    break
                claimed = self.claim(job)
                if claimed is not None:
                    self.loop.run_in_executor(None, self.execute, claimed)
            self.event.clear()
            try:
                await asyncio.wait_for(self.event.wait(), POLL_SECONDS)
            except TimeoutError:
                pass

    async def shutdown(self, timeout: float = 20):
        """Stops taking jobs, waits for running ones, and requeues whatever is still running so the next start resumes it."""
        self.closing = True
        if self.event is not None:
            self.event.set()
        deadline = time.monotonic() + timeout
        while self.live and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        with self.lock, db_manager.session() as db:
            for sandbox_id, job_id in list(self.live.items()):
                db.requeue_job(last_error="interrupted by a shutdown", id=job_id)
                logger.info("job left for the next start", extra={"job_id": job_id, "sandbox_id": sandbox_id})
