import ipaddress
import re
import uuid
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from apps.server import vault_sync
from apps.server.auth_api import AuthApi, Member
from apps.server.logger import logger
from apps.server.registry import PERMISSIONS, TOOLS
from apps.server.sandbox_api import SandboxApi
from apps.server.security import APP_ACTION, encrypt, enforce, secrets_changed
from apps.server.utils import to_stamp
from db.connection import db_manager
from db.generated.models import Sandbox, SandboxNetworkPolicy
from db.generated.query import (
    CreateAppParams,
    CreateSandboxNetworkRuleParams,
    CreateSandboxSecretParams,
    GrantSandboxAppPermissionParams,
    Querier,
    UpsertSandboxPermissionParams,
)

Effect = Literal["allow", "deny"]


class PermissionResponse(BaseModel):
    permission: str
    action: str
    label: str
    effect: Effect


class PermissionRequest(BaseModel):
    permission: str
    action: str
    effect: Effect


class NetworkRuleResponse(BaseModel):
    id: str
    rule_type: str
    value: str
    effect: Effect


class NetworkResponse(BaseModel):
    default_action: Effect
    allow_dns: bool
    rules: list[NetworkRuleResponse]


class NetworkPolicyRequest(BaseModel):
    default_action: Effect
    allow_dns: bool


DOMAIN = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.?$")


class NetworkRuleRequest(BaseModel):
    rule_type: Literal["domain", "ip", "cidr"]
    value: str = Field(min_length=1, max_length=253)
    effect: Effect

    @model_validator(mode="after")
    def valid_value(self):
        # the host's egress daemon shuts a sandbox off entirely over a rule it can't read
        self.value = self.value.strip().lower()
        try:
            if self.rule_type == "ip":
                ipaddress.ip_address(self.value)
            elif self.rule_type == "cidr":
                ipaddress.ip_network(self.value, strict=False)
            elif not DOMAIN.match(self.value):
                raise ValueError
        except ValueError:
            raise ValueError(f"{self.value!r} is not a valid {self.rule_type}") from None
        return self


class AppResponse(BaseModel):
    name: str
    binary: str
    effect: Effect


class AppPermissionRequest(BaseModel):
    effect: Effect


