"""The vault: a user's secrets, attached to any of their sandboxes as environment variables.

Changes reach running sandboxes right away (server/vault_sync.py). Each secret shows where it's used and when each
sandbox last received it, and can carry an expiry date and a rotation interval; secrets that are expiring or due for
rotation are flagged in the list, at /vault/reminders and in the worker's log.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.server import vault_sync
from apps.server.auth_api import AuthApi, Member
from apps.server.logger import logger
from apps.server.sandbox_api import SandboxApi
from apps.server.security import audit, encrypt, secrets_changed
from apps.server.utils import to_stamp
from db.connection import db_manager
from db.generated.models import VaultSecret
from db.generated.query import CreateVaultSecretParams, ListVaultSecretsByWorkspaceRow, Querier

NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
ACTIVITY_LIMIT = 100
# how far ahead an expiry or a rotation shows up as a reminder
EXPIRY_WARNING = timedelta(days=14)
ROTATION_WARNING = timedelta(days=7)

Status = Literal["ok", "rotation_soon", "expiring_soon", "rotation_due", "expired"]
MESSAGES = {
    "rotation_soon": "is due for rotation soon",
    "expiring_soon": "expires soon",
    "rotation_due": "is overdue for rotation",
    "expired": "has expired",
}


class VaultSecretRequest(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    value: str = Field(min_length=1, max_length=8192)
    description: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None
    rotate_every_days: int | None = Field(default=None, ge=1, le=3650)


class VaultSecretUpdate(BaseModel):
    # left out, a field stays as it is; the value can be replaced but never read back. expires_at and
    # rotate_every_days are cleared with null.
    value: str | None = Field(default=None, min_length=1, max_length=8192)
    description: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None
    rotate_every_days: int | None = Field(default=None, ge=1, le=3650)


class SandboxRef(BaseModel):
    id: str
    name: str
    status: str
    # when this sandbox last received the secret: at boot or by a live update
    last_used_at: str | None


class VaultSecretResponse(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: str
    updated_at: str
    last_used_at: str | None
    expires_at: str | None
    rotate_every_days: int | None
    rotated_at: str | None
    rotation_due_at: str | None
    status: Status
    sandboxes: list[SandboxRef]


class ReminderResponse(BaseModel):
    id: str
    name: str
    status: Status
    due_at: str
    message: str


class AttachedSecretResponse(BaseModel):
    id: str
    name: str


class ActivityResponse(BaseModel):
    id: str
    action: str
    resource_type: str
    resource_id: str | None
    sandbox_id: str | None
    metadata: dict
    created_at: str


def rotation_due(secret: VaultSecret | ListVaultSecretsByWorkspaceRow) -> datetime | None:
    """When the secret's next rotation is due, or None."""
    if not secret.rotate_every_days:
        return None
    return (secret.rotated_at or secret.created_at) + timedelta(days=secret.rotate_every_days)


def status_of(
    secret: VaultSecret | ListVaultSecretsByWorkspaceRow, now: datetime | None = None
) -> tuple[Status, datetime | None]:
    """The secret's most pressing reminder and when it's due, or ("ok", None)."""
    now = now or datetime.now(UTC)
    due_at = rotation_due(secret)
    if secret.expires_at is not None and secret.expires_at <= now:
        return "expired", secret.expires_at
    if due_at is not None and due_at <= now:
        return "rotation_due", due_at
    if secret.expires_at is not None and secret.expires_at <= now + EXPIRY_WARNING:
        return "expiring_soon", secret.expires_at
    if due_at is not None and due_at <= now + ROTATION_WARNING:
        return "rotation_soon", due_at
    return "ok", None


# secret id -> the reminder last logged for it, so the hourly check logs each change once
_reminded: dict[str, Status] = {}


def remind():
    """Logs the vault secrets that are expiring or due for rotation. Runs hourly in the worker."""
    with db_manager.session() as db:
        secrets = list(db.list_all_vault_secrets())
    for secret in secrets:
        status, due_at = status_of(secret)
        if status == "ok" or _reminded.get(secret.id) == status:
            if status == "ok":
                _reminded.pop(secret.id, None)
            continue
        _reminded[secret.id] = status
        logger.warning(
            f"vault secret {secret.name} {MESSAGES[status]}",
            extra={"secret_id": secret.id, "user_id": secret.user_id, "status": status, "due_at": due_at},
        )


