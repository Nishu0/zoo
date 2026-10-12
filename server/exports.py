"""Trajectory exports: agent runs as training and evaluation data.

POST /agent/exports picks finished runs (one run, or a sandbox's, a label's or a time range's, at most MAX_RUNS) and
queues an export; a worker builds a .tar.gz in object storage (exports/<id>.tar.gz) holding:

- trajectory.jsonl: one line per step: {run_id, sandbox_id, step, timestamp, instruction, screenshot, a11y_tree,
  action, result, model, provider}, where screenshot and a11y_tree are paths in the archive (or null) and result is
  what the agent said after the action (its reasoning, text and errors up to the next action)
- runs.jsonl: one line per run: its instruction, state, error, steps, tokens, cost and model
- frames/<run id>/<step>.png and <step>.a11y.json: the screen and accessibility tree the step was decided on

Accessibility trees are captured while the agent runs, when the workspace has capture_a11y on (the default). GET
/agent/exports/{id}/download streams the archive through the API. Exports are removed after
ZOO_EXPORT_RETENTION_DAYS (7 by default)."""

import asyncio
import io
import json
import os
import tarfile
import tempfile
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from db.connection import db_manager
from db.generated.models import AgentMessage, AgentRun, Export
from db.generated.query import FinishExportParams, ListExportableRunsParams, Querier
from logger.logger import logger
from server import labels, objects, workers
from server.auth_api import AuthApi, Member
from server.jobs import stamp
from server.security import decrypt_bytes
from utils.time import to_stamp

MAX_RUNS = 1000
RETENTION = timedelta(days=float(os.environ.get("ZOO_EXPORT_RETENTION_DAYS", "7")))
# an export whose worker went quiet this long is picked up again
STALE_SECONDS = 1800
POLL_SECONDS = 5
PURGE_SECONDS = 3600
EXTENSIONS = {b"\x89PNG": ".png", b"\xff\xd8": ".jpg"}


def archive_key(export_id: str) -> str:
    return f"exports/{export_id}.tar.gz"


