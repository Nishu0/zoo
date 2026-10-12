"""CUA (trycua/cua) computer-use agent bound to a sandbox, exposed over REST with SSE streaming.

Each sandbox has at most one run at a time. A run is a row in `agent_runs`: starting one queues it, and a worker
process (server/workers.py) claims it and keeps a heartbeat on it while it works. When the worker stops — a restart, a
crash, a deploy — the run is resumed: a graceful shutdown hands it back right away, and a run whose heartbeat goes
stale is picked up by the next worker. The agent continues from the conversation so far with a fresh screenshot. A
run that keeps getting interrupted fails after max_attempts.

Every run has limits on actions, wall-clock time and model tokens, from the workspace's agent settings and capped by
the server's. Each action is stored with the screenshot the agent saw before it, so a run can be replayed later.

Any number of clients (the dashboard, an API caller, a Slack, Discord or WhatsApp relay) can attach to a run's event
stream: in the process running it from memory, in any other process by following the database.
"""

import asyncio
import base64
import inspect
import io
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg

os.environ.setdefault("CUA_TELEMETRY_ENABLED", "false")

from cua_agent import ComputerAgent
from cua_agent.types import ToolError
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field

from db.connection import IntegrityError, db_manager
from db.generated.models import AgentMessage, AgentRun, Sandbox, WorkspaceAgentSetting
from db.generated.query import (
    CreateAgentChannelParams,
    CreateAgentMessageParams,
    CreateAgentRunParams,
    CreateVaultSecretParams,
    Querier,
    UpsertWorkspaceAgentSettingsParams,
)
from integrations import discord, slack, whatsapp
from logger.logger import logger
from server import metering, metrics, objects, workers
from server.auth_api import AuthApi, Member
from server.jobs import stamp
from server.registry import TOOLS
from server.sandbox_api import SandboxApi
from server.security import audit, decrypt, decrypt_bytes, encrypt, encrypt_bytes
from utils.time import to_stamp

MODEL = os.environ.get("ZOO_AGENT_MODEL", "anthropic/claude-sonnet-5-5")
# server-wide caps; a workspace can lower its limits but not raise them past these
MAX_STEPS = int(os.environ.get("ZOO_AGENT_MAX_STEPS", "100"))
MAX_SECONDS = int(os.environ.get("ZOO_AGENT_MAX_SECONDS", str(30 * 60)))
MAX_TOKENS = int(os.environ.get("ZOO_AGENT_MAX_TOKENS", "2000000"))
# how many times a run may be started, the first time included, before an interruption fails it
MAX_ATTEMPTS = 3
# runs one worker process works on at once
PARALLEL = int(os.environ.get("ZOO_AGENT_PARALLEL", "4"))
HEARTBEAT_SECONDS = 5
# a running run whose worker hasn't heartbeated for this long is resumed elsewhere
STALE_SECONDS = 30
POLL_SECONDS = 1.0
SWEEP_SECONDS = 3600
HISTORY = 40
# how often a taken-over run checks whether it has been handed back
TAKEOVER_POLL_SECONDS = 1.0
CHANNEL = "cua"
PLATFORMS = ("slack", "discord", "whatsapp")
SCREENS = {
    "desktop": "a Linux XFCE desktop",
    "browser": "a Linux desktop running Firefox",
    "macos": "a macOS desktop",
    "windows": "a Windows desktop",
}
ENVIRONMENTS = {"macos": "mac", "windows": "windows"}
BUTTONS = {"left": "left", "right": "right", "middle": "middle", "wheel": "middle"}
RESUME_NOTE = (
    "The server restarted while you were working on the task above. Take a screenshot to see where things "
    "stand, then carry on with the task. Don't repeat steps that are already done."
)
FINISHED = ("succeeded", "failed", "cancelled")


@dataclass(frozen=True)
class Provider:
    label: str
    prefix: str  # prepended to the model name to get the cua/litellm model string
    default_model: str
    env_key: str | None  # read by the provider when the workspace has not set a key of its own
    default_base: str | None = None


# Models must support computer use (or be a vision model cua has a loop for, like Qwen3-VL).
PROVIDERS = {
    "anthropic": Provider("Anthropic", "anthropic/", "claude-sonnet-5-5", "ANTHROPIC_API_KEY"),
    "openai": Provider("OpenAI", "openai/", "computer-use-preview", "OPENAI_API_KEY"),
    # cua's Gemini loop only matches bare model names and calls the Gemini API directly
    "gemini": Provider("Google Gemini", "", "gemini-2.5-computer-use-preview-10-2025", "GOOGLE_API_KEY"),
    "openrouter": Provider("OpenRouter", "openrouter/", "qwen/qwen3-vl-235b-a22b-instruct", "OPENROUTER_API_KEY"),
    "ollama": Provider("Ollama", "ollama_chat/", "qwen3-vl", None, "http://localhost:11434"),
    # cua has no loop of its own for Grok; its generic vision loop drives it, as for other vision models
    "xai": Provider("xAI Grok", "xai/", "grok-4", "XAI_API_KEY"),
}
ProviderId = Literal["anthropic", "openai", "gemini", "openrouter", "ollama", "xai"]


@dataclass(frozen=True)
class ModelConfig:
    model: str
    api_key: str | None = None
    api_base: str | None = None


@dataclass(frozen=True)
class Limits:
    steps: int
    seconds: int
    tokens: int


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    # with a provider: that provider's model name (its default when null). Without one: the workspace's provider,
    # and a full model string (anthropic/claude-...) to use instead of the workspace's model.
    model: str | None = Field(default=None, max_length=200)
    provider: ProviderId | None = None
    api_base: str | None = Field(default=None, max_length=500)
    stream: bool = True


class AgentMessageResponse(BaseModel):
    id: str
    kind: str
    content: str
    source: str
    run_id: str | None
    has_screenshot: bool
    created_at: str


class AgentRunResponse(BaseModel):
    id: str
    state: str
    source: str
    attempts: int
    steps: int
    tokens: int
    cost: float
    max_steps: int
    max_seconds: int
    max_tokens: int
    elapsed_seconds: float
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    # someone has taken the screen over (a user id, or link:<viewer link id>); the run waits for them
    paused_at: str | None = None
    paused_by: str | None = None
    provider: str | None = None
    model: str | None = None


class AgentStateResponse(BaseModel):
    running: bool
    model: str
    run: AgentRunResponse | None
    messages: list[AgentMessageResponse]


