# Auth API TODO
# 1. router
# 2. Handler
# 3. Logger
# 4. DB
import hashlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field, field_validator

from db.async_connection import async_db
from db.async_generated.query import EnsureWorkspaceQuotaParams as AsyncQuotaParams
from db.connection import IntegrityError, db_manager
from db.generated.models import User
from db.generated.query import CreateAPIKeyParams, CreateUserParams, Querier
from server import labels, quotas
from server.cache import cache
from server.limits import API_KEY, LOGIN_PER_EMAIL, LOGIN_PER_IP, SIGNUP_PER_IP, client_ip
from server.logger import logger
from server.utils import PaswwordUtils, to_stamp
from server.writer import writer

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = timedelta(hours=24)
API_KEY_PREFIX = "zoo_"


# a role can do what the roles before it can
ROLES = ("viewer", "member", "admin", "owner")
# what a viewer (and a read-only API key) may still POST: looking, not touching
# (a viewer link may only be a view link: server/viewer_api.py)
VIEWER_WRITES = {"/sandboxes/{sandbox_id}/screenshot", "/sandboxes/{sandbox_id}/viewer-links"}
# changes only admins make: hosts, shared secrets and profiles, keys and the warm pool (route
# templates; anything under them). A sandbox's own policies are its members' to set.
ADMIN_WRITES = (
    "/servers",
    "/nodes/tokens",
    "/vault",
    "/profiles",
    "/api-keys",
    "/pool",
    "/kubernetes",
    "/webhooks",
)
# routes that check the caller's role in the workspace they name themselves (server/workspaces_api.py)
SELF_CHECKED = ("/workspaces", "/invitations")
# what a key for one sandbox may reach besides that sandbox's own routes
SANDBOX_KEY_ROUTES = {("GET", "/sandboxes"), ("GET", "/tools"), ("GET", "/auth/me")}
# and a key for the sandboxes with some labels (an end user's): those, making more of them, and their usage and
# events
LABEL_KEY_ROUTES = SANDBOX_KEY_ROUTES | {
    ("POST", "/sandboxes"),
    ("GET", "/usage"),
    ("GET", "/usage/export"),
    ("GET", "/events"),
    ("GET", "/events/stream"),
}


