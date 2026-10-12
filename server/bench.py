"""Benchmarks: run a task suite against models, each task in a fresh sandbox, in parallel, and score the results.

A suite (YAML or JSON):

    name: office basics
    tasks:
      - id: save-a-file
        instruction: Open the text editor, type hello and save it as ~/hello.txt
        kind: desktop                 # the sandbox kind (desktop by default)
        setup:
          snapshot_id: ...            # start from this snapshot (Linux kinds; a snapshot in the workspace)
          commands: ["rm -f ~/hello.txt"]
        check:
          command: grep -q hello ~/hello.txt   # passes on exit code expect_exit_code (0)
        timeout: 600                  # seconds for the agent (default 600)
      - id: find-the-setting
        instruction: ...
        check:
          judge: Is the dark theme switched on?  # a model looks at the final screen and answers PASS or FAIL

POST /benchmarks queues a suite with models ([{provider, model}]) and `parallel`; a worker claims it and, for each
task and model, makes a sandbox labelled benchmark=<id>, sets it up, runs the agent with that model, checks the
result and deletes the sandbox. Workspace quotas still apply: a sandbox that doesn't fit waits for room. The judge is
the workspace's agent model. A benchmark whose worker goes away fails; it isn't resumed.

`python -m server.bench suite.yaml --models anthropic:claude-sonnet-5-5,openai:computer-use-preview` runs one
through the API (ZOO_URL, ZOO_API_KEY) and prints the scores."""

import asyncio
import csv
import io
import json
import os
import sys
import time
import uuid
from typing import Literal

import httpx
import litellm
import yaml
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from db.connection import db_manager
from db.generated.models import Benchmark, BenchmarkResult
from db.generated.query import CreateBenchmarkParams, Querier, UpsertBenchmarkResultParams
from logger.logger import logger
from server import labels, workers
from server.agent_api import FINISHED, PROVIDERS, AgentApi, ProviderId
from server.auth_api import AuthApi, Member
from server.jobs import stamp
from server.sandbox_api import VMS, CreateSandboxRequest, SandboxApi, boot_deadline
from utils.time import to_stamp

HEARTBEAT_SECONDS = 5
STALE_SECONDS = 60
POLL_SECONDS = 5
WAIT_SECONDS = 2
# how long a sandbox that doesn't fit the quota waits for room
ROOM_SECONDS = 3600
MAX_TASKS = 500
MAX_PARALLEL = 50
JUDGE = (
    "You check whether a computer-use agent finished its task. Look at the final screen and answer with PASS or "
    "FAIL on the first line, then one sentence on why.\n\nThe task was: {instruction}\n\nThe check: {question}"
)


class Setup(BaseModel):
    snapshot_id: str | None = None
    commands: list[str] = []


class Check(BaseModel):
    command: str | None = None
    expect_exit_code: int = 0
    judge: str | None = None

    @model_validator(mode="after")
    def one_kind(self) -> "Check":
        if (self.command is None) == (self.judge is None):
            raise ValueError("a check has either a command or a judge question")
        return self


class Task(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=20000)
    kind: Literal["desktop", "browser", "macos", "windows"] = "desktop"
    setup: Setup = Setup()
    check: Check
    timeout: int = Field(default=600, ge=30, le=6 * 3600)


class Suite(BaseModel):
    name: str = Field(default="benchmark", max_length=100)
    tasks: list[Task] = Field(min_length=1, max_length=MAX_TASKS)

    @field_validator("tasks")
    @classmethod
    def unique(cls, tasks: list[Task]) -> list[Task]:
        ids = [t.id for t in tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task ids must be unique")
        for task in tasks:
            if task.setup.snapshot_id and task.kind in VMS:
                raise ValueError(f"task {task.id}: snapshots set up Linux sandboxes only")
        return tasks


class ModelChoice(BaseModel):
    provider: ProviderId
    # the provider's model name; its default when null
    model: str | None = Field(default=None, max_length=200)

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model or PROVIDERS[self.provider].default_model}"


class BenchmarkRequest(BaseModel):
    # the suite as YAML or JSON text, or already parsed
    suite: str | dict
    models: list[ModelChoice] = Field(min_length=1, max_length=20)
    parallel: int = Field(default=2, ge=1, le=MAX_PARALLEL)


