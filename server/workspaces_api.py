"""Workspaces, their members and invitations.

Every user has a personal workspace they own (server/auth_api.py, personal_workspace) and can make more. Roles, from
least to most: viewer, member, admin, owner (auth_api.ROLES). Admins manage members and viewers; only owners change
or remove admins and owners, and a workspace always keeps at least one owner. Invitations carry a one-time token,
shown once to whoever invites (no email is sent); the invitee accepts it signed in with the invited address.

These routes check the caller's role in the workspace in the path, not the one X-Zoo-Workspace selects, and are for
people only: API keys act inside a workspace, not on it."""

import hashlib
import os
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, Field

from db.connection import db_manager
from db.generated.query import (
    CreateWorkspaceInvitationParams,
    Querier,
    SetWorkspaceQuotaParams,
    UpsertLabelQuotaParams,
)
from server import labels, quotas, sizes
from server.auth_api import ROLES, AuthApi, Member
from utils.time import to_stamp

INVITATION_TTL = timedelta(days=7)
ADMINS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


class WorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    role: str
    personal: bool
    created_at: str
    # what a sandbox without settings of its own gets (null: never)
    default_idle_timeout_minutes: int | None
    default_max_lifetime_minutes: int | None
    # agent steps store the accessibility tree next to the screenshot, for trajectory exports (server/exports.py)
    capture_a11y: bool = True


class TrajectorySettingsRequest(BaseModel):
    capture_a11y: bool


class LifecycleDefaultsRequest(BaseModel):
    default_idle_timeout_minutes: int | None = Field(default=None, gt=0)
    default_max_lifetime_minutes: int | None = Field(default=None, gt=0)


def workspace_response(workspace, role: str) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        role=role,
        personal=workspace.slug.startswith("personal-"),
        created_at=to_stamp(workspace.created_at),
        default_idle_timeout_minutes=workspace.default_idle_timeout_minutes,
        default_max_lifetime_minutes=workspace.default_max_lifetime_minutes,
        capture_a11y=workspace.capture_a11y,
    )


class MemberResponse(BaseModel):
    user_id: str
    email: str
    name: str | None
    role: str
    status: str
    joined_at: str | None


class RoleRequest(BaseModel):
    role: Literal["owner", "admin", "member", "viewer"]


class InvitationRequest(BaseModel):
    email: EmailStr
    role: Literal["admin", "member", "viewer"] = "member"


class InvitationResponse(BaseModel):
    id: str
    email: str
    role: str
    expires_at: str
    created_at: str


class CreatedInvitationResponse(InvitationResponse):
    # shown once; the invitee opens /invite/<token> in the dashboard
    token: str


class QuotaRequest(BaseModel):
    # null: no limit
    max_running_sandboxes: int | None = Field(default=None, ge=0)
    max_cpus: float | None = Field(default=None, ge=0)
    max_memory_mb: int | None = Field(default=None, ge=0)
    max_storage_gb: int | None = Field(default=None, ge=0)


class QuotaResponse(QuotaRequest):
    running_sandboxes: int
    cpus: float
    memory_mb: int
    storage_gb: float


class LabelQuotaRequest(QuotaRequest):
    key: str = Field(pattern=labels.KEY.pattern)
    # one value, or '*' for every value of the key without limits of its own
    value: str = Field(min_length=1, max_length=labels.MAX_VALUE)


class LabelQuotaResponse(LabelQuotaRequest):
    # what the value's sandboxes use now; null for '*'
    running_sandboxes: int | None
    cpus: float | None
    memory_mb: int | None
    storage_gb: float | None


def label_quota_response(db: Querier, workspace_id: str, row) -> LabelQuotaResponse:
    limits = LabelQuotaRequest.model_validate(row, from_attributes=True).model_dump()
    if row.value == labels.ANY:
        return LabelQuotaResponse(**limits, running_sandboxes=None, cpus=None, memory_mb=None, storage_gb=None)
    used = db.label_usage(unsized_gb=sizes.UNSIZED_DISK_GB, key=row.key, value=row.value, workspace_id=workspace_id)
    assert used is not None
    return LabelQuotaResponse(
        **limits,
        running_sandboxes=used.running,
        cpus=used.cpus,
        memory_mb=used.memory_mb,
        storage_gb=round(used.disk_gb + used.snapshot_bytes / (1 << 30), 2),
    )