class Member(User):
    """The user behind a request, in the workspace it acts in (X-Zoo-Workspace, else their personal one), with their
    role there. An API key acts in its own workspace, as a member (a viewer when read-only), and maybe for one
    sandbox only."""

    workspace_id: str
    role: str
    key_id: str | None = None
    key_sandbox_id: str | None = None
    # an API key's label selector: it only reaches sandboxes with these labels (server/labels.py)
    key_labels: dict[str, str] | None = None

    def can(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


def require(user: Member, role: str):
    """403 unless the user's role in their workspace is at least `role`."""
    if not user.can(role):
        raise HTTPException(status_code=403, detail=f"this needs the {role} role in the workspace")


def require_sandbox(user: Member, sandbox_id: str):
    """403 when the user acts through an API key for another sandbox."""
    if user.key_sandbox_id is not None and user.key_sandbox_id != sandbox_id:
        raise HTTPException(status_code=403, detail="this API key is for another sandbox")


def personal_workspace(user: User, db: Querier) -> str:
    """The user's own workspace, made the first time it is needed."""
    slug = f"personal-{user.id}"
    workspace = db.get_workspace_by_slug(slug=slug)
    if workspace is not None:
        return workspace.id
    # in a transaction of its own, so the request's other sessions see it at once
    try:
        with db_manager.session() as own:
            made = own.create_workspace(id=str(uuid.uuid4()), name="Personal", slug=slug, created_by=user.id)
            assert made is not None
            own.add_workspace_member(workspace_id=made.id, user_id=user.id, role="owner")
            quotas.ensure(own, made.id)
            return made.id
    except IntegrityError:
        # another request made it first
        with db_manager.session() as own:
            workspace = own.get_workspace_by_slug(slug=slug)
        assert workspace is not None
        return workspace.id


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def sandbox_labels_async(sandbox_id: str) -> dict[str, str]:
    async def load():
        async with async_db.session() as db:
            sandbox = await db.get_sandbox(id=sandbox_id)
            found = {row.key: row.value async for row in db.list_sandbox_labels(sandbox_id=sandbox_id)}
        return {"workspace_id": sandbox.workspace_id if sandbox else None, "labels": found}

    return (await cache.get("sandbox_labels", sandbox_id, load))["labels"]


async def personal_workspace_async(user) -> str:
    slug = f"personal-{user.id}"
    try:
        async with async_db.session() as db:
            workspace = await db.get_workspace_by_slug(slug=slug)
            if workspace is not None:
                return workspace.id
            made = await db.create_workspace(id=str(uuid.uuid4()), name="Personal", slug=slug, created_by=user.id)
            assert made is not None
            await db.add_workspace_member(workspace_id=made.id, user_id=user.id, role="owner")
            await db.ensure_workspace_quota(
                AsyncQuotaParams(
                    workspace_id=made.id,
                    max_running_sandboxes=int(quotas.RUNNING) if quotas.RUNNING else None,
                    max_cpus=float(quotas.CPUS) if quotas.CPUS else None,
                    max_memory_mb=int(quotas.MEMORY) if quotas.MEMORY else None,
                    max_storage_gb=int(quotas.STORAGE) if quotas.STORAGE else None,
                )
            )
            return made.id
    except asyncpg.UniqueViolationError:
        async with async_db.session() as db:
            workspace = await db.get_workspace_by_slug(slug=slug)
            if workspace is None:
                raise
            return workspace.id


class AuthRequst(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    avatar_url: str | None
    # null until the dashboard's first-run steps are done or skipped
    onboarded_at: str | None = None


class ApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # null: every sandbox in the workspace
    sandbox_id: str | None = None
    # or only the sandboxes with these labels, e.g. {"end_user": "alice"}; sandboxes the key makes get them
    label_selector: dict[str, str] | None = None
    read_only: bool = False
    expires_at: datetime | None = None

    @field_validator("label_selector")
    @classmethod
    def check_selector(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is not None and not value:
            raise ValueError("a label selector needs at least one label; leave it out for the whole workspace")
        return labels.validate(value) if value else value


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    sandbox_id: str | None
    label_selector: dict[str, str] | None = None
    read_only: bool
    expires_at: str | None
    last_used_at: str | None
    revoked_at: str | None
    created_at: str


def key_response(row) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        sandbox_id=row.sandbox_id,
        label_selector=labels.parse_selector(row.label_selector),
        read_only=row.read_only,
        expires_at=to_stamp(row.expires_at),
        last_used_at=to_stamp(row.last_used_at),
        revoked_at=to_stamp(row.revoked_at),
        created_at=to_stamp(row.created_at),
    )


class CreatedApiKeyResponse(ApiKeyResponse):
    key: str


class AuthApi:
    def __init__(self, app: APIRouter):
        self.logger = logger
        self.app = app
        self.secret = os.environ.get("JWT_SECRET")
        if not self.secret:
            raise RuntimeError("JWT_SECRET is not set")
        self._register_routes()

    def _register_routes(self):
        self.app.post("/auth/signup", response_model=TokenResponse, status_code=201)(self.register)
        self.app.post("/auth/login", response_model=TokenResponse)(self.login)

        @self.app.get("/auth/me", response_model=UserResponse)
        def me(user: User = Depends(self.current_user)) -> UserResponse:
            return UserResponse(
                id=user.id,
                email=user.email,
                name=user.name,
                avatar_url=user.avatar_url,
                onboarded_at=to_stamp(user.onboarded_at),
            )

        @self.app.put("/auth/me/onboarded", status_code=204)
        def onboarded(user: User = Depends(self.current_user), db: Querier = Depends(db_manager.get_client)):
            """Marks the first-run steps done (or skipped), so the dashboard stops showing them."""
            db.set_user_onboarded(id=user.id)

        @self.app.get("/api-keys", response_model=list[ApiKeyResponse])
        def list_keys(
            user: Member = Depends(self.current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ApiKeyResponse]:
            return [key_response(r) for r in db.list_api_keys_by_workspace(workspace_id=user.workspace_id)]

        @self.app.post("/api-keys", response_model=CreatedApiKeyResponse, status_code=201)
        def create_key(
            payload: ApiKeyRequest,
            user: Member = Depends(self.current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> CreatedApiKeyResponse:
            if payload.sandbox_id is not None and payload.label_selector is not None:
                raise HTTPException(status_code=422, detail="a key is for one sandbox or for labels, not both")
            if payload.sandbox_id is not None:
                sandbox = db.get_sandbox(id=payload.sandbox_id)
                if sandbox is None or sandbox.workspace_id != user.workspace_id or sandbox.status == "deleted":
                    raise HTTPException(status_code=404, detail="sandbox not found")
            if payload.expires_at is not None and payload.expires_at.astimezone(UTC) <= datetime.now(UTC):
                raise HTTPException(status_code=422, detail="expires_at is in the past")
            key = API_KEY_PREFIX + secrets.token_urlsafe(32)
            row = db.create_api_key(
                CreateAPIKeyParams(
                    id=str(uuid.uuid4()),
                    workspace_id=user.workspace_id,
                    created_by=user.id,
                    name=payload.name,
                    key_hash=hash_key(key),
                    key_prefix=key[:12],
                    scopes="[]",
                    expires_at=payload.expires_at,
                    sandbox_id=payload.sandbox_id,
                    read_only=payload.read_only,
                    label_selector=json.dumps(payload.label_selector) if payload.label_selector else None,
                )
            )
            assert row is not None
            self.logger.info("api key created", extra={"user_id": user.id, "key_id": row.id})
            return CreatedApiKeyResponse(key=key, **key_response(row).model_dump())

        @self.app.delete("/api-keys/{key_id}", response_model=list[ApiKeyResponse])
        def revoke_key(
            key_id: str, user: Member = Depends(self.current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[ApiKeyResponse]:
            if not any(r.id == key_id for r in db.list_api_keys_by_workspace(workspace_id=user.workspace_id)):
                raise HTTPException(status_code=404, detail="api key not found")
            db.revoke_api_key(id=key_id)
            return list_keys(user, db)

    def _create_token(self, user_id: str) -> TokenResponse:
        now = datetime.now(UTC)
        claims = {"sub": user_id, "iat": now, "exp": now + ACCESS_TOKEN_TTL}
        token = jwt.encode(claims, self.secret, algorithm=JWT_ALGORITHM)
        return TokenResponse(access_token=token, user_id=user_id)

    def register(
        self, payload: AuthRequst, request: Request, db: Querier = Depends(db_manager.get_client)
    ) -> TokenResponse:
        SIGNUP_PER_IP.check(client_ip(request))
        email = payload.email.lower()
        if db.get_user_by_email(email=email):
            raise HTTPException(status_code=400, detail="user already exists with this email")

        hashed_pwd = PaswwordUtils.hash_pass(payload.password)
        new_user: User | None = db.create_user(
            CreateUserParams(
                id=str(uuid.uuid4()),
                email=email,
                name=payload.name,
                avatar_url=None,
                password=hashed_pwd,
            )
        )
        if new_user is None:
            raise HTTPException(status_code=500, detail="failed to create user")

        self.logger.info("user signed up", extra={"user_id": new_user.id})
        return self._create_token(new_user.id)

    def login(
        self, payload: AuthRequst, request: Request, db: Querier = Depends(db_manager.get_client)
    ) -> TokenResponse:
        email = payload.email.lower()
        LOGIN_PER_IP.check(client_ip(request))
        LOGIN_PER_EMAIL.check(email)
        user = db.get_user_by_email(email=email)
        if user is None or not PaswwordUtils.verify_pass(user.password, payload.password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")

        self.logger.info("user logged in", extra={"user_id": user.id})
        return self._create_token(user.id)

    def user_from_token(self, token: str, db: Querier, workspace: str | None = None) -> Member | None:
        """The member behind a session token or API key, in `workspace` (the personal workspace when None); None
        when the token is no good. 404 when the user isn't an active member of that workspace."""
        if token.startswith(API_KEY_PREFIX):
            key = db.get_api_key_by_hash(key_hash=hash_key(token))
            if key is None:
                return None
            if workspace is not None and workspace != key.workspace_id:
                raise HTTPException(status_code=403, detail="this API key belongs to another workspace")
            API_KEY.check(key.id)
            user = db.get_user(id=key.created_by)
            membership = db.get_workspace_member(workspace_id=key.workspace_id, user_id=key.created_by)
            # a key stops working when the member who made it leaves
            if user is None or membership is None or membership.status != "active":
                return None
            # at most once a minute: every request through a busy key would write otherwise
            if key.last_used_at is None or (datetime.now(UTC) - key.last_used_at).total_seconds() > 60:
                with db_manager.session() as session:
                    session.update_api_key_last_used(id=key.id)
            role = "viewer" if key.read_only or membership.role == "viewer" else "member"
            return Member(
                **user.model_dump(),
                workspace_id=key.workspace_id,
                role=role,
                key_id=key.id,
                key_sandbox_id=key.sandbox_id,
                key_labels=labels.parse_selector(key.label_selector),
            )
        try:
            claims = jwt.decode(token, self.secret, algorithms=[JWT_ALGORITHM])
        except jwt.PyJWTError:
            return None
        sub = claims.get("sub")
        user = db.get_user(id=sub) if isinstance(sub, str) else None
        if user is None:
            return None
        workspace_id = workspace or personal_workspace(user, db)
        membership = db.get_workspace_member(workspace_id=workspace_id, user_id=user.id)
        if membership is None or membership.status != "active":
            raise HTTPException(status_code=404, detail="workspace not found")
        return Member(**user.model_dump(), workspace_id=workspace_id, role=membership.role)

    async def user_from_token_async(self, token: str, workspace: str | None = None) -> Member | None:
        is_key = token.startswith(API_KEY_PREFIX)
        if is_key:
            identity = hash_key(token)
            subject = ""
        else:
            try:
                assert self.secret is not None
                claims = jwt.decode(token, self.secret, algorithms=[JWT_ALGORITHM])
            except jwt.PyJWTError:
                return None
            subject = claims.get("sub")
            if not isinstance(subject, str):
                return None
            identity = subject

        async def load():
            async with async_db.session() as db:
                key = await db.get_api_key_by_hash(key_hash=identity) if is_key else None
                if is_key and key is None:
                    return None
                user = await db.get_user(id=key.created_by if key is not None else subject)
                if user is None:
                    return None
            workspace_id = key.workspace_id if key is not None else workspace or await personal_workspace_async(user)

            async def load_membership():
                async with async_db.session() as db:
                    return await db.get_workspace_member(workspace_id=workspace_id, user_id=user.id)

            membership = await cache.get("membership", f"{workspace_id}:{user.id}", load_membership)
            if membership is None or membership.status != "active":
                return None
            role = (
                ("viewer" if key.read_only or membership.role == "viewer" else "member")
                if key is not None
                else membership.role
            )
            member = Member(
                **user.model_dump(),
                workspace_id=workspace_id,
                role=role,
                key_id=key.id if key is not None else None,
                key_sandbox_id=key.sandbox_id if key is not None else None,
                key_labels=labels.parse_selector(key.label_selector) if key is not None else None,
            )
            if key is not None and (
                key.last_used_at is None or (datetime.now(UTC) - key.last_used_at).total_seconds() > 60
            ):
                writer.enqueue("key_touch", (key.id, datetime.now(UTC)))
            return {"workspace_id": workspace_id, "member": member, "expires_at": key.expires_at if key else None}

        result = await cache.get("api_key" if is_key else "session_member", f"{identity}:{workspace or ''}", load)
        if result is None or result["expires_at"] is not None and result["expires_at"] <= datetime.now(UTC):
            return None
        member = result["member"]
        if is_key:
            if workspace is not None and workspace != member.workspace_id:
                raise HTTPException(status_code=403, detail="this API key belongs to another workspace")
            API_KEY.check(member.key_id)
        return member

    async def current_user(
        self,
        request: Request,
        creds: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
        x_zoo_workspace: str | None = Header(default=None),
    ) -> Member:
        user = await self.user_from_token_async(creds.credentials, x_zoo_workspace or None)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        route = getattr(request.scope.get("route"), "path", request.url.path)
        route = route.removeprefix("/v1")
        method = request.method
        writes = method not in ("GET", "HEAD") and not route.startswith(SELF_CHECKED)
        if writes and not user.can("member") and route not in VIEWER_WRITES:
            raise HTTPException(status_code=403, detail="viewers can look but not change anything")
        if writes and route.startswith(ADMIN_WRITES):
            require(user, "admin")
        if user.key_sandbox_id is not None:
            sandbox_id = request.path_params.get("sandbox_id")
            if sandbox_id is not None:
                require_sandbox(user, sandbox_id)
            elif (method, route) not in SANDBOX_KEY_ROUTES:
                raise HTTPException(status_code=403, detail="this API key only reaches its own sandbox")
        if user.key_labels is not None:
            sandbox_id = request.path_params.get("sandbox_id")
            if sandbox_id is not None:
                # as if it weren't there, like a sandbox in another workspace
                if not labels.matches(await sandbox_labels_async(sandbox_id), user.key_labels):
                    raise HTTPException(status_code=404, detail="sandbox not found")
            elif (method, route) not in LABEL_KEY_ROUTES:
                raise HTTPException(status_code=403, detail="this API key only reaches its labelled sandboxes")
        # for the audit log (server/audit_api.py), which records the request once it has succeeded
        request.state.member = user
        return user