class ResultResponse(BaseModel):
    task_id: str
    model: str
    passed: bool | None
    score: float | None
    steps: int
    tokens: int
    cost: float
    seconds: float
    run_id: str | None
    error: str | None


class ModelSummary(BaseModel):
    model: str
    tasks: int
    passed: int
    pass_rate: float
    mean_steps: float
    mean_cost: float
    mean_seconds: float


class BenchmarkResponse(BaseModel):
    id: str
    name: str
    state: str
    parallel: int
    tasks: int
    models: list[str]
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class BenchmarkDetailResponse(BenchmarkResponse):
    summary: list[ModelSummary]
    results: list[ResultResponse]


def parse_suite(suite: str | dict) -> Suite:
    try:
        data = yaml.safe_load(suite) if isinstance(suite, str) else suite
        return Suite.model_validate(data)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"the suite isn't valid YAML or JSON: {e}")
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"the suite is invalid: {e}")


def benchmark_response(row: Benchmark) -> BenchmarkResponse:
    suite = Suite.model_validate_json(row.suite)
    return BenchmarkResponse(
        id=row.id,
        name=row.name,
        state=row.state,
        parallel=row.parallel,
        tasks=len(suite.tasks),
        models=[ModelChoice.model_validate(m).label for m in json.loads(row.models)],
        error=row.error,
        created_at=to_stamp(row.created_at),
        started_at=to_stamp(row.started_at),
        finished_at=to_stamp(row.finished_at),
    )


def result_response(row: BenchmarkResult) -> ResultResponse:
    return ResultResponse(**{k: getattr(row, k) for k in ResultResponse.model_fields})


def summarize(results: list[BenchmarkResult]) -> list[ModelSummary]:
    by_model: dict[str, list[BenchmarkResult]] = {}
    for row in results:
        by_model.setdefault(row.model, []).append(row)
    summary = []
    for model, rows in sorted(by_model.items()):
        passed = sum(1 for r in rows if r.passed)
        summary.append(
            ModelSummary(
                model=model,
                tasks=len(rows),
                passed=passed,
                pass_rate=round(passed / len(rows), 4),
                mean_steps=round(sum(r.steps for r in rows) / len(rows), 2),
                mean_cost=round(sum(r.cost for r in rows) / len(rows), 6),
                mean_seconds=round(sum(r.seconds for r in rows) / len(rows), 1),
            )
        )
    return summary


class TaskFailed(Exception):
    pass