class ExportRequest(BaseModel):
    # one run; or every finished run matching the rest
    run_id: str | None = None
    sandbox_id: str | None = None
    labels: dict[str, str] | None = None
    since: datetime | None = Field(default=None, alias="from")
    until: datetime | None = Field(default=None, alias="to")

    model_config = {"populate_by_name": True}

    @field_validator("labels")
    @classmethod
    def check_labels(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return labels.validate(value) if value else None


class ExportResponse(BaseModel):
    id: str
    state: str
    runs: int
    steps: int
    size_bytes: int
    error: str | None
    created_at: str
    finished_at: str | None


def export_response(row: Export) -> ExportResponse:
    return ExportResponse(
        id=row.id,
        state=row.state,
        runs=len(json.loads(row.run_ids)),
        steps=row.steps,
        size_bytes=row.size_bytes,
        error=row.error,
        created_at=to_stamp(row.created_at),
        finished_at=to_stamp(row.finished_at),
    )


def pick(db: Querier, user: Member, payload: ExportRequest) -> list[str]:
    """The ids of the finished runs the request names, in the user's workspace; 404/422 when there are none."""
    if payload.run_id is not None:
        run = db.get_agent_run(id=payload.run_id)
        sandbox = db.get_sandbox(id=run.sandbox_id) if run is not None else None
        if run is None or sandbox is None or sandbox.workspace_id != user.workspace_id:
            raise HTTPException(status_code=404, detail="run not found")
        if run.state not in ("succeeded", "failed", "cancelled"):
            raise HTTPException(status_code=409, detail="the run hasn't finished")
        return [run.id]
    runs = db.list_exportable_runs(
        ListExportableRunsParams(
            workspace_id=user.workspace_id,
            sandbox_id=payload.sandbox_id,
            since=payload.since,
            until=payload.until,
            row_limit=MAX_RUNS * 10,
        )
    )
    labelled = labels.of_workspace(db, user.workspace_id) if payload.labels else {}
    chosen = [
        run.id for run in runs if not payload.labels or labels.matches(labelled.get(run.sandbox_id, {}), payload.labels)
    ]
    if not chosen:
        raise HTTPException(status_code=422, detail="no finished runs match")
    if len(chosen) > MAX_RUNS:
        raise HTTPException(status_code=422, detail=f"{len(chosen)} runs match; export at most {MAX_RUNS} at once")
    return chosen


def messages_of(db: Querier, run_id: str) -> list[AgentMessage]:
    found: list[AgentMessage] = []
    while True:
        page = list(db.list_agent_run_messages(run_id=run_id, offset=len(found)))
        found += page
        if len(page) < 1000:
            return found


def stored(ref: str | None) -> bytes | None:
    """A step's screenshot or tree from object storage, decrypted; None when it has none or it was removed."""
    if not ref:
        return None
    try:
        return decrypt_bytes(objects.get(f"agent/{ref}"))
    except FileNotFoundError:
        return None


def add(archive: tarfile.TarFile, name: str, data: bytes):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def write_run(archive: tarfile.TarFile, trajectory: io.TextIOBase, run: AgentRun, messages: list[AgentMessage]) -> int:
    """Adds one run's steps to the archive and the trajectory; returns how many steps it had."""
    instruction = next((m.content for m in messages if m.kind == "user"), "")
    step = 0
    lines: list[dict] = []
    for message in messages:
        if message.kind == "action":
            step += 1
            screenshot = tree = None
            image = stored(message.screenshot)
            if image is not None:
                ext = next((e for magic, e in EXTENSIONS.items() if image.startswith(magic)), ".bin")
                screenshot = f"frames/{run.id}/{step:04d}{ext}"
                add(archive, screenshot, image)
            data = stored(message.a11y)
            if data is not None:
                tree = f"frames/{run.id}/{step:04d}.a11y.json"
                add(archive, tree, data)
            lines.append(
                {
                    "run_id": run.id,
                    "sandbox_id": run.sandbox_id,
                    "step": step,
                    "timestamp": message.created_at.isoformat(),
                    "instruction": instruction,
                    "screenshot": screenshot,
                    "a11y_tree": tree,
                    "action": message.content,
                    "result": [],
                    "model": run.model,
                    "provider": run.provider,
                }
            )
        elif lines and message.kind in ("text", "reasoning", "error"):
            lines[-1]["result"].append({"kind": message.kind, "text": message.content})
    trajectory.writelines(json.dumps(line) + "\n" for line in lines)
    return step


def build(export: Export) -> tuple[int, int]:
    """Builds the export's archive and uploads it; returns its steps and size."""
    run_ids: list[str] = json.loads(export.run_ids)
    steps = 0
    with tempfile.TemporaryDirectory() as scratch:
        path = os.path.join(scratch, "export.tar.gz")
        trajectory = io.StringIO()
        runs = io.StringIO()
        with tarfile.open(path, "w:gz") as archive:
            for run_id in run_ids:
                with db_manager.session() as db:
                    run = db.get_agent_run(id=run_id)
                    messages = messages_of(db, run_id) if run is not None else []
                if run is None:
                    continue
                count = write_run(archive, trajectory, run, messages)
                steps += count
                runs.write(
                    json.dumps(
                        {
                            "run_id": run.id,
                            "sandbox_id": run.sandbox_id,
                            "instruction": next((m.content for m in messages if m.kind == "user"), ""),
                            "state": run.state,
                            "error": run.error,
                            "steps": count,
                            "tokens": run.tokens,
                            "cost": run.cost,
                            "model": run.model,
                            "provider": run.provider,
                            "started_at": run.started_at.isoformat() if run.started_at else None,
                            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                        }
                    )
                    + "\n"
                )
            add(archive, "trajectory.jsonl", trajectory.getvalue().encode())
            add(archive, "runs.jsonl", runs.getvalue().encode())
        objects.put_file(archive_key(export.id), path)
        return steps, os.path.getsize(path)


def work_once() -> bool:
    """Builds the next queued export, if there is one; False when there was none."""
    with db_manager.session() as db:
        export = db.claim_export(worker=workers.WORKER_ID, stale_seconds=STALE_SECONDS)
    if export is None:
        return False
    try:
        steps, size = build(export)
        state, error = "succeeded", None
    except Exception as e:
        logger.error("trajectory export failed", extra={"export_id": export.id, "error": repr(e)})
        steps, size, state, error = 0, 0, "failed", str(e)[:1000] or e.__class__.__name__
    with db_manager.session() as db:
        db.finish_export(FinishExportParams(state=state, error=error, steps=steps, size_bytes=size, id=export.id))
    return True


def purge():
    with db_manager.session() as db:
        removed = list(db.purge_exports(created_at=stamp(-RETENTION.total_seconds())))
    for export_id in removed:
        objects.delete(archive_key(export_id))


async def loop():
    """Every worker builds queued exports, one at a time; the one holding the lease removes old ones hourly."""
    purged = 0.0
    while True:
        try:
            while await asyncio.to_thread(work_once):
                pass
            now = asyncio.get_running_loop().time()
            if now - purged >= PURGE_SECONDS and await asyncio.to_thread(workers.hold, "exports", PURGE_SECONDS * 2):
                purged = now
                await asyncio.to_thread(purge)
        except Exception as e:
            logger.error("trajectory exports failed", extra={"error": repr(e)})
        await asyncio.sleep(POLL_SECONDS)


class ExportsApi:
    def __init__(self, app: FastAPI, auth: AuthApi):
        self.app = app
        self.auth = auth
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.post("/agent/exports", response_model=ExportResponse, status_code=202)
        def create_export(
            payload: ExportRequest, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> ExportResponse:
            run_ids = pick(db, user, payload)
            row = db.create_export(
                id=str(uuid.uuid4()), workspace_id=user.workspace_id, created_by=user.id, run_ids=json.dumps(run_ids)
            )
            assert row is not None
            return export_response(row)

        @self.app.get("/agent/exports", response_model=list[ExportResponse])
        def list_exports(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ExportResponse]:
            return [export_response(row) for row in db.list_exports(workspace_id=user.workspace_id)]

        @self.app.get("/agent/exports/{export_id}", response_model=ExportResponse)
        def get_export(
            export_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> ExportResponse:
            return export_response(self.owned(export_id, user, db))

        @self.app.get("/agent/exports/{export_id}/download")
        def download(
            export_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> StreamingResponse:
            export = self.owned(export_id, user, db)
            if export.state != "succeeded":
                raise HTTPException(status_code=409, detail=f"the export is {export.state}")
            return StreamingResponse(
                stream(archive_key(export.id)),
                media_type="application/gzip",
                headers={
                    "Content-Disposition": f'attachment; filename="zoo-trajectories-{export.id[:8]}.tar.gz"',
                    "Content-Length": str(export.size_bytes),
                },
            )

    def owned(self, export_id: str, user: Member, db: Querier) -> Export:
        export = db.get_export(id=export_id)
        if export is None or export.workspace_id != user.workspace_id:
            raise HTTPException(status_code=404, detail="export not found")
        return export


def stream(key: str) -> Iterator[bytes]:
    """The object's bytes, streamed from object storage; 502 before anything is sent when it can't be read."""
    client = httpx.Client(timeout=60)
    response = client.send(client.build_request("GET", objects.presign("GET", key)), stream=True)
    if response.status_code != 200:
        response.close()
        client.close()
        raise HTTPException(
            status_code=502, detail=f"couldn't read the export from object storage: {response.status_code}"
        )

    def body() -> Iterator[bytes]:
        try:
            yield from response.iter_bytes(1 << 20)
        finally:
            response.close()
            client.close()

    return body()
