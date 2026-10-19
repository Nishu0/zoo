"""Scheduled backups to object storage, and restoring the database from one.

Every ZOO_BACKUP_INTERVAL_HOURS (24 by default; 0 turns scheduled backups off) one worker, holding the `backups`
lease:
  1. dumps the database with pg_dump (custom format) to backups/db/<time>.dump,
  2. snapshots the home of every Linux sandbox that is running or stopped (server/docker.py snapshot, also in object
     storage), named "Scheduled <time>",
  3. keeps the newest ZOO_BACKUP_KEEP (7) dumps and scheduled snapshots per sandbox, and removes the rest.
When a backup is due is read from the newest dump in the store, so restarts don't reset the schedule.

POST /admin/backups runs one now. Restore the database with

    python -m server.backups restore backups/db/<time>.dump

which replaces the database's contents with the dump's (pg_restore --clean); stop the API and workers first. Sandbox
homes come back through each sandbox's snapshot restore. `python -m server.backups run` takes one backup."""

import asyncio
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

from apps.server import audit_api, objects, workers
from apps.server.logger import logger
from apps.server.runtime import remove_snapshot
from db.connection import db_manager
from db.generated.query import CreateSnapshotParams

INTERVAL = timedelta(hours=float(os.environ.get("ZOO_BACKUP_INTERVAL_HOURS", "24")))
KEEP = int(os.environ.get("ZOO_BACKUP_KEEP", "7"))
PREFIX = "backups/db/"
SCHEDULED = "Scheduled "
# how often the worker looks whether a backup is due
CHECK_SECONDS = 300


def dumps() -> list[tuple[str, int]]:
    """The database dumps in the store, newest first, with their sizes."""
    return sorted(objects.listing(PREFIX), reverse=True)


def dump_database() -> str:
    """Dumps the database into object storage; returns the dump's key."""
    key = f"{PREFIX}{datetime.now(UTC):%Y%m%dT%H%M%SZ}.dump"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "zoo.dump")
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", f"--file={path}", db_manager.url],
            check=True,
            capture_output=True,
        )
        objects.put_file(key, path)
    return key


def snapshot_homes(sandboxes) -> int:
    """Queues a scheduled snapshot of every Linux sandbox that is running or stopped and not busy; returns how many."""
    queued = 0
    stamp = f"{SCHEDULED}{datetime.now(UTC):%Y-%m-%d %H:%M}"
    with db_manager.session() as db:
        for sandbox in db.list_all_sandboxes():
            if sandbox.kind in ("macos", "windows") or sandbox.status not in ("running", "stopped"):
                continue
            if db.get_active_job(sandbox_id=sandbox.id) is not None:
                continue
            row = db.create_snapshot(
                CreateSnapshotParams(
                    id=str(uuid.uuid4()),
                    sandbox_id=sandbox.id,
                    server_id=sandbox.server_id,
                    name=stamp,
                    created_by=sandbox.created_by,
                )
            )
            assert row is not None
            sandboxes.jobs.enqueue(db, sandbox.id, "snapshot", snapshot_id=row.id)
            audit_api.system(sandbox.workspace_id, "snapshot (scheduled)", "sandboxes", sandbox.id, snapshot=row.id)
            queued += 1
    sandboxes.jobs.kick()
    return queued


def prune(sandboxes):
    """Keeps the newest KEEP dumps, and the newest KEEP scheduled snapshots of each sandbox."""
    for key, _ in dumps()[KEEP:]:
        objects.delete(key)
    with db_manager.session() as db:
        stale = []
        for sandbox in db.list_all_sandboxes():
            scheduled = [
                s
                for s in db.list_snapshots_by_sandbox(sandbox_id=sandbox.id)
                if s.name.startswith(SCHEDULED) and s.state == "ready"
            ]
            stale += [(sandbox, s.id) for s in scheduled[KEEP:]]
    for sandbox, snapshot_id in stale:
        try:
            with db_manager.session() as db:
                server = sandboxes.server_of(sandbox, db)
            remove_snapshot(sandbox, snapshot_id, server)
            with db_manager.session() as db:
                db.delete_snapshot(id=snapshot_id)
        except Exception as e:
            logger.error("pruning a snapshot failed", extra={"snapshot_id": snapshot_id, "error": repr(e)})


def run(sandboxes) -> str:
    """One backup: the database, then every sandbox's home, then pruning. Returns the dump's key."""
    key = dump_database()
    queued = snapshot_homes(sandboxes)
    prune(sandboxes)
    logger.info("backup taken", extra={"key": key, "snapshots": queued})
    return key


def taken_at(key: str) -> datetime:
    """When a dump was taken, from its key."""
    return datetime.strptime(key.removeprefix(PREFIX), "%Y%m%dT%H%M%SZ.dump").replace(tzinfo=UTC)


def due() -> bool:
    newest = dumps()[:1]
    return not newest or datetime.now(UTC) - taken_at(newest[0][0]) >= INTERVAL


async def loop(sandboxes):
    """Takes a backup whenever one is due, on the one worker holding the lease."""
    if INTERVAL <= timedelta(0):
        return
    while True:
        try:
            if await asyncio.to_thread(workers.hold, "backups", CHECK_SECONDS * 3) and await asyncio.to_thread(due):
                await asyncio.to_thread(run, sandboxes)
        except Exception as e:
            logger.error("scheduled backup failed", extra={"error": repr(e)})
        await asyncio.sleep(CHECK_SECONDS)


def restore(key: str):
    """Replaces the database's contents with a dump from object storage."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "zoo.dump")
        objects.get_file(key, path)
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--single-transaction",
                "--no-owner",
                f"--dbname={db_manager.url}",
                path,
            ],
            check=True,
        )


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    objects.require()
    db_manager.init_db()
    if sys.argv[1:2] == ["restore"] and len(sys.argv) == 3:
        restore(sys.argv[2])
    elif sys.argv[1:] == ["run"]:
        # the database only: sandbox snapshots are jobs, which a running worker takes
        print(dump_database())
    else:
        sys.exit("usage: python -m server.backups run | restore <key>")