class ProviderResponse(BaseModel):
    id: str
    label: str
    default_model: str
    default_base: str | None
    needs_key: bool
    server_key: bool


class ProviderStatusResponse(ProviderResponse):
    # a run can use it now: a workspace key, a server key, or no key needed
    has_key: bool
    # the vault secret a workspace key goes in
    key_secret: str | None


class SecretRef(BaseModel):
    id: str
    name: str


class LimitsResponse(BaseModel):
    max_steps: int
    max_seconds: int
    max_tokens: int


class AgentSettingsRequest(BaseModel):
    provider: ProviderId
    model: str = Field(min_length=1, max_length=200)
    # a key typed in: saved to the vault as AGENT_<PROVIDER>_API_KEY and used from there. None keeps the current
    # key, "" removes it
    api_key: str | None = Field(default=None, max_length=500)
    # or an existing vault secret to use as the key; "" removes it
    api_key_secret_id: str | None = Field(default=None, max_length=100)
    api_base: str | None = Field(default=None, max_length=500)
    # None uses the server's cap
    max_steps: int | None = Field(default=None, ge=1)
    max_seconds: int | None = Field(default=None, ge=10)
    max_tokens: int | None = Field(default=None, ge=1000)


class AgentSettingsResponse(BaseModel):
    provider: str | None
    model: str
    has_api_key: bool
    api_key_secret: SecretRef | None
    api_base: str | None
    default_model: str
    limits: LimitsResponse
    caps: LimitsResponse
    providers: list[ProviderResponse]


class ChannelRequest(BaseModel):
    platform: Literal["slack", "discord", "whatsapp"]
    external_id: str = Field(min_length=1, max_length=100)
    # platform user IDs allowed to command the sandbox from this channel, or ["*"] for anyone in it. Required for
    # Slack and Discord; a WhatsApp link is one phone number, which is its own allowlist.
    allowed_users: list[str] = Field(default_factory=list, max_length=100)


class ChannelUpdate(BaseModel):
    allowed_users: list[str] = Field(min_length=1, max_length=100)


class ChannelResponse(BaseModel):
    id: str
    platform: str
    external_id: str
    allowed_users: list[str]
    created_at: str


class IntegrationResponse(BaseModel):
    platform: str
    configured: bool
    webhook_path: str | None


def normalize(platform: str, external_id: str) -> str:
    value = external_id.strip()
    if platform == "whatsapp":
        return "".join(c for c in value if c.isdigit())
    if platform == "slack":
        return value.upper()
    return value


def normalize_users(platform: str, users: list[str]) -> list[str]:
    """Cleans an allowlist: Slack IDs are upper case, Discord IDs are numbers, and "*" means anyone."""
    cleaned: list[str] = []
    for user in users:
        value = user.strip().lstrip("@")
        if value.startswith("<@") and value.endswith(">"):
            value = value[2:-1].lstrip("!")
        if not value:
            continue
        if value != "*":
            value = normalize(platform, value)
            if platform == "discord" and not value.isdigit():
                raise HTTPException(status_code=422, detail=f"{user!r} is not a Discord user ID")
            if platform == "slack" and not value.isalnum():
                raise HTTPException(status_code=422, detail=f"{user!r} is not a Slack user ID")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def summarize(action: dict) -> str:
    kind = action.get("type", "action")
    if kind in ("click", "double_click", "move"):
        button = action.get("button", "left")
        prefix = f"{button} click" if kind == "click" and button != "left" else kind.replace("_", " ")
        return f"{prefix} {action.get('x')}, {action.get('y')}"
    if kind == "type":
        text = action.get("text", "")
        return f'type "{text[:80]}{"…" if len(text) > 80 else ""}"'
    if kind == "keypress":
        keys = action.get("keys", [])
        return "press " + ("+".join(keys) if isinstance(keys, list) else str(keys))
    if kind == "scroll":
        return (
            f"scroll {action.get('scroll_x', 0)}, {action.get('scroll_y', 0)} at {action.get('x')}, {action.get('y')}"
        )
    if kind == "drag":
        path = action.get("path") or [{}]
        return f"drag {path[0].get('x')}, {path[0].get('y')} → {path[-1].get('x')}, {path[-1].get('y')}"
    if kind == "wait":
        return "wait"
    return kind.replace("_", " ")


def events_of(item: dict) -> list[tuple[str, str]]:
    """Turns one CUA response item into (kind, content, extra) events. Tool outputs carry screenshots and are dropped."""
    kind = item.get("type")
    if kind == "message" and item.get("role") == "assistant":
        content = item.get("content")
        text = (
            content
            if isinstance(content, str)
            else "".join(c.get("text", "") for c in content or [] if isinstance(c, dict))
        )
        return [("text", text)] if text.strip() else []
    if kind == "reasoning":
        text = "\n".join(s.get("text", "") for s in item.get("summary") or [] if isinstance(s, dict))
        return [("reasoning", text)] if text.strip() else []
    if kind == "computer_call":
        action = item.get("action") or {}
        return [("action", summarize(action))]
    if kind == "function_call":
        return [("action", f"{item.get('name')}({item.get('arguments', '')})")]
    return []


