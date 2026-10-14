"""Webhooks: events (server/events.py) POSTed to a URL a workspace admin registers.

Each event a webhook wants (its types, [] for all, and its label selector) gets a delivery row in the event's own
transaction (db/migrations, webhook_deliveries_for), so none is lost to a crash. Workers send due deliveries, leased
so two never send the same one, and retry those that fail after BACKOFF (1 min, 5 min, 30 min, 2 h, 6 h; override
with ZOO_WEBHOOK_BACKOFF_SECONDS, comma-separated), then mark them failed.

A delivery is a JSON body (the event, as GET /events returns it, plus workspace_id) with headers Zoo-Event-Id,
Zoo-Event-Type, Zoo-Timestamp and Zoo-Signature: v1=<hex HMAC-SHA256 of "{timestamp}.{body}" with the webhook's
secret>, which is shown once when the webhook is made. Receivers should check the signature and that the timestamp
is recent, and answer 2xx within TIMEOUT seconds.

Webhook URLs must be http(s) and, unless ZOO_WEBHOOK_ALLOW_PRIVATE=1, resolve to public addresses only, checked when
the webhook is saved and again before every send, so a webhook can't reach into Zoo's own network."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from db.connection import db_manager
from db.generated.models import Webhook
from db.generated.query import CreateWebhookParams, FinishWebhookDeliveryParams, Querier, UpdateWebhookParams
from logger.logger import logger
from server import labels, metrics, pages
from server.auth_api import AuthApi, Member, require
from server.events import event_response
from server.jobs import stamp
from server.security import decrypt, encrypt
from utils.time import to_stamp

BACKOFF = [
    int(s) for s in os.environ.get("ZOO_WEBHOOK_BACKOFF_SECONDS", "60,300,1800,7200,21600").split(",") if s.strip()
]
ALLOW_PRIVATE = os.environ.get("ZOO_WEBHOOK_ALLOW_PRIVATE") == "1"
TIMEOUT = 10
BATCH = 20
LEASE_SECONDS = 120
POLL_SECONDS = 2
SECRET_PREFIX = "whsec_"
EVENT_TYPES = {
    "sandbox.state",
}


def check_url(url: str):
    """ValueError unless the URL is http(s) and its host resolves only to addresses webhooks may reach."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("the URL must be http:// or https:// with a host")
    if ALLOW_PRIVATE:
        return
    try:
        found = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise ValueError(f"couldn't resolve {parsed.hostname}: {e}")
    for *_, address in found:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global:
            raise ValueError(f"{parsed.hostname} resolves to {ip}, which isn't a public address")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"v1={digest}"