class SecretRequest(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    value: str = Field(min_length=1, max_length=8192)


class SecretResponse(BaseModel):
    id: str
    name: str
    enabled: bool
    created_at: str


class SandboxPolicyApi:
    def __init__(self, app: APIRouter, auth: AuthApi, sandboxes: SandboxApi):
        self.logger = logger
        self.app = app
        self.auth = auth
        self.sandboxes = sandboxes
        self._register_routes()

    def _register_routes(self):
        current_user = self.auth.current_user

        @self.app.get("/sandboxes/{sandbox_id}/permissions", response_model=list[PermissionResponse])
        def list_permissions(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[PermissionResponse]:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            return self._permissions(sandbox, db)

        @self.app.put("/sandboxes/{sandbox_id}/permissions", response_model=list[PermissionResponse])
        def set_permission(
            sandbox_id: str,
            payload: PermissionRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[PermissionResponse]:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            if (payload.permission, payload.action) not in PERMISSIONS:
                raise HTTPException(status_code=422, detail="unknown permission")
            db.upsert_sandbox_permission(
                UpsertSandboxPermissionParams(
                    id=str(uuid.uuid4()),
                    sandbox_id=sandbox.id,
                    permission=payload.permission,
                    action=payload.action,
                    effect=payload.effect,
                    rules="{}",
                )
            )
            return self._permissions(sandbox, db)

        @self.app.get("/sandboxes/{sandbox_id}/network", response_model=NetworkResponse)
        def get_network(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> NetworkResponse:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            return self._network(sandbox, db)

        @self.app.put("/sandboxes/{sandbox_id}/network", response_model=NetworkResponse)
        def set_network(
            sandbox_id: str,
            payload: NetworkPolicyRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> NetworkResponse:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            db.upsert_sandbox_network_policy(
                id=str(uuid.uuid4()),
                sandbox_id=sandbox.id,
                default_action=payload.default_action,
                allow_dns=payload.allow_dns,
            )
            return self._enforced(sandbox, db, self._network)

        @self.app.post("/sandboxes/{sandbox_id}/network/rules", response_model=NetworkResponse, status_code=201)
        def add_rule(
            sandbox_id: str,
            payload: NetworkRuleRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> NetworkResponse:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            policy = self._policy(sandbox, db)
            db.create_sandbox_network_rule(
                CreateSandboxNetworkRuleParams(
                    id=str(uuid.uuid4()),
                    policy_id=policy.id,
                    rule_type=payload.rule_type,
                    value=payload.value.strip().lower(),
                    effect=payload.effect,
                )
            )
            return self._enforced(sandbox, db, self._network)

        @self.app.delete("/sandboxes/{sandbox_id}/network/rules/{rule_id}", response_model=NetworkResponse)
        def delete_rule(
            sandbox_id: str,
            rule_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> NetworkResponse:
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            policy = db.get_sandbox_network_policy(sandbox_id=sandbox.id)
            rule = db.get_sandbox_network_rule(id=rule_id)
            if policy is None or rule is None or rule.policy_id != policy.id:
                raise HTTPException(status_code=404, detail="rule not found")
            db.delete_sandbox_network_rule(id=rule.id)
            return self._enforced(sandbox, db, self._network)

        @self.app.get("/sandboxes/{sandbox_id}/secrets", response_model=list[SecretResponse])
        def list_secrets(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[SecretResponse]:
            return self._secrets(self.sandboxes.owned(sandbox_id, user, db), db)

        @self.app.put("/sandboxes/{sandbox_id}/secrets", response_model=list[SecretResponse])
        def set_secret(
            sandbox_id: str,
            payload: SecretRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[SecretResponse]:
            secrets_changed()
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            existing = db.get_sandbox_secret_by_name(sandbox_id=sandbox.id, name=payload.name)
            if existing is not None:
                db.delete_sandbox_secret(id=existing.id)
            db.create_sandbox_secret(
                CreateSandboxSecretParams(
                    id=str(uuid.uuid4()),
                    sandbox_id=sandbox.id,
                    name=payload.name,
                    secret_ref=encrypt(payload.value, db, sandbox.workspace_id),
                    injection_config='{"type": "env"}',
                    enabled=True,
                )
            )
            vault_sync.push(vault_sync.plan(db, [sandbox.id]))
            return self._secrets(sandbox, db)

        @self.app.delete("/sandboxes/{sandbox_id}/secrets/{secret_id}", response_model=list[SecretResponse])
        def delete_secret(
            sandbox_id: str,
            secret_id: str,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[SecretResponse]:
            secrets_changed()
            sandbox = self.sandboxes.owned(sandbox_id, user, db)
            secret = db.get_sandbox_secret(id=secret_id)
            if secret is None or secret.sandbox_id != sandbox.id:
                raise HTTPException(status_code=404, detail="secret not found")
            db.delete_sandbox_secret(id=secret.id)
            vault_sync.push(vault_sync.plan(db, [sandbox.id]))
            return self._secrets(sandbox, db)

        @self.app.get("/sandboxes/{sandbox_id}/apps", response_model=list[AppResponse])
        def list_apps(
            sandbox_id: str, user: Member = Depends(current_user), db: Querier = Depends(db_manager.get_client)
        ) -> list[AppResponse]:
            sandbox = self.sandboxes.running(sandbox_id, user, db)
            return self._apps(sandbox, db)

        @self.app.put("/sandboxes/{sandbox_id}/apps/{binary}", response_model=list[AppResponse])
        def set_app(
            sandbox_id: str,
            binary: str,
            payload: AppPermissionRequest,
            user: Member = Depends(current_user),
            db: Querier = Depends(db_manager.get_client),
        ) -> list[AppResponse]:
            sandbox = self.sandboxes.running(sandbox_id, user, db)
            installed = {a["binary"]: a for a in TOOLS["installed_apps"].call(sandbox.runtime_id)["gui_apps"]}
            if binary not in installed:
                raise HTTPException(status_code=404, detail="app not installed in this sandbox")

            app = next(
                (a for a in db.list_apps_by_workspace(workspace_id=sandbox.workspace_id) if a.slug == binary), None
            )
            if app is None:
                app = db.create_app(
                    CreateAppParams(
                        id=str(uuid.uuid4()),
                        workspace_id=sandbox.workspace_id,
                        name=installed[binary]["name"],
                        slug=binary,
                        description=None,
                        install_config="{}",
                    )
                )
            db.grant_sandbox_app_permission(
                GrantSandboxAppPermissionParams(
                    id=str(uuid.uuid4()),
                    sandbox_id=sandbox.id,
                    app_id=app.id,
                    action=APP_ACTION,
                    effect=payload.effect,
                )
            )
            return self._enforced(sandbox, db, self._apps)

    def _secrets(self, sandbox: Sandbox, db: Querier) -> list[SecretResponse]:
        return [
            SecretResponse(id=r.id, name=r.name, enabled=bool(r.enabled), created_at=to_stamp(r.created_at))
            for r in db.list_sandbox_secrets(sandbox_id=sandbox.id)
        ]

    def _enforced(self, sandbox: Sandbox, db: Querier, render):
        try:
            enforce(db.get_sandbox(id=sandbox.id), db)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"policy could not be applied: {e}")
        return render(sandbox, db)

    def _permissions(self, sandbox: Sandbox, db: Querier) -> list[PermissionResponse]:
        stored = {
            (p.permission, p.action): cast(Effect, p.effect) for p in db.list_sandbox_permissions(sandbox_id=sandbox.id)
        }
        return [
            PermissionResponse(permission=p, action=a, label=label, effect=cast(Effect, stored.get((p, a), "allow")))
            for (p, a), label in PERMISSIONS.items()
        ]

    def _policy(self, sandbox: Sandbox, db: Querier) -> SandboxNetworkPolicy:
        policy = db.get_sandbox_network_policy(sandbox_id=sandbox.id)
        if policy is None:
            policy = db.upsert_sandbox_network_policy(
                id=str(uuid.uuid4()), sandbox_id=sandbox.id, default_action="allow", allow_dns=True
            )
        return policy

    def _network(self, sandbox: Sandbox, db: Querier) -> NetworkResponse:
        policy = db.get_sandbox_network_policy(sandbox_id=sandbox.id)
        if policy is None:
            return NetworkResponse(default_action="allow", allow_dns=True, rules=[])
        rules = [
            NetworkRuleResponse(id=r.id, rule_type=r.rule_type, value=r.value, effect=cast(Effect, r.effect))
            for r in db.list_sandbox_network_rules(policy_id=policy.id)
        ]
        return NetworkResponse(
            default_action=cast(Effect, policy.default_action), allow_dns=bool(policy.allow_dns), rules=rules
        )

    def _apps(self, sandbox: Sandbox, db: Querier) -> list[AppResponse]:
        stored = {
            p.app_slug: cast(Effect, p.effect)
            for p in db.list_sandbox_app_permissions(sandbox_id=sandbox.id)
            if p.action == APP_ACTION
        }
        apps = TOOLS["installed_apps"].call(sandbox.runtime_id or "")["gui_apps"]
        seen = {}
        for a in apps:
            if a["binary"] and a["binary"] not in seen:
                seen[a["binary"]] = AppResponse(
                    name=a["name"],
                    binary=a["binary"],
                    effect=cast(Effect, stored.get(a["binary"], "allow")),
                )
        return sorted(seen.values(), key=lambda a: a.name.lower())
