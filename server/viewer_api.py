"""Viewer links: signed URLs to a sandbox's screen, to embed in another app or to share with an end user or a support
person. A view link only watches: the proxy drops its input (server/vnc.py, Gate), whatever the page does. A control
link's input reaches the sandbox, and acting while an agent runs takes the screen over from the agent until it is
handed back.

A link is a random token; only its SHA-256 is stored. GET /embed/{token} is a page with nothing but the screen,
which only the origins in ZOO_EMBED_ORIGINS may frame (none when unset); its websocket is /embed/{token}/ws.
Revoking a link, or its expiry, disconnects its viewers within LINK_CHECK_SECONDS."""

import asyncio
import hashlib
import json
import os
import secrets
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from db.connection import db_manager
from db.generated.models import ViewerLink
from db.generated.query import CreateViewerLinkParams, Querier
from logger.logger import logger
from server import audit_api
from server.auth_api import AuthApi, Member, require
from server.jobs import stamp
from server.sandbox_api import SandboxApi
from server.vnc import Gate
from utils.time import to_stamp

MAX_HOURS = int(os.environ.get("ZOO_MAX_VIEWER_LINK_HOURS", "24"))
# who may put /embed pages in a frame: space- or comma-separated origins, as CSP frame-ancestors takes them
EMBED_ORIGINS = os.environ.get("ZOO_EMBED_ORIGINS", "").replace(",", " ").split()
TOKEN_PREFIX = "zv_"
NOVNC = "https://cdn.jsdelivr.net/npm/@novnc/novnc@1.6.0/core/rfb.js"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def api_url(request: Request) -> str:
    return os.environ.get("ZOO_API_URL") or str(request.base_url).rstrip("/")


class ViewerLinkRequest(BaseModel):
    mode: Literal["view", "control"] = "view"
    expires_in_minutes: int = Field(default=60, ge=1, le=MAX_HOURS * 60)
    # who the link is for, as the embedding app knows them; shown in takeovers and the audit log
    end_user: str | None = Field(default=None, max_length=200)


class ViewerLinkResponse(BaseModel):
    id: str
    sandbox_id: str
    mode: str
    end_user: str | None
    expires_at: str
    revoked_at: str | None
    created_at: str


class CreatedViewerLinkResponse(ViewerLinkResponse):
    token: str
    # the page to open or put in an iframe
    url: str


class EmbedStateResponse(BaseModel):
    mode: str
    sandbox_status: str
    # idle, running or paused (taken over)
    agent: str
    paused_by: str | None


def link_response(link: ViewerLink) -> ViewerLinkResponse:
    return ViewerLinkResponse(
        id=link.id,
        sandbox_id=link.sandbox_id,
        mode=link.mode,
        end_user=link.end_user,
        expires_at=to_stamp(link.expires_at),
        revoked_at=to_stamp(link.revoked_at),
        created_at=to_stamp(link.created_at),
    )


def live(link_id: str) -> bool:
    with db_manager.session() as db:
        return bool(db.viewer_link_live(id=link_id))


def frame_ancestors() -> str:
    return " ".join(EMBED_ORIGINS) if EMBED_ORIGINS else "'none'"