class VaultApi:
    def __init__(self, app: APIRouter, auth: AuthApi, sandboxes: SandboxApi):
        self.app = app
        self.auth = auth
        self.sandboxes = sandboxes
        self._register_routes()

    def secret(self, secret_id: str, user: Member, db: Querier) -> VaultSecret:
        secret = db.get_vault_secret(id=secret_id)
        if secret is None or secret.workspace_id != user.workspace_id:
            raise HTTPException(status_code=404, detail="secret not found")
        return secret

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/vault/secrets", response_model=list[VaultSecretResponse])
        def list_secrets(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[VaultSecretResponse]:
            return self._secrets(user, db)

        @self.app.get("/vault/reminders", response_model=list[ReminderResponse])
        def reminders(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ReminderResponse]:
            found = []
            for secret in db.list_vault_secrets_by_workspace(workspace_id=user.workspace_id):
                status, due_at = status_of(secret)
                if status != "ok" and due_at is not None:
                    message = f"{secret.name} {MESSAGES[status]}"
                    found.append(
                        ReminderResponse(
                            id=secret.id,
                            name=secret.name,
                            status=status,
                            due_at=to_stamp(due_at),
                            message=message,
                        )
                    )
            return sorted(found, key=lambda r: r.due_at)

        @self.app.post("/vault/secrets", response_model=list[VaultSecretResponse], status_code=201)
        def create_secret(
            payload: VaultSecretRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[VaultSecretResponse]:
            if db.get_vault_secret_by_name(workspace_id=user.workspace_id, name=payload.name) is not None:
                raise HTTPException(status_code=409, detail=f"{payload.name} already exists; update it instead")
            secret = db.create_vault_secret(
                CreateVaultSecretParams(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    name=payload.name,
                    description=(payload.description or "").strip() or None,
                    ciphertext=encrypt(payload.value, db, user.workspace_id),
                    workspace_id=user.workspace_id,
                )
            )
            assert secret is not None
            if payload.expires_at is not None or payload.rotate_every_days is not None:
                db.set_vault_secret_schedule(
                    expires_at=payload.expires_at or None,
                    rotate_every_days=payload.rotate_every_days,
                    id=secret.id,
                )
            audit(db, user, "secret.create", "secret", secret.id, name=secret.name)
            return self._secrets(user, db)

        @self.app.patch("/vault/secrets/{secret_id}", response_model=list[VaultSecretResponse])
        def update_secret(
            secret_id: str,
            payload: VaultSecretUpdate,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[VaultSecretResponse]:
            secrets_changed()
            secret = self.secret(secret_id, user, db)
            if payload.value is not None:
                db.update_vault_secret_value(ciphertext=encrypt(payload.value, db, user.workspace_id), id=secret.id)
                audit(db, user, "secret.rotate", "secret", secret.id, name=secret.name)
                vault_sync.push(vault_sync.plan(db, self._users_of(secret.id, db)))
            if payload.description is not None:
                db.update_vault_secret_description(description=payload.description.strip() or None, id=secret.id)
                audit(db, user, "secret.describe", "secret", secret.id, name=secret.name)
            fields = payload.model_fields_set
            if "expires_at" in fields or "rotate_every_days" in fields:
                expires_at = secret.expires_at
                if "expires_at" in fields:
                    expires_at = payload.expires_at or None
                every = payload.rotate_every_days if "rotate_every_days" in fields else secret.rotate_every_days
                db.set_vault_secret_schedule(expires_at=expires_at, rotate_every_days=every, id=secret.id)
                audit(
                    db,
                    user,
                    "secret.schedule",
                    "secret",
                    secret.id,
                    name=secret.name,
                    expires_at=to_stamp(expires_at),
                    rotate_every_days=every,
                )
            return self._secrets(user, db)

        @self.app.delete("/vault/secrets/{secret_id}", response_model=list[VaultSecretResponse])
        def delete_secret(
            secret_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[VaultSecretResponse]:
            secrets_changed()
            secret = self.secret(secret_id, user, db)
            users = self._users_of(secret.id, db)
            db.detach_vault_secret_everywhere(secret_id=secret.id)
            db.delete_vault_secret(id=secret.id)
            audit(db, user, "secret.delete", "secret", secret.id, name=secret.name)
            vault_sync.push(vault_sync.plan(db, users))
            return self._secrets(user, db)

        @self.app.get("/sandboxes/{sandbox_id}/vault-secrets", response_model=list[AttachedSecretResponse])
        def attached_secrets(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[AttachedSecretResponse]:
            return self._attached(self.sandboxes.owned(sandbox_id, user, db).id, db)

        @self.app.put("/sandboxes/{sandbox_id}/vault-secrets/{secret_id}", response_model=list[AttachedSecretResponse])
        def attach_secret(
            sandbox_id: str,
            secret_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[AttachedSecretResponse]:
            secrets_changed()
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            secret = self.secret(secret_id, user, db)
            db.attach_vault_secret(sandbox_id=sandbox.id, secret_id=secret.id)
            audit(db, user, "secret.attach", "secret", secret.id, sandbox.id, name=secret.name, sandbox=sandbox.name)
            vault_sync.push(vault_sync.plan(db, [sandbox.id]))
            return self._attached(sandbox.id, db)

        @self.app.delete(
            "/sandboxes/{sandbox_id}/vault-secrets/{secret_id}", response_model=list[AttachedSecretResponse]
        )
        def detach_secret(
            sandbox_id: str,
            secret_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[AttachedSecretResponse]:
            secrets_changed()
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            secret = self.secret(secret_id, user, db)
            db.detach_vault_secret(sandbox_id=sandbox.id, secret_id=secret.id)
            audit(db, user, "secret.detach", "secret", secret.id, sandbox.id, name=secret.name, sandbox=sandbox.name)
            vault_sync.push(vault_sync.plan(db, [sandbox.id]))
            return self._attached(sandbox.id, db)

        @self.app.get("/vault/activity", response_model=list[ActivityResponse])
        def activity(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ActivityResponse]:
            return [
                ActivityResponse(
                    id=a.id,
                    action=a.action,
                    resource_type=a.resource_type,
                    resource_id=a.resource_id,
                    sandbox_id=a.sandbox_id,
                    metadata=json.loads(a.metadata or "{}"),
                    created_at=to_stamp(a.created_at),
                )
                for a in db.list_vault_audit_logs(workspace_id=user.workspace_id, limit=ACTIVITY_LIMIT)
            ]

    def _users_of(self, secret_id: str, db: Querier) -> list[str]:
        return [row.sandbox_id for row in db.list_vault_secret_sandboxes(secret_id=secret_id)]

    def _secrets(self, user: Member, db: Querier) -> list[VaultSecretResponse]:
        grants: dict[str, list[SandboxRef]] = {}
        for g in db.list_vault_grants_by_workspace(workspace_id=user.workspace_id):
            grants.setdefault(g.secret_id, []).append(
                SandboxRef(
                    id=g.sandbox_id,
                    name=g.sandbox_name,
                    status=g.sandbox_status,
                    last_used_at=to_stamp(g.last_used_at),
                )
            )
        responses = []
        for s in db.list_vault_secrets_by_workspace(workspace_id=user.workspace_id):
            status, _ = status_of(s)
            responses.append(
                VaultSecretResponse(
                    id=s.id,
                    name=s.name,
                    description=s.description,
                    created_at=to_stamp(s.created_at),
                    updated_at=to_stamp(s.updated_at),
                    last_used_at=to_stamp(s.last_used_at),
                    expires_at=to_stamp(s.expires_at),
                    rotate_every_days=s.rotate_every_days,
                    rotated_at=to_stamp(s.rotated_at),
                    rotation_due_at=to_stamp(rotation_due(s)),
                    status=status,
                    sandboxes=grants.get(s.id, []),
                )
            )
        return responses

    def _attached(self, sandbox_id: str, db: Querier) -> list[AttachedSecretResponse]:
        return [
            AttachedSecretResponse(id=r.id, name=r.name) for r in db.list_sandbox_vault_secrets(sandbox_id=sandbox_id)
        ]