class WebhookRequest(BaseModel):
    url: str = Field(max_length=2000)
    # event types to send; empty: all of them
    types: list[str] = []
    # only events of sandboxes with these labels; null: any
    label_selector: dict[str, str] | None = None
    enabled: bool = True

    @field_validator("types")
    @classmethod
    def check_types(cls, value: list[str]) -> list[str]:
        unknown = set(value) - EVENT_TYPES
        if unknown:
            raise ValueError(f"unknown event types {sorted(unknown)}; known: {sorted(EVENT_TYPES)}")
        return sorted(set(value))

    @field_validator("label_selector")
    @classmethod
    def check_selector(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return labels.validate(value) if value else None


class WebhookResponse(BaseModel):
    id: str
    url: str
    types: list[str]
    label_selector: dict[str, str] | None
    enabled: bool
    created_at: str


class CreatedWebhookResponse(WebhookResponse):
    # shown once: sign checks with it
    secret: str


class DeliveryResponse(BaseModel):
    id: str
    event_id: str
    event_type: str
    status: str
    attempts: int
    next_attempt_at: str
    last_error: str | None
    response_status: int | None
    created_at: str
    delivered_at: str | None


def webhook_response(row: Webhook) -> WebhookResponse:
    return WebhookResponse(
        id=row.id,
        url=row.url,
        types=json.loads(row.types),
        label_selector=labels.parse_selector(row.label_selector),
        enabled=row.enabled,
        created_at=to_stamp(row.created_at),
    )


class WebhooksApi:
    def __init__(self, app: APIRouter, auth: AuthApi):
        self.logger = logger
        self.app = app
        self.auth = auth
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/webhooks", response_model=list[WebhookResponse])
        def list_webhooks(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[WebhookResponse]:
            require(user, "admin")
            return [webhook_response(row) for row in db.list_webhooks(workspace_id=user.workspace_id)]

        @self.app.post("/webhooks", response_model=CreatedWebhookResponse, status_code=201)
        def create_webhook(
            payload: WebhookRequest, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> CreatedWebhookResponse:
            valid_url(payload.url)
            secret = SECRET_PREFIX + secrets.token_urlsafe(32)
            row = db.create_webhook(
                CreateWebhookParams(
                    id=str(uuid.uuid4()),
                    workspace_id=user.workspace_id,
                    url=payload.url,
                    secret_ciphertext=encrypt(secret, db, user.workspace_id),
                    types=json.dumps(payload.types),
                    label_selector=json.dumps(payload.label_selector) if payload.label_selector else None,
                    enabled=payload.enabled,
                    created_by=user.id,
                )
            )
            assert row is not None
            return CreatedWebhookResponse(**webhook_response(row).model_dump(), secret=secret)

        @self.app.put("/webhooks/{webhook_id}", response_model=WebhookResponse)
        def update_webhook(
            webhook_id: str,
            payload: WebhookRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> WebhookResponse:
            self.owned(webhook_id, user, db)
            valid_url(payload.url)
            row = db.update_webhook(
                UpdateWebhookParams(
                    url=payload.url,
                    types=json.dumps(payload.types),
                    label_selector=json.dumps(payload.label_selector) if payload.label_selector else None,
                    enabled=payload.enabled,
                    id=webhook_id,
                )
            )
            assert row is not None
            return webhook_response(row)

        @self.app.delete("/webhooks/{webhook_id}", status_code=204)
        def delete_webhook(
            webhook_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ):
            db.delete_webhook(id=self.owned(webhook_id, user, db).id)

        @self.app.get("/webhooks/{webhook_id}/deliveries", response_model=pages.Page[DeliveryResponse])
        def deliveries(
            webhook_id: str,
            page: pages.Params = Depends(),
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> pages.Page[DeliveryResponse]:
            require(user, "admin")
            webhook = self.owned(webhook_id, user, db)
            rows, after = pages.collect(
                lambda at, id, limit: db.page_webhook_deliveries(
                    webhook_id=webhook.id, before_at=at, before_id=id, row_limit=limit
                ),
                lambda row: True,
                lambda row: (row.created_at, row.id),
                page,
            )
            data = [
                DeliveryResponse(
                    id=row.id,
                    event_id=row.event_id,
                    event_type=row.event_type,
                    status=row.status,
                    attempts=row.attempts,
                    next_attempt_at=to_stamp(row.next_attempt_at),
                    last_error=row.last_error,
                    response_status=row.response_status,
                    created_at=to_stamp(row.created_at),
                    delivered_at=to_stamp(row.delivered_at),
                )
                for row in rows
            ]
            return pages.Page(data=data, next_cursor=after)

        @self.app.post("/webhooks/{webhook_id}/test", status_code=202)
        def test_webhook(
            webhook_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> dict:
            """Sends a webhook.test event to this webhook only; its delivery shows in /deliveries."""
            webhook = self.owned(webhook_id, user, db)
            db.emit_event(
                workspace_id=user.workspace_id,
                sandbox_id=None,
                type="webhook.test",
                data=json.dumps({"webhook_id": webhook.id}),
            )
            event = db.latest_workspace_event(workspace_id=user.workspace_id, type="webhook.test")
            assert event is not None
            db.create_webhook_delivery(webhook_id=webhook.id, event_id=event.id)
            return {"event_id": event.id}

    def owned(self, webhook_id: str, user: Member, db: Querier) -> Webhook:
        webhook = db.get_webhook(id=webhook_id)
        if webhook is None or webhook.workspace_id != user.workspace_id:
            raise HTTPException(status_code=404, detail="webhook not found")
        return webhook


def valid_url(url: str):
    try:
        check_url(url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def deliver_due() -> int:
    """Sends the deliveries that are due, as many as BATCH; returns how many it tried."""
    with db_manager.session() as db:
        due = list(db.claim_webhook_deliveries(lease_seconds=LEASE_SECONDS, row_limit=BATCH))
    for delivery in due:
        with db_manager.session() as db:
            webhook = db.get_webhook(id=delivery.webhook_id)
            event = db.get_event(id=delivery.event_id)
        attempts = delivery.attempts + 1
        status, code, error = "failed", None, None
        if webhook is None or event is None or not webhook.enabled:
            error = "the webhook was disabled or removed"
        else:
            body = json.dumps({**event_response(event).model_dump(), "workspace_id": event.workspace_id}).encode()
            timestamp = str(int(time.time()))
            try:
                check_url(webhook.url)
                response = httpx.post(
                    webhook.url,
                    content=body,
                    timeout=TIMEOUT,
                    follow_redirects=False,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Zoo-Webhooks/1",
                        "Zoo-Event-Id": event.id,
                        "Zoo-Event-Type": event.type,
                        "Zoo-Timestamp": timestamp,
                        "Zoo-Signature": sign(decrypt(webhook.secret_ciphertext), timestamp, body),
                    },
                )
                code = response.status_code
                if response.is_success:
                    status = "delivered"
                    late = (datetime.now(UTC) - event.created_at).total_seconds()
                    metrics.event_delivery.record(late, {"channel": "webhook"})
                else:
                    error = f"HTTP {code}: {response.text[:300]}"
            except (httpx.HTTPError, ValueError) as e:
                error = str(e)[:500] or e.__class__.__name__
            if status != "delivered" and attempts <= len(BACKOFF):
                status = "pending"
        retry = stamp(BACKOFF[attempts - 1]) if status == "pending" else stamp()
        with db_manager.session() as db:
            db.finish_webhook_delivery(
                FinishWebhookDeliveryParams(
                    status=status,
                    attempts=attempts,
                    next_attempt_at=retry,
                    last_error=error,
                    response_status=code,
                    id=delivery.id,
                )
            )
        if status == "failed":
            logger.warning("webhook delivery failed", extra={"delivery_id": delivery.id, "error": error})
    return len(due)


async def loop():
    """Every worker sends due deliveries; the lease on each keeps them from sending the same one."""
    while True:
        try:
            while await asyncio.to_thread(deliver_due) == BATCH:
                pass
        except Exception as e:
            logger.error("webhook deliveries failed", extra={"error": repr(e)})
        await asyncio.sleep(POLL_SECONDS)