class Bench:
    """Runs claimed benchmarks in this worker process."""

    def __init__(self, sandboxes: SandboxApi, agents: AgentApi):
        self.sandboxes = sandboxes
        self.agents = agents
        self.running: dict[str, asyncio.Task] = {}

    async def loop(self):
        while True:
            try:
                await asyncio.to_thread(self.fail_stale)
                if not self.agents.closing:
                    with db_manager.session() as db:
                        row = db.claim_benchmark(worker=workers.WORKER_ID)
                    if row is not None:
                        self.running[row.id] = asyncio.create_task(self.run(row))
            except Exception as e:
                logger.error("benchmarks failed", extra={"error": repr(e)})
            await asyncio.sleep(POLL_SECONDS)

    async def shutdown(self):
        """Stops the benchmarks running here; they end cancelled, and their sandboxes are removed."""
        tasks = list(self.running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def fail_stale(self):
        with db_manager.session() as db:
            stale = list(db.fail_stale_benchmarks(heartbeat_at=stamp(-STALE_SECONDS)))
        for benchmark_id in stale:
            logger.warning("benchmark's worker went away", extra={"benchmark_id": benchmark_id})
            self.clean_up(benchmark_id)

    def clean_up(self, benchmark_id: str):
        """Deletes the benchmark's sandboxes that are still there."""
        with db_manager.session() as db:
            benchmark = db.get_benchmark(id=benchmark_id)
            if benchmark is None:
                return
            labelled = labels.of_workspace(db, benchmark.workspace_id)
            for sandbox_id, found in labelled.items():
                if found.get("benchmark") == benchmark_id:
                    self.delete(db, sandbox_id)
        self.sandboxes.jobs.kick()

    def delete(self, db: Querier, sandbox_id: str):
        sandbox = db.get_sandbox(id=sandbox_id)
        if sandbox is None or sandbox.status in ("deleting", "deleted"):
            return
        db.cancel_sandbox_jobs(sandbox_id=sandbox_id)
        db.update_sandbox_status(status="deleting", id=sandbox_id)
        self.sandboxes.jobs.enqueue(db, sandbox_id, "delete")

    async def run(self, row: Benchmark):
        state, error = "succeeded", None
        work = None
        try:
            suite = Suite.model_validate_json(row.suite)
            models = [ModelChoice.model_validate(m) for m in json.loads(row.models)]
            with db_manager.session() as db:
                person = db.get_user(id=row.created_by)
            if person is None:
                raise RuntimeError("the user who started the benchmark is gone")
            user = Member(**person.model_dump(), workspace_id=row.workspace_id, role="member")
            gate = asyncio.Semaphore(row.parallel)

            async def one(task: Task, model: ModelChoice):
                async with gate:
                    await self.attempt(row.id, user, task, model)

            work = asyncio.gather(*(one(task, model) for task in suite.tasks for model in models))
            while not work.done():
                await asyncio.wait([work], timeout=HEARTBEAT_SECONDS)
                with db_manager.session() as db:
                    cancel = db.heartbeat_benchmark(id=row.id, worker=workers.WORKER_ID)
                if cancel is None or cancel:
                    work.cancel()
                    state, error = "cancelled", "stopped" if cancel else "taken over by another worker"
                    break
            if state == "succeeded":
                await work
        except asyncio.CancelledError:
            state, error = "cancelled", "the worker shut down"
        except Exception as e:
            logger.error("benchmark failed", extra={"benchmark_id": row.id, "error": repr(e)})
            state, error = "failed", str(e)[:1000] or e.__class__.__name__
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            with db_manager.session() as db:
                db.finish_benchmark(state=state, error=error, id=row.id)
            await asyncio.to_thread(self.clean_up, row.id)
            self.running.pop(row.id, None)

    async def attempt(self, benchmark_id: str, user: Member, task: Task, model: ModelChoice):
        """One task with one model, recorded whatever happens."""
        began = time.monotonic()
        sandbox_id = run_id = None
        passed, error = None, None
        try:
            sandbox_id = await self.make_sandbox(benchmark_id, user, task)
            for command in task.setup.commands:
                result = await self.exec(user, sandbox_id, command)
                if result["exit_code"] != 0:
                    raise TaskFailed(f"setup `{command}` exited {result['exit_code']}: {result['stderr'][-300:]}")
            run_id = await self.run_agent(user, sandbox_id, task, model)
            passed = await self.check(user, sandbox_id, task)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error = str(getattr(e, "detail", "") or e)[:1000] or e.__class__.__name__
        finally:
            steps, tokens, cost = 0, 0, 0.0
            if run_id is not None:
                with db_manager.session() as db:
                    run = db.get_agent_run(id=run_id)
                if run is not None:
                    steps, tokens, cost = run.steps, run.tokens, run.cost
            with db_manager.session() as db:
                db.upsert_benchmark_result(
                    UpsertBenchmarkResultParams(
                        benchmark_id=benchmark_id,
                        task_id=task.id,
                        model=model.label,
                        run_id=run_id,
                        sandbox_id=sandbox_id,
                        passed=passed,
                        score=None if passed is None else float(passed),
                        steps=steps,
                        tokens=tokens,
                        cost=cost,
                        seconds=round(time.monotonic() - began, 1),
                        error=error,
                    )
                )
                if sandbox_id is not None:
                    self.delete(db, sandbox_id)
            self.sandboxes.jobs.kick()

    async def make_sandbox(self, benchmark_id: str, user: Member, task: Task) -> str:
        """A running sandbox for the task, from its snapshot when it has one, waiting for quota room."""
        request = CreateSandboxRequest(
            name=f"bench-{benchmark_id[:8]}-{task.id}"[:100], kind=task.kind, labels={"benchmark": benchmark_id}
        )
        deadline = time.monotonic() + ROOM_SECONDS
        while True:
            try:
                with db_manager.session() as db:
                    sandbox = self.sandboxes.create(request, user, db)
                break
            except HTTPException as e:
                if e.status_code != 409 or time.monotonic() > deadline:
                    raise
                await asyncio.sleep(15)
        self.sandboxes.jobs.kick()
        await self.until(sandbox.id, "running", boot_deadline(task.kind))
        if task.setup.snapshot_id:
            with db_manager.session() as db:
                snapshot = db.get_snapshot(id=task.setup.snapshot_id)
                source = db.get_sandbox(id=snapshot.sandbox_id) if snapshot is not None else None
                if snapshot is None or source is None or source.workspace_id != user.workspace_id:
                    raise TaskFailed("the setup snapshot isn't in this workspace")
                if snapshot.state != "ready":
                    raise TaskFailed(f"the setup snapshot is {snapshot.state}")
                self.sandboxes.jobs.enqueue(db, sandbox.id, "stop")
            self.sandboxes.jobs.kick()
            await self.until(sandbox.id, "stopped", 300)
            with db_manager.session() as db:
                self.sandboxes.jobs.enqueue(db, sandbox.id, "restore", snapshot_id=snapshot.id)
            self.sandboxes.jobs.kick()
            await self.until(sandbox.id, "stopped", 1800)
            with db_manager.session() as db:
                db.update_sandbox_status(status="provisioning", id=sandbox.id)
                self.sandboxes.jobs.enqueue(db, sandbox.id, "boot", boot_deadline(task.kind))
            self.sandboxes.jobs.kick()
            await self.until(sandbox.id, "running", boot_deadline(task.kind))
        return sandbox.id

    async def until(self, sandbox_id: str, status: str, timeout: float):
        """Waits until the sandbox has `status` with no job in progress."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with db_manager.session() as db:
                sandbox = db.get_sandbox(id=sandbox_id)
                busy = db.get_active_job(sandbox_id=sandbox_id) is not None
            if sandbox is None or sandbox.status in ("failed", "deleting", "deleted"):
                raise TaskFailed(
                    f"the sandbox is {sandbox.status if sandbox else 'gone'}: {sandbox and sandbox.error_message}"
                )
            if sandbox.status == status and not busy:
                return
            await asyncio.sleep(WAIT_SECONDS)
        raise TaskFailed(f"the sandbox wasn't {status} within {timeout:g}s")

    async def exec(self, user: Member, sandbox_id: str, command: str) -> dict:
        return await self.sandboxes.run_tool(
            user, sandbox_id, "execute_command", {"command": command, "timeout": 300}, "bench"
        )

    async def run_agent(self, user: Member, sandbox_id: str, task: Task, model: ModelChoice) -> str:
        run = self.agents.start(user, sandbox_id, task.instruction, "bench", model.model, model.provider)
        deadline = time.monotonic() + task.timeout
        while True:
            with db_manager.session() as db:
                row = db.get_agent_run(id=run.id)
            if row is None or row.state in FINISHED:
                return run.id
            if time.monotonic() > deadline:
                self.agents.stop(sandbox_id)
                raise TaskFailed(f"the agent didn't finish within {task.timeout}s")
            await asyncio.sleep(WAIT_SECONDS)

    async def check(self, user: Member, sandbox_id: str, task: Task) -> bool:
        if task.check.command is not None:
            result = await self.exec(user, sandbox_id, task.check.command)
            return result["exit_code"] == task.check.expect_exit_code
        assert task.check.judge is not None
        screen = await self.sandboxes.run_tool(user, sandbox_id, "screenshot", {}, "bench")
        with db_manager.session() as db:
            config = self.agents.config(user.workspace_id, db)
        response = await litellm.acompletion(
            model=config.model,
            api_key=config.api_key,
            api_base=config.api_base,
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": JUDGE.format(instruction=task.instruction, question=task.check.judge)},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screen}"}},
                    ],
                }
            ],
        )
        answer = (response.choices[0].message.content or "").strip().upper()  # type: ignore[union-attr]
        if not answer.startswith(("PASS", "FAIL")):
            raise TaskFailed(f"the judge answered neither PASS nor FAIL: {answer[:200]}")
        return answer.startswith("PASS")


class BenchmarksApi:
    def __init__(self, app: FastAPI, auth: AuthApi):
        self.app = app
        self.auth = auth
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.post("/benchmarks", response_model=BenchmarkResponse, status_code=202)
        def create_benchmark(
            payload: BenchmarkRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> BenchmarkResponse:
            if user.key_sandbox_id is not None or user.key_labels is not None:
                raise HTTPException(status_code=403, detail="benchmarks make their own sandboxes; use a workspace key")
            suite = parse_suite(payload.suite)
            for task in suite.tasks:
                if task.setup.snapshot_id:
                    snapshot = db.get_snapshot(id=task.setup.snapshot_id)
                    source = db.get_sandbox(id=snapshot.sandbox_id) if snapshot is not None else None
                    if source is None or source.workspace_id != user.workspace_id:
                        raise HTTPException(status_code=404, detail=f"task {task.id}: snapshot not found")
            row = db.create_benchmark(
                CreateBenchmarkParams(
                    id=str(uuid.uuid4()),
                    workspace_id=user.workspace_id,
                    created_by=user.id,
                    name=suite.name,
                    suite=suite.model_dump_json(),
                    models=json.dumps([m.model_dump() for m in payload.models]),
                    parallel=payload.parallel,
                )
            )
            assert row is not None
            return benchmark_response(row)

        @self.app.get("/benchmarks", response_model=list[BenchmarkResponse])
        def list_benchmarks(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[BenchmarkResponse]:
            return [benchmark_response(row) for row in db.list_benchmarks(workspace_id=user.workspace_id)]

        @self.app.get("/benchmarks/{benchmark_id}", response_model=BenchmarkDetailResponse)
        def get_benchmark(
            benchmark_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> BenchmarkDetailResponse:
            row = self.owned(benchmark_id, user, db)
            results = list(db.list_benchmark_results(benchmark_id=row.id))
            return BenchmarkDetailResponse(
                **benchmark_response(row).model_dump(),
                summary=summarize(results),
                results=[result_response(r) for r in results],
            )

        @self.app.post("/benchmarks/{benchmark_id}/cancel", status_code=204)
        def cancel_benchmark(
            benchmark_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ):
            row = self.owned(benchmark_id, user, db)
            if row.state == "queued":
                db.finish_benchmark(state="cancelled", error="stopped", id=row.id)
            else:
                db.request_benchmark_cancel(id=row.id)

        @self.app.get("/benchmarks/{benchmark_id}/export")
        def export_benchmark(
            benchmark_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> StreamingResponse:
            row = self.owned(benchmark_id, user, db)
            out = io.StringIO()
            writer = csv.writer(out)
            fields = list(ResultResponse.model_fields)
            writer.writerow(fields)
            for result in db.list_benchmark_results(benchmark_id=row.id):
                writer.writerow([getattr(result, f) for f in fields])
            return StreamingResponse(
                iter([out.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="zoo-benchmark-{row.id[:8]}.csv"'},
            )

    def owned(self, benchmark_id: str, user: Member, db: Querier) -> Benchmark:
        row = db.get_benchmark(id=benchmark_id)
        if row is None or row.workspace_id != user.workspace_id:
            raise HTTPException(status_code=404, detail="benchmark not found")
        return row


def main(argv: list[str]) -> int:
    """python -m server.bench SUITE --models provider:model[,provider:model...] [--parallel N]"""
    if len(argv) < 3 or argv[1] != "--models":
        print(main.__doc__, file=sys.stderr)
        return 2
    parallel = int(argv[argv.index("--parallel") + 1]) if "--parallel" in argv else 2
    models = []
    for item in argv[2].split(","):
        provider, _, model = item.partition(":")
        models.append({"provider": provider, "model": model or None})
    with open(argv[0]) as f:
        suite = f.read()
    base = os.environ.get("ZOO_URL", "http://localhost:8000").rstrip("/")
    client = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {os.environ['ZOO_API_KEY']}"}, timeout=60)
    response = client.post("/benchmarks", json={"suite": suite, "models": models, "parallel": parallel})
    if response.is_error:
        print(f"{response.status_code}: {response.text}", file=sys.stderr)
        return 1
    benchmark_id = response.json()["id"]
    print(f"benchmark {benchmark_id} queued")
    while True:
        detail = client.get(f"/benchmarks/{benchmark_id}").json()
        done = len(detail["results"])
        print(f"\r{detail['state']}: {done}/{detail['tasks'] * len(detail['models'])} done", end="", flush=True)
        if detail["state"] not in ("queued", "running"):
            break
        time.sleep(10)
    print()
    for row in detail["summary"]:
        print(
            f"{row['model']:<50} {row['passed']}/{row['tasks']} passed ({row['pass_rate']:.0%}), "
            f"{row['mean_steps']:g} steps, ${row['mean_cost']:.4f}, {row['mean_seconds']:g}s per task"
        )
    if detail["error"]:
        print(f"error: {detail['error']}", file=sys.stderr)
    return 0 if detail["state"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