class ViewerApi:
    def __init__(self, app: FastAPI, auth: AuthApi, sandboxes: SandboxApi):
        self.logger = logger
        self.app = app
        self.auth = auth
        self.sandboxes = sandboxes
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.post(
            "/sandboxes/{sandbox_id}/viewer-links", response_model=CreatedViewerLinkResponse, status_code=201
        )
        def create_link(
            sandbox_id: str,
            payload: ViewerLinkRequest,
            request: Request,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> CreatedViewerLinkResponse:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            # viewers and read-only keys may share what they can see, not more
            if payload.mode == "control":
                require(user, "member")
            token = TOKEN_PREFIX + secrets.token_urlsafe(32)
            link = db.create_viewer_link(
                CreateViewerLinkParams(
                    id=str(uuid.uuid4()),
                    workspace_id=sandbox.workspace_id,
                    sandbox_id=sandbox.id,
                    token_hash=hash_token(token),
                    mode=payload.mode,
                    end_user=payload.end_user,
                    created_by=user.id,
                    expires_at=stamp(payload.expires_in_minutes * 60),
                )
            )
            assert link is not None
            return CreatedViewerLinkResponse(
                **link_response(link).model_dump(), token=token, url=f"{api_url(request)}/embed/{token}"
            )

        @self.app.get("/sandboxes/{sandbox_id}/viewer-links", response_model=list[ViewerLinkResponse])
        def list_links(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ViewerLinkResponse]:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            return [link_response(link) for link in db.list_viewer_links(sandbox_id=sandbox.id)]

        @self.app.delete("/sandboxes/{sandbox_id}/viewer-links/{link_id}", status_code=204)
        def revoke_link(
            sandbox_id: str,
            link_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ):
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            link = db.get_viewer_link(id=link_id)
            if link is None or link.sandbox_id != sandbox.id:
                raise HTTPException(status_code=404, detail="viewer link not found")
            db.revoke_viewer_link(id=link.id)

        @self.app.get("/embed/{token}", response_class=HTMLResponse)
        def embed(token: str) -> HTMLResponse:
            headers = {
                "Content-Security-Policy": f"frame-ancestors {frame_ancestors()}",
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            }
            with db_manager.session() as db:
                link = db.get_live_viewer_link(token_hash=hash_token(token))
            if link is None:
                return HTMLResponse(GONE, status_code=404, headers=headers)
            return HTMLResponse(page(token, link.mode), headers=headers)

        @self.app.get("/embed/{token}/state", response_model=EmbedStateResponse)
        def embed_state(token: str) -> EmbedStateResponse:
            with db_manager.session() as db:
                link = self.link(token, db)
                sandbox = db.get_sandbox(id=link.sandbox_id)
                run = db.get_active_agent_run(sandbox_id=link.sandbox_id)
            agent = "idle" if run is None else "paused" if run.paused_at is not None else run.state
            return EmbedStateResponse(
                mode=link.mode,
                sandbox_status=sandbox.status if sandbox is not None else "deleted",
                agent=agent,
                paused_by=run.paused_by if run is not None else None,
            )

        @self.app.post("/embed/{token}/takeover", status_code=204)
        def embed_takeover(token: str):
            with db_manager.session() as db:
                link = self.control_link(token, db)
                run = db.get_active_agent_run(sandbox_id=link.sandbox_id)
                if run is None or run.state != "running":
                    raise HTTPException(status_code=409, detail="the agent is not running")
                if db.pause_agent_run(paused_by=f"link:{link.id}", id=run.id) is None:
                    raise HTTPException(status_code=409, detail="the screen is already taken over")
            audit_api.system(
                link.workspace_id, "takeover", "sandboxes", link.sandbox_id, source="viewer_link", link_id=link.id
            )

        @self.app.post("/embed/{token}/release", status_code=204)
        def embed_release(token: str):
            with db_manager.session() as db:
                link = self.control_link(token, db)
                run = db.get_active_agent_run(sandbox_id=link.sandbox_id)
                if run is None or db.resume_agent_run(id=run.id) is None:
                    raise HTTPException(status_code=409, detail="the screen is not taken over")
            audit_api.system(
                link.workspace_id, "release", "sandboxes", link.sandbox_id, source="viewer_link", link_id=link.id
            )

        self.app.websocket("/embed/{token}/ws")(self.screen)

    def link(self, token: str, db: Querier) -> ViewerLink:
        link = db.get_live_viewer_link(token_hash=hash_token(token))
        if link is None:
            raise HTTPException(status_code=401, detail="this link was revoked or has expired")
        return link

    def control_link(self, token: str, db: Querier) -> ViewerLink:
        link = self.link(token, db)
        if link.mode != "control":
            raise HTTPException(status_code=403, detail="this link can only watch")
        return link

    async def screen(self, websocket: WebSocket, token: str):
        def find():
            with db_manager.session() as db:
                link = db.get_live_viewer_link(token_hash=hash_token(token))
                sandbox = db.get_sandbox(id=link.sandbox_id) if link is not None else None
            return link, sandbox

        link, sandbox = await asyncio.to_thread(find)
        if link is None:
            await websocket.close(code=1008, reason="this link was revoked or has expired")
            return
        if sandbox is None or sandbox.status != "running" or not sandbox.access_url:
            await websocket.close(code=1008, reason="sandbox not available")
            return
        gate = Gate(view_only=link.mode == "view")
        await self.sandboxes.screen(websocket, sandbox, gate, f"link:{link.id}", lambda: live(link.id))


GONE = """<!doctype html><meta charset="utf-8"><title>Link expired</title>
<body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#000;
color:#a1a1aa;font:14px system-ui,sans-serif">This link was revoked or has expired.</body>"""


def page(token: str, mode: str) -> str:
    """The embed page: the screen, a status line, and for a control link Take over / Hand back. It tells the page
    that frames it what happens with postMessage ({source: "zoo", type: "connect" | "disconnect" | "takeover" |
    "release" | "agent", ...}) and takes {source: "zoo", type: "takeover" | "release"} from it."""
    # script contents aren't HTML-decoded, so only a closing tag could break out of it
    config = json.dumps({"token": token, "mode": mode}).replace("<", "\\u003c")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zoo viewer</title>
<style>
  html, body {{ margin: 0; height: 100%; background: #000; overflow: hidden; font: 13px system-ui, sans-serif; }}
  #screen {{ position: absolute; inset: 0; }}
  #status {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    color: #a1a1aa; pointer-events: none; }}
  #bar {{ position: absolute; right: 12px; bottom: 12px; display: none; gap: 8px; align-items: center; }}
  #bar span {{ color: #e4e4e7; background: rgba(0,0,0,.6); padding: 6px 10px; border-radius: 6px; }}
  #bar button {{ font: inherit; padding: 6px 12px; border-radius: 6px; border: 0; cursor: pointer;
    background: #fafafa; color: #18181b; }}
</style></head>
<body><div id="screen"></div><div id="status">Connecting…</div>
<div id="bar"><span id="agent"></span><button id="toggle" type="button"></button></div>
<script id="config" type="application/json">{config}</script>
<script type="module">
import RFB from "{NOVNC}";
const {{ token, mode }} = JSON.parse(document.getElementById("config").textContent);
const base = location.pathname.replace(/\\/$/, "");
const status = document.getElementById("status");
const bar = document.getElementById("bar");
const label = document.getElementById("agent");
const toggle = document.getElementById("toggle");
const tell = (type, extra = {{}}) => parent !== window && parent.postMessage({{ source: "zoo", type, ...extra }}, "*");
let agent = "idle";

function connect() {{
  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + base + "/ws";
  const rfb = new RFB(document.getElementById("screen"), url);
  rfb.scaleViewport = true;
  rfb.background = "#000";
  rfb.viewOnly = mode === "view";
  rfb.addEventListener("connect", () => {{ status.textContent = ""; tell("connect"); }});
  rfb.addEventListener("disconnect", () => {{
    status.textContent = "Disconnected";
    tell("disconnect");
    setTimeout(connect, 3000);
  }});
}}

async function act(kind) {{
  const response = await fetch(base + "/" + kind, {{ method: "POST" }});
  if (response.ok) tell(kind);
  await refresh();
}}

async function refresh() {{
  const response = await fetch(base + "/state");
  if (!response.ok) {{ status.textContent = "This link was revoked or has expired."; return; }}
  const state = await response.json();
  if (state.agent !== agent) tell("agent", {{ state: state.agent }});
  agent = state.agent;
  if (mode !== "control" || agent === "idle") {{ bar.style.display = "none"; return; }}
  bar.style.display = "flex";
  label.textContent = agent === "paused" ? "You have control" : "The agent is working";
  toggle.textContent = agent === "paused" ? "Hand back" : "Take over";
}}

toggle.addEventListener("click", () => act(agent === "paused" ? "release" : "takeover"));
addEventListener("message", (event) => {{
  if (event.source !== parent || event.data?.source !== "zoo" || mode !== "control") return;
  if (event.data.type === "takeover" || event.data.type === "release") act(event.data.type);
}});
connect();
refresh();
setInterval(refresh, 3000);
</script></body></html>"""