def quota_response(db: Querier, workspace_id: str) -> QuotaResponse:
    limits = db.get_workspace_quota(workspace_id=workspace_id)
    used = quotas.usage(db, workspace_id)
    return QuotaResponse(
        **(QuotaRequest.model_validate(limits, from_attributes=True).model_dump() if limits else {}),
        running_sandboxes=used.running,
        cpus=used.cpus,
        memory_mb=used.memory_mb,
        storage_gb=round(quotas.storage_gb(used), 2),
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def personal(slug: str) -> bool:
    return slug.startswith("personal-")


def invitation_response(row) -> InvitationResponse:
    return InvitationResponse(
        id=row.id,
        email=row.email,
        role=row.role,
        expires_at=to_stamp(row.expires_at),
        created_at=to_stamp(row.created_at),
    )


class WorkspacesApi:
    def __init__(self, app: FastAPI, auth: AuthApi):
        self.app = app
        self.auth = auth
        self._register_routes()

    def role_in(self, workspace_id: str, user: Member, db: Querier, minimum: str = "viewer") -> str:
        """The user's role in the workspace: 404 when they aren't an active member, 403 below `minimum`."""
        if user.key_id is not None:
            raise HTTPException(status_code=403, detail="API keys can't manage workspaces")
        member = db.get_workspace_member(workspace_id=workspace_id, user_id=user.id)
        if member is None or member.status != "active":
            raise HTTPException(status_code=404, detail="workspace not found")
        if ROLES.index(member.role) < ROLES.index(minimum):
            raise HTTPException(status_code=403, detail=f"this needs the {minimum} role in the workspace")
        return member.role

    def keep_an_owner(self, workspace_id: str, user_id: str, db: Querier):
        """409 when user_id is the workspace's last owner, about to stop being one."""
        target = db.get_workspace_member(workspace_id=workspace_id, user_id=user_id)
        if (
            target is not None
            and target.role == "owner"
            and (db.count_workspace_owners(workspace_id=workspace_id) or 0) <= 1
        ):
            raise HTTPException(status_code=409, detail="a workspace needs at least one owner")

    def members(self, workspace_id: str, db: Querier) -> list[MemberResponse]:
        return [
            MemberResponse(
                user_id=m.user_id,
                email=m.email,
                name=m.name,
                role=m.role,
                status=m.status,
                joined_at=to_stamp(m.joined_at),
            )
            for m in db.list_workspace_members(workspace_id=workspace_id)
        ]

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/workspaces", response_model=list[WorkspaceResponse])
        def list_workspaces(
            user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[WorkspaceResponse]:
            return [workspace_response(w, w.role) for w in db.list_workspaces_by_user(user_id=user.id)]

        @self.app.post("/workspaces", response_model=WorkspaceResponse, status_code=201)
        def create_workspace(
            payload: WorkspaceRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> WorkspaceResponse:
            if user.key_id is not None:
                raise HTTPException(status_code=403, detail="API keys can't manage workspaces")
            name = payload.name.strip()
            base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "workspace"
            workspace = db.create_workspace(
                id=str(uuid.uuid4()), name=name, slug=f"{base}-{secrets.token_hex(3)}", created_by=user.id
            )
            assert workspace is not None
            db.add_workspace_member(workspace_id=workspace.id, user_id=user.id, role="owner")
            quotas.ensure(db, workspace.id)
            return workspace_response(workspace, "owner")

        @self.app.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
        def rename_workspace(
            workspace_id: str,
            payload: WorkspaceRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> WorkspaceResponse:
            role = self.role_in(workspace_id, user, db, "admin")
            workspace = db.get_workspace(id=workspace_id)
            assert workspace is not None
            workspace = db.update_workspace(name=payload.name.strip(), slug=workspace.slug, id=workspace_id)
            assert workspace is not None
            return workspace_response(workspace, role)

        @self.app.put("/workspaces/{workspace_id}/lifecycle", response_model=WorkspaceResponse)
        def set_lifecycle_defaults(
            workspace_id: str,
            payload: LifecycleDefaultsRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> WorkspaceResponse:
            role = self.role_in(workspace_id, user, db, "admin")
            workspace = db.set_workspace_lifecycle(
                default_idle_timeout_minutes=payload.default_idle_timeout_minutes,
                default_max_lifetime_minutes=payload.default_max_lifetime_minutes,
                id=workspace_id,
            )
            assert workspace is not None
            return workspace_response(workspace, role)

        @self.app.delete("/workspaces/{workspace_id}", status_code=204)
        def delete_workspace(
            workspace_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ):
            self.role_in(workspace_id, user, db, "owner")
            workspace = db.get_workspace(id=workspace_id)
            assert workspace is not None
            if personal(workspace.slug):
                raise HTTPException(status_code=409, detail="a personal workspace can't be deleted")
            if db.count_running_sandboxes_by_workspace(workspace_id=workspace_id):
                raise HTTPException(status_code=409, detail="stop the workspace's sandboxes first")
            db.delete_workspace(id=workspace_id)

        @self.app.get("/workspaces/{workspace_id}/members", response_model=list[MemberResponse])
        def list_members(
            workspace_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[MemberResponse]:
            self.role_in(workspace_id, user, db)
            return self.members(workspace_id, db)

        @self.app.patch("/workspaces/{workspace_id}/members/{user_id}", response_model=list[MemberResponse])
        def change_role(
            workspace_id: str,
            user_id: str,
            payload: RoleRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[MemberResponse]:
            mine = self.role_in(workspace_id, user, db, "admin")
            target = db.get_workspace_member(workspace_id=workspace_id, user_id=user_id)
            if target is None:
                raise HTTPException(status_code=404, detail="member not found")
            # admins manage members and viewers; owners manage everyone
            if mine != "owner" and (target.role in ("owner", "admin") or payload.role in ("owner", "admin")):
                raise HTTPException(status_code=403, detail="only owners change admins and owners")
            if payload.role != "owner":
                self.keep_an_owner(workspace_id, user_id, db)
            db.update_workspace_member_role(role=payload.role, workspace_id=workspace_id, user_id=user_id)
            return self.members(workspace_id, db)

        @self.app.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
        def remove_member(
            workspace_id: str,
            user_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ):
            # anyone may leave; removing someone else takes an admin, or an owner for admins and owners
            mine = self.role_in(workspace_id, user, db, "viewer" if user_id == user.id else "admin")
            target = db.get_workspace_member(workspace_id=workspace_id, user_id=user_id)
            if target is None:
                raise HTTPException(status_code=404, detail="member not found")
            if user_id != user.id and mine != "owner" and target.role in ("owner", "admin"):
                raise HTTPException(status_code=403, detail="only owners remove admins and owners")
            workspace = db.get_workspace(id=workspace_id)
            if workspace is not None and personal(workspace.slug) and target.role == "owner":
                raise HTTPException(status_code=409, detail="nobody leaves their personal workspace")
            self.keep_an_owner(workspace_id, user_id, db)
            db.remove_workspace_member(workspace_id=workspace_id, user_id=user_id)

        @self.app.get("/workspaces/{workspace_id}/quota", response_model=QuotaResponse)
        def get_quota(
            workspace_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> QuotaResponse:
            self.role_in(workspace_id, user, db)
            return quota_response(db, workspace_id)

        @self.app.put("/workspaces/{workspace_id}/quota", response_model=QuotaResponse)
        def set_quota(
            workspace_id: str,
            payload: QuotaRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> QuotaResponse:
            # quotas are the install's to set, not the workspace's own admins'
            if user.key_id is not None or user.email.lower() not in ADMINS:
                raise HTTPException(status_code=403, detail="only Zoo's admins (ADMIN_EMAILS) set quotas")
            if db.get_workspace(id=workspace_id) is None:
                raise HTTPException(status_code=404, detail="workspace not found")
            db.set_workspace_quota(SetWorkspaceQuotaParams(workspace_id=workspace_id, **payload.model_dump()))
            return quota_response(db, workspace_id)

        @self.app.get("/workspaces/{workspace_id}/label-quotas", response_model=list[LabelQuotaResponse])
        def list_label_quotas(
            workspace_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[LabelQuotaResponse]:
            self.role_in(workspace_id, user, db)
            return [label_quota_response(db, workspace_id, r) for r in db.list_label_quotas(workspace_id=workspace_id)]

        @self.app.put("/workspaces/{workspace_id}/label-quotas", response_model=LabelQuotaResponse)
        def set_label_quota(
            workspace_id: str,
            payload: LabelQuotaRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> LabelQuotaResponse:
            # how a workspace shares out what it has among its end users is its admins' call
            self.role_in(workspace_id, user, db, "admin")
            row = db.upsert_label_quota(UpsertLabelQuotaParams(workspace_id=workspace_id, **payload.model_dump()))
            assert row is not None
            return label_quota_response(db, workspace_id, row)

        @self.app.delete("/workspaces/{workspace_id}/label-quotas", status_code=204)
        def remove_label_quota(
            workspace_id: str,
            key: str,
            value: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ):
            self.role_in(workspace_id, user, db, "admin")
            db.delete_label_quota(workspace_id=workspace_id, key=key, value=value)

        @self.app.put("/workspaces/{workspace_id}/trajectories", response_model=WorkspaceResponse)
        def set_trajectory_settings(
            workspace_id: str,
            payload: TrajectorySettingsRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> WorkspaceResponse:
            role = self.role_in(workspace_id, user, db, "admin")
            workspace = db.set_workspace_capture_a11y(capture_a11y=payload.capture_a11y, id=workspace_id)
            assert workspace is not None
            return workspace_response(workspace, role)

        @self.app.get("/workspaces/{workspace_id}/invitations", response_model=list[InvitationResponse])
        def list_invitations(
            workspace_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[InvitationResponse]:
            self.role_in(workspace_id, user, db, "admin")
            return [invitation_response(i) for i in db.list_workspace_invitations(workspace_id=workspace_id)]

        @self.app.post(
            "/workspaces/{workspace_id}/invitations", response_model=CreatedInvitationResponse, status_code=201
        )
        def invite(
            workspace_id: str,
            payload: InvitationRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> CreatedInvitationResponse:
            mine = self.role_in(workspace_id, user, db, "admin")
            if payload.role == "admin" and mine != "owner":
                raise HTTPException(status_code=403, detail="only owners invite admins")
            invitee = db.get_user_by_email(email=payload.email.lower())
            if invitee is not None:
                existing = db.get_workspace_member(workspace_id=workspace_id, user_id=invitee.id)
                if existing is not None and existing.status == "active":
                    raise HTTPException(status_code=409, detail=f"{payload.email} is already a member")
            token = secrets.token_urlsafe(32)
            row = db.create_workspace_invitation(
                CreateWorkspaceInvitationParams(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    email=payload.email.lower(),
                    role=payload.role,
                    invited_by=user.id,
                    token_hash=hash_token(token),
                    expires_at=datetime.now(UTC) + INVITATION_TTL,
                )
            )
            assert row is not None
            return CreatedInvitationResponse(token=token, **invitation_response(row).model_dump())

        @self.app.delete("/workspaces/{workspace_id}/invitations/{invitation_id}", status_code=204)
        def revoke_invitation(
            workspace_id: str,
            invitation_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ):
            self.role_in(workspace_id, user, db, "admin")
            if db.revoke_workspace_invitation(id=invitation_id, workspace_id=workspace_id) is None:
                raise HTTPException(status_code=404, detail="invitation not found")

        @self.app.post("/invitations/{token}/accept", response_model=WorkspaceResponse)
        def accept(
            token: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> WorkspaceResponse:
            if user.key_id is not None:
                raise HTTPException(status_code=403, detail="API keys can't accept invitations")
            invitation = db.get_workspace_invitation_by_token(token_hash=hash_token(token))
            if invitation is None:
                raise HTTPException(status_code=404, detail="this invitation is invalid, used or expired")
            if invitation.email.lower() != user.email.lower():
                raise HTTPException(status_code=403, detail=f"this invitation is for {invitation.email}")
            existing = db.get_workspace_member(workspace_id=invitation.workspace_id, user_id=user.id)
            if existing is not None and existing.status == "active":
                raise HTTPException(status_code=409, detail="you're already a member")
            if db.accept_workspace_invitation(id=invitation.id) is None:
                raise HTTPException(status_code=404, detail="this invitation is invalid, used or expired")
            if existing is not None:
                db.remove_workspace_member(workspace_id=invitation.workspace_id, user_id=user.id)
            db.add_workspace_member(workspace_id=invitation.workspace_id, user_id=user.id, role=invitation.role)
            workspace = db.get_workspace(id=invitation.workspace_id)
            assert workspace is not None
            return workspace_response(workspace, invitation.role)