def duration(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour{'s' if seconds != 3600 else ''}"
    if seconds >= 120 and seconds % 60 == 0:
        return f"{seconds // 60} minutes"
    return f"{seconds} seconds"


def seconds_since(timestamp: datetime | None) -> float:
    if not timestamp:
        return 0.0
    return max((datetime.now(UTC) - timestamp).total_seconds(), 0.0)


def run_response(run: AgentRun) -> AgentRunResponse:
    if run.finished_at and run.started_at:
        elapsed = (run.finished_at - run.started_at).total_seconds()
    else:
        elapsed = seconds_since(run.started_at)
    return AgentRunResponse(
        id=run.id,
        state=run.state,
        source=run.source,
        attempts=run.attempts,
        steps=run.steps,
        tokens=run.tokens,
        cost=run.cost,
        max_steps=run.max_steps,
        max_seconds=run.max_seconds,
        max_tokens=run.max_tokens,
        elapsed_seconds=round(elapsed, 1),
        error=run.error,
        created_at=to_stamp(run.created_at),
        started_at=to_stamp(run.started_at),
        finished_at=to_stamp(run.finished_at),
        paused_at=to_stamp(run.paused_at),
        paused_by=run.paused_by,
        provider=run.provider,
        model=run.model,
    )


def message_response(m: AgentMessage) -> AgentMessageResponse:
    return AgentMessageResponse(
        id=m.id,
        kind=m.kind,
        content=m.content,
        source=m.source,
        run_id=m.run_id,
        has_screenshot=bool(m.screenshot),
        created_at=to_stamp(m.created_at),
    )


def event_of(m: AgentMessage) -> dict:
    """A stored message as a stream event, the same shape the running process emits."""
    event: dict[str, Any] = {"type": m.kind, "text": m.content, "id": m.id, "screenshot": bool(m.screenshot)}
    if m.kind == "user":
        event["source"] = m.source
    return event


def screen_key(ref: str) -> str:
    """Where a step's screenshot (encrypted) lives in object storage; ref is <run id>/<name>."""
    return f"agent/{ref}"


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


class ZooComputer:
    """Implements cua_agent's AsyncComputerHandler protocol on top of the sandbox tool registry, so agent actions
    go through the same permission checks and Activity log as API and MCP calls, on Linux, macOS and Windows."""

    def __init__(
        self,
        sandboxes: SandboxApi,
        user: Member,
        sandbox: Sandbox,
        run: "Run | None" = None,
        deadline: asyncio.Timeout | None = None,
    ):
        self.sandboxes = sandboxes
        self.user = user
        self.sandbox = sandbox
        # the run this computer works for, which waits while someone has taken the screen over, and its time
        # limit, which the wait doesn't count against
        self.run = run
        self.deadline = deadline
        self.size: tuple[int, int] | None = None
        # the latest screenshot the agent was given, and where it was stored once something referred to it
        self.last: bytes | None = None
        self.last_ref: str | None = None

    async def call(self, name: str, **args) -> Any:
        if self.run is not None:
            await self.wait_for_screen()
        try:
            return await self.sandboxes.run_tool(self.user, self.sandbox.id, name, args, CHANNEL)
        except HTTPException as e:
            raise ToolError(str(e.detail))

    async def wait_for_screen(self):
        """Waits while a person has the screen (a takeover), then takes a fresh look: they may have changed it."""
        assert self.run is not None
        run_id = self.run.id
        paused = await asyncio.to_thread(paused_by, run_id)
        if paused is None:
            return
        began = time.monotonic()
        self.run.emit("status", f"Paused: {paused} took over the screen. Waiting for it to be handed back.")
        while paused is not None:
            await asyncio.sleep(TAKEOVER_POLL_SECONDS)
            paused = await asyncio.to_thread(paused_by, run_id)
        deadline = self.deadline
        when = deadline.when() if deadline is not None else None
        if deadline is not None and when is not None:
            deadline.reschedule(when + time.monotonic() - began)
        self.last, self.last_ref = None, None
        self.run.emit("status", "Handed back. Carrying on from the screen as it is now.")

    async def accessibility_tree(self) -> dict | None:
        """The active window's UI elements, for the step's record (trajectory exports). Called directly, not as a
        tool, so it doesn't show in the Activity log as something the agent did. None when the window exposes none."""
        runtime_id = self.sandbox.runtime_id
        if not runtime_id:
            return None
        fn = TOOLS["accessibility_tree"].impl(runtime_id)
        try:
            if inspect.iscoroutinefunction(fn):
                return await fn(runtime_id)
            return await asyncio.to_thread(fn, runtime_id)
        except Exception as e:
            logger.info("no accessibility tree for this step", extra={"sandbox_id": self.sandbox.id, "error": repr(e)})
            return None

    async def get_environment(self) -> str:
        return ENVIRONMENTS.get(self.sandbox.kind, "linux")

    async def get_dimensions(self) -> tuple[int, int]:
        if self.size is None:
            await self.screenshot()
        assert self.size is not None
        return self.size

    async def screenshot(self, text: str | None = None) -> str:
        data = await self.call("screenshot")
        raw = base64.b64decode(data)
        if self.size is None:
            self.size = Image.open(io.BytesIO(raw)).size
        self.last, self.last_ref = raw, None
        return data

    async def click(self, x: int, y: int, button: str = "left") -> None:
        await self.call("click", x=x, y=y, button=BUTTONS.get(button, "left"))

    async def double_click(self, x: int, y: int) -> None:
        await self.call("double_click", x=x, y=y)

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        # cua's convention: positive scroll_y scrolls up, positive scroll_x scrolls left
        if scroll_y:
            await self.call("scroll", direction="up" if scroll_y > 0 else "down", amount=abs(scroll_y), x=x, y=y)
        if scroll_x:
            await self.call("scroll", direction="left" if scroll_x > 0 else "right", amount=abs(scroll_x), x=x, y=y)

    async def type(self, text: str) -> None:
        await self.call("type_text", text=text)

    async def wait(self, ms: int = 1000) -> None:
        await asyncio.sleep(min(ms, 30000) / 1000)

    async def move(self, x: int, y: int) -> None:
        await self.call("move_mouse", x=x, y=y)

    async def keypress(self, keys: list[str] | str) -> None:
        keys = [keys] if isinstance(keys, str) else [k for k in keys if k]
        if len(keys) == 1:
            await self.call("press_key", key=keys[0])
        else:
            await self.call("hotkey", keys=keys)

    async def drag(self, path: list[dict[str, int]]) -> None:
        start, end = path[0], path[-1]
        await self.call("drag", start_x=start["x"], start_y=start["y"], end_x=end["x"], end_y=end["y"])

    async def get_current_url(self) -> str:
        return ""

    async def left_mouse_down(self, x: int | None = None, y: int | None = None) -> None:
        raise ToolError("left_mouse_down is not supported; use drag")

    async def left_mouse_up(self, x: int | None = None, y: int | None = None) -> None:
        raise ToolError("left_mouse_up is not supported; use drag")


@dataclass
class Run:
    """A handle on one agent run. Whichever process executes it, its events are read from the database (follow)."""

    id: str
    sandbox_id: str
    source: str
    task: asyncio.Task | None = None
    # set when this process gives the run back: a shutdown (resumed later) or a lost claim (resumed elsewhere)
    handed_back: str | None = None

    def stream(self) -> AsyncIterator[dict]:
        return follow(self.id)

    def emit(self, kind: str, content: str):
        with db_manager.session() as db:
            record(db, self.sandbox_id, self.id, kind, content, self.source)


def paused_by(run_id: str) -> str | None:
    """Who has taken the screen over from the run (a user's email, or a viewer link's end user), None if nobody."""
    with db_manager.session() as db:
        run = db.get_agent_run(id=run_id)
        if run is None or run.paused_at is None:
            return None
        by = run.paused_by or ""
        if by.startswith("link:"):
            link = db.get_viewer_link(id=by.removeprefix("link:"))
            return (link.end_user if link is not None else None) or "a viewer link"
        user = db.get_user(id=by)
        return user.email if user is not None else "someone"


def record(
    db: Querier,
    sandbox_id: str,
    run_id: str | None,
    kind: str,
    content: str,
    source: str,
    screenshot: str | None = None,
    a11y: str | None = None,
) -> AgentMessage:
    message = db.create_agent_message(
        CreateAgentMessageParams(
            id=str(uuid.uuid4()),
            sandbox_id=sandbox_id,
            run_id=run_id,
            kind=kind,
            content=content,
            source=source,
            screenshot=screenshot,
            a11y=a11y,
        )
    )
    assert message is not None
    return message


async def follow(run_id: str) -> AsyncIterator[dict]:
    """Streams a run from the database, from its first message until it finishes. Each write to the run or its
    messages notifies the agent_run channel (db/migrations, notify_agent_run), so this reads again only when there
    is something new, whichever process wrote it."""
    async with await psycopg.AsyncConnection.connect(db_manager.url, autocommit=True) as conn:
        # listening before the first read, so nothing written in between is missed
        await conn.execute("LISTEN agent_run")
        seen = 0
        usage: tuple | None = None
        running = False
        while True:
            # the run first: once it reads finished, every message it recorded is already there to read
            with db_manager.session() as db:
                run = db.get_agent_run(id=run_id)
                messages = list(db.list_agent_run_messages(run_id=run_id, offset=seen))
            for m in messages:
                yield event_of(m)
            seen += len(messages)
            if run is None:
                yield {"type": "done", "state": "failed"}
                return
            current = (run.steps, run.tokens, run.cost)
            # a run that finished since the last read still reports what it used last
            if run.state != "queued" and current != usage:
                usage = current
                yield usage_event(run)
            if run.state in FINISHED and not messages:
                yield {"type": "done", "state": run.state}
                return
            # handed back by a worker shutting down: it resumes later, as a new stream
            if running and run.state == "queued" and not messages:
                yield {"type": "done", "state": "queued"}
                return
            running = running or run.state == "running"
            if not messages:
                async for notice in conn.notifies():
                    if notice.payload == run_id:
                        break


def usage_event(run: AgentRun) -> dict:
    return {
        "type": "usage",
        "steps": run.steps,
        "tokens": run.tokens,
        "cost": run.cost,
        "elapsed": round(seconds_since(run.started_at), 1),
        "max_steps": run.max_steps,
        "max_seconds": run.max_seconds,
        "max_tokens": run.max_tokens,
    }


def sse(events: AsyncIterator[dict]) -> StreamingResponse:
    async def body():
        async for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


class AgentApi:
    def __init__(self, app: FastAPI, auth: AuthApi, sandboxes: SandboxApi):
        self.logger = logger
        self.app = app
        self.auth = auth
        self.sandboxes = sandboxes
        # sandbox id -> the run executing in this process
        self.runs: dict[str, Run] = {}
        self.closing = False
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/sandboxes/{sandbox_id}/agent", response_model=AgentStateResponse)
        def state(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> AgentStateResponse:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            latest = db.get_latest_agent_run(sandbox_id=sandbox.id)
            return AgentStateResponse(
                running=latest is not None and latest.state not in FINISHED,
                model=self.config(sandbox.workspace_id, db).model,
                run=run_response(latest) if latest is not None else None,
                messages=[message_response(m) for m in db.list_agent_messages(sandbox_id=sandbox.id)],
            )

        @self.app.post("/sandboxes/{sandbox_id}/agent")
        async def chat(sandbox_id: str, payload: AgentRequest, user: Member = Depends(current_user)):
            run = self.start(
                user, sandbox_id, payload.message, "api", payload.model, payload.provider, payload.api_base
            )
            if payload.stream:
                return sse(run.stream())
            return {"run_id": run.id, "events": [e async for e in run.stream()]}

        @self.app.get("/sandboxes/{sandbox_id}/agent/stream")
        def attach(sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)):
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            run = self.handle(sandbox.id, db)
            if run is None:
                raise HTTPException(status_code=404, detail="the agent is not running")
            return sse(run.stream())

        @self.app.post("/sandboxes/{sandbox_id}/agent/stop", status_code=204)
        def stop(sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)):
            self.stop(self.sandboxes.owned(sandbox_id, user, db).id)

        @self.app.post("/sandboxes/{sandbox_id}/agent/takeover", response_model=AgentRunResponse)
        def takeover(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> AgentRunResponse:
            """Pauses the running agent before its next action, so a person can use the screen."""
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            run = db.get_active_agent_run(sandbox_id=sandbox.id)
            if run is None or run.state != "running":
                raise HTTPException(status_code=409, detail="the agent is not running")
            paused = db.pause_agent_run(paused_by=user.id, id=run.id)
            if paused is None:
                raise HTTPException(status_code=409, detail="the screen is already taken over")
            return run_response(paused)

        @self.app.post("/sandboxes/{sandbox_id}/agent/release", response_model=AgentRunResponse)
        def release(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> AgentRunResponse:
            """Hands the screen back to the agent, which carries on from the screen as the person left it."""
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            run = db.get_active_agent_run(sandbox_id=sandbox.id)
            resumed = db.resume_agent_run(id=run.id) if run is not None else None
            if resumed is None:
                raise HTTPException(status_code=409, detail="the screen is not taken over")
            return run_response(resumed)

        @self.app.delete("/sandboxes/{sandbox_id}/agent", status_code=204)
        def reset(sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)):
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            if db.get_active_agent_run(sandbox_id=sandbox.id) is not None:
                raise HTTPException(status_code=409, detail="stop the agent before clearing the conversation")
            self.forget(sandbox.id, db)

        @self.app.get("/sandboxes/{sandbox_id}/agent/messages/{message_id}/screenshot")
        def screenshot(
            sandbox_id: str,
            message_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> Response:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            message = db.get_agent_message(id=message_id)
            if message is None or message.sandbox_id != sandbox.id or not message.screenshot:
                raise HTTPException(status_code=404, detail="no screenshot for this step")
            try:
                data = decrypt_bytes(objects.get(screen_key(message.screenshot)))
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="the screenshot was removed")
            return Response(data, media_type=image_type(data), headers={"Cache-Control": "private, max-age=86400"})

        @self.app.get("/agent/providers", response_model=list[ProviderStatusResponse])
        def providers(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ProviderStatusResponse]:
            """What a run can be started with, and whether this workspace has a key for each."""
            return [
                ProviderStatusResponse(
                    id=id,
                    label=p.label,
                    default_model=p.default_model,
                    default_base=p.default_base,
                    needs_key=p.env_key is not None,
                    server_key=bool(p.env_key and os.environ.get(p.env_key)),
                    has_key=self.has_key(id, user.workspace_id, db),
                    key_secret=key_name(id) if p.env_key else None,
                )
                for id, p in PROVIDERS.items()
            ]

        @self.app.get("/agent/settings", response_model=AgentSettingsResponse)
        def get_settings(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> AgentSettingsResponse:
            workspace_id = user.workspace_id
            return self.settings_response(db.get_workspace_agent_settings(workspace_id=workspace_id), db)

        @self.app.put("/agent/settings", response_model=AgentSettingsResponse)
        def put_settings(
            payload: AgentSettingsRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> AgentSettingsResponse:
            workspace_id = user.workspace_id
            saved = db.get_workspace_agent_settings(workspace_id=workspace_id)
            # a key belongs to its provider, so switching providers drops it unless a new one is given
            key_id = saved.api_key_secret_id if saved and saved.provider == payload.provider else None
            if payload.api_key_secret_id is not None:
                key_id = None
                if payload.api_key_secret_id:
                    secret = db.get_vault_secret(id=payload.api_key_secret_id)
                    if secret is None or secret.workspace_id != user.workspace_id:
                        raise HTTPException(status_code=404, detail="vault secret not found")
                    key_id = secret.id
            if payload.api_key is not None:
                key = payload.api_key.strip()
                key_id = self.save_key(user, payload.provider, key, workspace_id, db) if key else None
            settings = db.upsert_workspace_agent_settings(
                UpsertWorkspaceAgentSettingsParams(
                    workspace_id=workspace_id,
                    provider=payload.provider,
                    model=payload.model.strip(),
                    api_key_secret_id=key_id,
                    api_base=(payload.api_base or "").strip() or None,
                    max_steps=min(payload.max_steps, MAX_STEPS) if payload.max_steps else None,
                    max_seconds=min(payload.max_seconds, MAX_SECONDS) if payload.max_seconds else None,
                    max_tokens=min(payload.max_tokens, MAX_TOKENS) if payload.max_tokens else None,
                    updated_by=user.id,
                )
            )
            return self.settings_response(settings, db)

        @self.app.delete("/agent/settings", response_model=AgentSettingsResponse)
        def reset_settings(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> AgentSettingsResponse:
            db.delete_workspace_agent_settings(workspace_id=user.workspace_id)
            return self.settings_response(None, db)

        @self.app.get("/agent/integrations", response_model=list[IntegrationResponse])
        def integrations(user: Member = Depends(current_user)) -> list[IntegrationResponse]:
            return [
                IntegrationResponse(platform="slack", configured=slack.configured(), webhook_path=slack.WEBHOOK_PATH),
                IntegrationResponse(platform="discord", configured=discord.configured(), webhook_path=None),
                IntegrationResponse(
                    platform="whatsapp", configured=whatsapp.configured(), webhook_path=whatsapp.WEBHOOK_PATH
                ),
            ]

        @self.app.get("/sandboxes/{sandbox_id}/agent/channels", response_model=list[ChannelResponse])
        def list_channels(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ChannelResponse]:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            return [channel_response(c) for c in db.list_agent_channels_by_sandbox(sandbox_id=sandbox.id)]

        @self.app.post("/sandboxes/{sandbox_id}/agent/channels", response_model=ChannelResponse, status_code=201)
        def add_channel(
            sandbox_id: str,
            payload: ChannelRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> ChannelResponse:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            external_id = normalize(payload.platform, payload.external_id)
            if not external_id:
                raise HTTPException(status_code=422, detail="enter a channel ID or phone number")
            allowed = normalize_users(payload.platform, payload.allowed_users)
            if payload.platform != "whatsapp" and not allowed:
                raise HTTPException(
                    status_code=422,
                    detail="list the user IDs allowed to command this sandbox from the channel, or * for anyone",
                )
            if db.get_agent_channel(platform=payload.platform, external_id=external_id) is not None:
                raise HTTPException(
                    status_code=409, detail=f"that {payload.platform} channel is already linked to a sandbox"
                )
            channel = db.create_agent_channel(
                CreateAgentChannelParams(
                    id=str(uuid.uuid4()),
                    sandbox_id=sandbox.id,
                    platform=payload.platform,
                    external_id=external_id,
                    created_by=user.id,
                    allowed_users=json.dumps(allowed if payload.platform != "whatsapp" else []),
                )
            )
            return channel_response(channel)

        @self.app.patch("/sandboxes/{sandbox_id}/agent/channels/{channel_id}", response_model=ChannelResponse)
        def update_channel(
            sandbox_id: str,
            channel_id: str,
            payload: ChannelUpdate,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> ChannelResponse:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            channel = db.get_agent_channel_by_id(id=channel_id)
            if channel is None or channel.sandbox_id != sandbox.id:
                raise HTTPException(status_code=404, detail="channel not found")
            if channel.platform == "whatsapp":
                raise HTTPException(status_code=400, detail="a WhatsApp link is already limited to its phone number")
            allowed = normalize_users(channel.platform, payload.allowed_users)
            if not allowed:
                raise HTTPException(status_code=422, detail="list at least one user ID, or * for anyone")
            return channel_response(
                db.set_agent_channel_allowed_users(allowed_users=json.dumps(allowed), id=channel.id)
            )

        @self.app.delete("/sandboxes/{sandbox_id}/agent/channels/{channel_id}", status_code=204)
        def remove_channel(
            sandbox_id: str,
            channel_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ):
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            channel = db.get_agent_channel_by_id(id=channel_id)
            if channel is None or channel.sandbox_id != sandbox.id:
                raise HTTPException(status_code=404, detail="channel not found")
            db.delete_agent_channel(id=channel.id)

    # Settings

    def save_key(self, user: Member, provider: str, key: str, workspace_id: str, db: Querier) -> str:
        """Stores a provider key typed into the agent settings as a vault secret and returns its id."""
        name = key_name(provider)
        ciphertext = encrypt(key, db, workspace_id)
        secret = db.get_vault_secret_by_name(workspace_id=workspace_id, name=name)
        if secret is not None:
            db.update_vault_secret_value(ciphertext=ciphertext, id=secret.id)
            audit(db, user, "secret.rotate", "secret", secret.id, name=name)
            return secret.id
        created = db.create_vault_secret(
            CreateVaultSecretParams(
                id=str(uuid.uuid4()),
                user_id=user.id,
                name=name,
                description=f"{PROVIDERS[provider].label} key for the agent",
                ciphertext=ciphertext,
                workspace_id=workspace_id,
            )
        )
        assert created is not None
        audit(db, user, "secret.create", "secret", created.id, name=name)
        return created.id

    def config(self, workspace_id: str, db: Querier) -> ModelConfig:
        """The workspace's provider and model, or the server default (ZOO_AGENT_MODEL with keys from the environment)."""
        settings = db.get_workspace_agent_settings(workspace_id=workspace_id)
        if settings is None:
            return ModelConfig(model=MODEL)
        provider = PROVIDERS[settings.provider]
        key = None
        if settings.api_key_secret_id:
            secret = db.get_vault_secret(id=settings.api_key_secret_id)
            if secret is not None:
                key = decrypt(secret.ciphertext)
        return ModelConfig(
            model=provider.prefix + settings.model,
            api_key=key,
            api_base=settings.api_base or provider.default_base,
        )

    def run_config(self, row: AgentRun, workspace_id: str, db: Querier) -> ModelConfig:
        """The provider and model a run was started with, else the workspace's (with the run's model, if it has one)."""
        if row.provider is None:
            config = self.config(workspace_id, db)
            if row.model:
                return ModelConfig(model=row.model, api_key=config.api_key, api_base=config.api_base)
            return config
        provider = PROVIDERS[row.provider]
        return ModelConfig(
            model=provider.prefix + (row.model or provider.default_model),
            api_key=self.vault_key(row.provider, workspace_id, db),
            api_base=row.api_base or provider.default_base,
        )

    def vault_key(self, provider: str, workspace_id: str, db: Querier) -> str | None:
        """The workspace's key for the provider: the one its agent settings use, else the vault secret
        AGENT_<PROVIDER>_API_KEY. None leaves the provider to read its environment variable."""
        settings = db.get_workspace_agent_settings(workspace_id=workspace_id)
        if settings is not None and settings.provider == provider and settings.api_key_secret_id:
            secret = db.get_vault_secret(id=settings.api_key_secret_id)
            if secret is not None:
                return decrypt(secret.ciphertext)
        secret = db.get_vault_secret_by_name(workspace_id=workspace_id, name=key_name(provider))
        return decrypt(secret.ciphertext) if secret is not None else None

    def has_key(self, provider: str, workspace_id: str, db: Querier) -> bool:
        env_key = PROVIDERS[provider].env_key
        if env_key is None or os.environ.get(env_key):
            return True
        return self.vault_key(provider, workspace_id, db) is not None

    def limits(self, workspace_id: str, db: Querier) -> Limits:
        settings = db.get_workspace_agent_settings(workspace_id=workspace_id)
        return limits_of(settings)

    def settings_response(self, settings: WorkspaceAgentSetting | None, db: Querier) -> AgentSettingsResponse:
        secret = db.get_vault_secret(id=settings.api_key_secret_id) if settings and settings.api_key_secret_id else None
        limits = limits_of(settings)
        return AgentSettingsResponse(
            provider=settings.provider if settings else None,
            model=settings.model if settings else MODEL,
            has_api_key=secret is not None,
            api_key_secret=SecretRef(id=secret.id, name=secret.name) if secret else None,
            api_base=settings.api_base if settings else None,
            default_model=MODEL,
            limits=LimitsResponse(max_steps=limits.steps, max_seconds=limits.seconds, max_tokens=limits.tokens),
            caps=LimitsResponse(max_steps=MAX_STEPS, max_seconds=MAX_SECONDS, max_tokens=MAX_TOKENS),
            providers=[
                ProviderResponse(
                    id=id,
                    label=p.label,
                    default_model=p.default_model,
                    default_base=p.default_base,
                    needs_key=p.env_key is not None,
                    server_key=bool(p.env_key and os.environ.get(p.env_key)),
                )
                for id, p in PROVIDERS.items()
            ],
        )

    # Runs

    def start(
        self,
        user: Member,
        sandbox_id: str,
        text: str,
        source: str,
        model: str | None = None,
        provider: str | None = None,
        api_base: str | None = None,
    ) -> Run:
        """Records the user's message and queues a run, with the workspace's provider and model or the ones given.
        In a worker process the run starts here right away; otherwise a worker picks it up. Must be called on the
        event loop."""
        with db_manager.session() as db:
            sandbox = self.sandboxes.running(sandbox_id, user, db)
            if provider is not None and not self.has_key(provider, sandbox.workspace_id, db):
                raise HTTPException(
                    status_code=400,
                    detail=f"no key for {PROVIDERS[provider].label}: add the vault secret {key_name(provider)} "
                    f"or set {PROVIDERS[provider].env_key} on the server",
                )
            if sandbox.kind == "code":
                raise HTTPException(
                    status_code=400, detail="the computer-use agent needs a desktop; code sandboxes have no screen"
                )
            if db.get_active_agent_run(sandbox_id=sandbox.id) is not None:
                raise HTTPException(status_code=409, detail="the agent is already working in this sandbox")
            limits = self.limits(sandbox.workspace_id, db)
            try:
                row = db.create_agent_run(
                    CreateAgentRunParams(
                        id=str(uuid.uuid4()),
                        sandbox_id=sandbox.id,
                        user_id=user.id,
                        source=source,
                        model=model,
                        max_steps=limits.steps,
                        max_seconds=limits.seconds,
                        max_tokens=limits.tokens,
                        max_attempts=MAX_ATTEMPTS,
                        provider=provider,
                        api_base=api_base,
                    )
                )
            except IntegrityError:
                raise HTTPException(status_code=409, detail="the agent is already working in this sandbox")
            assert row is not None
            self.record(db, sandbox.id, row.id, "user", text, source)
        run = Run(id=row.id, sandbox_id=sandbox.id, source=source)
        if workers.works() and not self.closing and len(self.runs) < PARALLEL:
            launched = self.claim(row)
            if launched is not None:
                return launched
        return run

    def handle(self, sandbox_id: str, db: Querier) -> Run | None:
        """The sandbox's unfinished run, to stream."""
        local = self.runs.get(sandbox_id)
        if local is not None:
            return local
        active = db.get_active_agent_run(sandbox_id=sandbox_id)
        if active is None:
            return None
        return Run(id=active.id, sandbox_id=sandbox_id, source=active.source)

    def claim(self, row: AgentRun) -> Run | None:
        """Claims a queued run for this process and starts it. Must be called on the event loop."""
        if row.sandbox_id in self.runs:
            return None
        with db_manager.session() as db:
            claimed = db.claim_agent_run(worker=workers.WORKER_ID, id=row.id)
            if claimed is None:
                return None
        run = Run(id=claimed.id, sandbox_id=claimed.sandbox_id, source=claimed.source)
        self.runs[claimed.sandbox_id] = run
        run.task = asyncio.create_task(self.execute(run, claimed))
        return run

    def stop(self, sandbox_id: str) -> bool:
        """Stops the sandbox's run, wherever it runs. False when nothing is running."""
        local = self.runs.get(sandbox_id)
        if local is not None and local.task is not None:
            local.task.cancel()
            return True
        with db_manager.session() as db:
            active = db.get_active_agent_run(sandbox_id=sandbox_id)
            if active is None:
                return False
            if active.state == "queued":
                db.finish_agent_run(state="cancelled", error="stopped", id=active.id)
                self.record(db, sandbox_id, active.id, "error", "stopped", active.source)
                metrics.agent_runs.add(1, {"outcome": "cancelled"})
            else:
                # the worker running it sees this on its next heartbeat
                db.request_agent_run_cancel(id=active.id)
        return True

    def clear(self, sandbox_id: str) -> bool:
        with db_manager.session() as db:
            if db.get_active_agent_run(sandbox_id=sandbox_id) is not None:
                return False
            self.forget(sandbox_id, db)
        return True

    def forget(self, sandbox_id: str, db: Querier):
        """Clears the conversation and the screenshots of its runs."""
        db.delete_agent_messages(sandbox_id=sandbox_id)
        for run in db.list_agent_runs_by_sandbox(sandbox_id=sandbox_id):
            objects.delete_prefix(f"agent/{run.id}/")

    def history(self, sandbox_id: str, db: Querier) -> list[dict]:
        """Earlier turns as plain user/assistant text. Actions and screenshots stay out; the agent takes a fresh screenshot."""
        turns = [m for m in db.list_agent_messages(sandbox_id=sandbox_id) if m.kind in ("user", "text")]
        return [{"role": "user" if m.kind == "user" else "assistant", "content": m.content} for m in turns[-HISTORY:]]

    def record(
        self,
        db: Querier,
        sandbox_id: str,
        run_id: str | None,
        kind: str,
        content: str,
        source: str,
        screenshot: str | None = None,
    ) -> AgentMessage:
        return record(db, sandbox_id, run_id, kind, content, source, screenshot)

    def emit(self, run: Run, kind: str, content: str, screenshot: str | None = None, a11y: str | None = None):
        with db_manager.session() as db:
            record(db, run.sandbox_id, run.id, kind, content, run.source, screenshot, a11y)

    async def save_a11y(self, run: Run, computer: ZooComputer, workspace_id: str) -> str | None:
        """Stores the accessibility tree an action was decided on, next to its screenshot, and returns its reference."""
        tree = await computer.accessibility_tree()
        if tree is None:
            return None
        ref = f"{run.id}/{uuid.uuid4().hex}.a11y.enc"
        with db_manager.session() as db:
            token = encrypt_bytes(json.dumps(tree).encode(), db, workspace_id)
        await asyncio.to_thread(objects.put, screen_key(ref), token)
        return ref

    async def save_screen(self, run: Run, computer: ZooComputer, workspace_id: str) -> str | None:
        """Stores the screen an action was decided on, once, and returns its reference. cua runs the action after
        this, so when the agent hasn't seen a screenshot yet, the screen now is still the one before the action."""
        if computer.last is None:
            try:
                await computer.screenshot()
            except Exception as e:
                self.logger.warning("agent screenshot failed", extra={"run_id": run.id, "error": repr(e)})
                return None
        assert computer.last is not None
        if computer.last_ref is None:
            ref = f"{run.id}/{uuid.uuid4().hex}.bin"
            with db_manager.session() as db:
                token = encrypt_bytes(computer.last, db, workspace_id)
            await asyncio.to_thread(objects.put, screen_key(ref), token)
            computer.last_ref = ref
        return computer.last_ref

    def finish(self, run: Run, state: str, error: str | None):
        with db_manager.session() as db:
            db.finish_agent_run(state=state, error=error, id=run.id)
        metrics.agent_runs.add(1, {"outcome": state})
        if state == "failed":
            metrics.error("agent")

    async def execute(self, run: Run, row: AgentRun):
        state, error = "succeeded", None
        steps, tokens, cost = row.steps, row.tokens, row.cost
        # what was already metered (server/metering.py): a resumed run picks up from its stored usage
        metered = (steps, tokens, cost)
        try:
            with db_manager.session() as db:
                sandbox = db.get_sandbox(id=row.sandbox_id)
                person = db.get_user(id=row.user_id)
                # the run acts as a member of the sandbox's workspace, whoever's request started it
                user = (
                    Member(**person.model_dump(), workspace_id=sandbox.workspace_id, role="member")
                    if person is not None and sandbox is not None
                    else None
                )
                if sandbox is None or user is None or sandbox.status != "running" or not sandbox.runtime_id:
                    raise RuntimeError(f"the sandbox is {sandbox.status if sandbox else 'gone'}")
                config = self.run_config(row, sandbox.workspace_id, db)
                workspace = db.get_workspace(id=sandbox.workspace_id)
                capture_a11y = workspace is not None and workspace.capture_a11y
                messages = self.history(sandbox.id, db)
            if row.attempts > 1:
                self.emit(run, "status", "Resumed after a server restart.")
                messages.append({"role": "user", "content": RESUME_NOTE})
            remaining = row.max_seconds - seconds_since(row.started_at)
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining) as deadline:
                computer = ZooComputer(self.sandboxes, user, sandbox, run, deadline)
                agent = ComputerAgent(
                    model=config.model,
                    api_key=config.api_key,
                    api_base=config.api_base,
                    tools=[computer],
                    instructions=f"You are operating {SCREENS.get(sandbox.kind, 'a desktop')} inside a Zoo sandbox. "
                    "Take a screenshot before acting, work step by step and check the result of each action. "
                    "When the task is done, reply with a short summary of what you did.",
                    only_n_most_recent_images=3,
                    telemetry_enabled=False,
                )
                async for result in agent.run(messages):
                    usage = result.get("usage") or {}
                    used = int(usage.get("total_tokens") or 0)
                    tokens += used
                    cost += float(usage.get("response_cost") or 0)
                    metrics.agent_tokens.add(used)
                    for item in result.get("output", []):
                        for kind, content in events_of(item):
                            shot = tree = None
                            if kind == "action":
                                shot = await self.save_screen(run, computer, sandbox.workspace_id)
                                if capture_a11y:
                                    tree = await self.save_a11y(run, computer, sandbox.workspace_id)
                            self.emit(run, kind, content, shot, tree)
                            steps += kind == "action"
                    with db_manager.session() as db:
                        db.record_agent_run_usage(steps=steps, tokens=tokens, cost=cost, id=run.id)
                        metering.agent(
                            db,
                            sandbox.workspace_id,
                            sandbox.id,
                            steps - metered[0],
                            tokens - metered[1],
                            cost - metered[2],
                        )
                    metered = (steps, tokens, cost)
                    if steps >= row.max_steps:
                        state, error = "failed", f"stopped after {row.max_steps} actions"
                        break
                    if tokens >= row.max_tokens:
                        state, error = "failed", f"stopped after {tokens:,} tokens (the limit is {row.max_tokens:,})"
                        break
            if error:
                self.emit(run, "error", error)
        except asyncio.CancelledError as e:
            if run.handed_back is not None:
                self.hand_back(run, row)
                return
            state, error = "cancelled", str(e) or "stopped"
            self.emit(run, "error", error)
        except TimeoutError:
            state, error = "failed", f"stopped after {duration(row.max_seconds)}"
            self.emit(run, "error", error)
        except Exception as e:
            self.logger.error("agent run failed", extra={"sandbox_id": run.sandbox_id, "error": repr(e)})
            state, error = "failed", str(e) or e.__class__.__name__
            self.emit(run, "error", error)
        finally:
            if run.handed_back is None:
                self.finish(run, state, error)
                self.runs.pop(run.sandbox_id, None)

    def hand_back(self, run: Run, row: AgentRun):
        """Leaves an interrupted run for the next worker: right away on a shutdown, or not at all when another
        worker has already taken it over."""
        self.runs.pop(run.sandbox_id, None)
        if run.handed_back == "shutdown":
            note = "Paused for a server restart; it picks up again when the server is back."
            with db_manager.session() as db:
                # a graceful hand-back doesn't count as an attempt
                db.requeue_agent_run(error="interrupted by a shutdown", attempts=1, id=run.id)
                self.record(db, run.sandbox_id, run.id, "status", note, run.source)

    # Worker

    async def work(self):
        """The worker loop: claims queued runs, keeps a heartbeat on the ones running here, resumes runs whose
        worker went away, and removes screenshots of deleted runs."""
        swept = 0.0
        beat = 0.0
        while not self.closing:
            try:
                now = time.monotonic()
                if now - beat >= HEARTBEAT_SECONDS:
                    beat = now
                    await asyncio.to_thread(self.recover)
                    self.heartbeat()
                if now - swept >= SWEEP_SECONDS:
                    swept = now
                    await asyncio.to_thread(self.sweep)
                self.claim_queued()
            except Exception as e:
                self.logger.error("agent worker failed", extra={"error": repr(e)})
            await asyncio.sleep(POLL_SECONDS)

    def claim_queued(self):
        with db_manager.session() as db:
            queued = list(db.list_queued_agent_runs())
        for row in queued:
            if self.closing or len(self.runs) >= PARALLEL:
                return
            self.claim(row)

    def heartbeat(self):
        for run in list(self.runs.values()):
            with db_manager.session() as db:
                cancel = db.heartbeat_agent_run(id=run.id, worker=workers.WORKER_ID)
            if run.task is None or run.task.done():
                continue
            if cancel is None:
                # another worker took the run over (this one looked dead to it); leave it to them
                self.logger.warning("agent run taken over", extra={"run_id": run.id, "sandbox_id": run.sandbox_id})
                run.handed_back = "lost"
                run.task.cancel()
            elif cancel:
                run.task.cancel("stopped")

    def recover(self):
        """Requeues runs whose worker stopped heartbeating, or fails them once they used up their attempts."""
        with db_manager.session() as db:
            stale = list(db.list_stale_agent_runs(heartbeat_at=stamp(-STALE_SECONDS)))
            for run in stale:
                if run.attempts < run.max_attempts:
                    self.logger.info("agent run resumed", extra={"run_id": run.id, "sandbox_id": run.sandbox_id})
                    db.requeue_agent_run(error="its worker stopped", attempts=0, id=run.id)
                else:
                    error = f"interrupted {run.attempts} times; send a message to try again"
                    db.finish_agent_run(state="failed", error=error, id=run.id)
                    self.record(db, run.sandbox_id, run.id, "error", error, run.source)
                    metrics.agent_runs.add(1, {"outcome": "failed"})
                    metrics.error("agent")

    def sweep(self):
        """Removes stored screenshots whose run no longer exists (its sandbox was deleted)."""
        with db_manager.session() as db:
            known = {r for r in db.list_agent_run_ids()}
        for key in objects.keys("agent/"):
            run_id = key.split("/")[1] if key.count("/") >= 2 else ""
            if run_id not in known:
                objects.delete(key)

    async def shutdown(self, timeout: float = 10):
        """Gives running agents a moment to finish, then hands the rest back to the queue, so they resume when a
        worker is back. Each conversation notes the pause."""
        self.closing = True
        tasks = [run.task for run in self.runs.values() if run.task is not None]
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for run in list(self.runs.values()):
            if run.task in pending:
                run.handed_back = "shutdown"
                run.task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def key_name(provider: str) -> str:
    """The vault secret that holds a workspace's key for a provider."""
    return f"AGENT_{provider.upper()}_API_KEY"


def limits_of(settings: WorkspaceAgentSetting | None) -> Limits:
    def pick(value: int | None, cap: int) -> int:
        return min(value, cap) if value else cap

    if settings is None:
        return Limits(MAX_STEPS, MAX_SECONDS, MAX_TOKENS)
    return Limits(
        pick(settings.max_steps, MAX_STEPS),
        pick(settings.max_seconds, MAX_SECONDS),
        pick(settings.max_tokens, MAX_TOKENS),
    )


def channel_response(channel) -> ChannelResponse:
    return ChannelResponse(
        id=channel.id,
        platform=channel.platform,
        external_id=channel.external_id,
        allowed_users=json.loads(channel.allowed_users or "[]"),
        created_at=to_stamp(channel.created_at),
    )
