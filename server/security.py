import asyncio
import json
import os
import secrets
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from db.async_connection import async_db
from db.connection import db_manager
from db.generated.models import Sandbox
from db.generated.query import CreateAuditLogParams, Querier
from server import kms
from server.auth_api import Member

APP_ACTION = "launch"
REDACTED = "[redacted]"
# shorter values would turn ordinary output into a wall of [redacted]
MIN_REDACT_LENGTH = 6
VNC_PASSWORD = "vnc_password"


def require_secrets_key():
    if not (os.environ.get("ZOO_SECRETS_KEY") or os.environ.get("ZOO_KMS")):
        raise RuntimeError(
            "ZOO_SECRETS_KEY is not set. Set it to a long random string, separate from JWT_SECRET "
            "(e.g. `openssl rand -base64 48`), or point ZOO_KMS at a KMS key; it protects stored secrets, so keep it "
            "safe and stable"
        )


# Envelope encryption: each workspace has its own data key, wrapped by ZOO_SECRETS_KEY or a KMS (server/kms.py) and
# stored in secret_keys. A value is "zk1:<key id>:<Fernet token under that data key>".
ENVELOPE = b"zk1:"
SYSTEM = "system"  # the scope of what belongs to no workspace yet, like a warm-pool sandbox's VNC password
# unwrapped data keys by id, so the KMS is asked once per key per process
_data_keys: dict[str, Fernet] = {}


def _data_key(db: Querier, scope: str) -> tuple[str, Fernet]:
    row = db.get_secret_key_by_scope(scope=scope)
    if row is None:
        db.create_secret_key(id=str(uuid.uuid4()), scope=scope, wrapped=kms.wrap(Fernet.generate_key()))
        row = db.get_secret_key_by_scope(scope=scope)
        assert row is not None
    if row.id not in _data_keys:
        _data_keys[row.id] = Fernet(kms.unwrap(row.wrapped))
    return row.id, _data_keys[row.id]


def _data_key_by_id(key_id: str) -> Fernet:
    if key_id not in _data_keys:
        with db_manager.session() as db:
            row = db.get_secret_key(id=key_id)
        if row is None:
            raise InvalidToken
        _data_keys[key_id] = Fernet(kms.unwrap(row.wrapped))
    return _data_keys[key_id]


def encrypt(value: str, db: Querier, scope: str) -> str:
    """Encrypts with the data key of scope: a workspace id, or SYSTEM."""
    return encrypt_bytes(value.encode(), db, scope).decode()


def decrypt(token: str) -> str:
    return decrypt_bytes(token.encode()).decode()


async def decrypt_async(token: str) -> str:
    if not token.startswith("zk1:"):
        raise InvalidToken
    key_id, encrypted = token[4:].split(":", 1)
    cipher = _data_keys.get(key_id)
    if cipher is None:
        async with async_db.session() as db:
            row = await db.get_secret_key(id=key_id)
        if row is None:
            raise InvalidToken
        cipher = Fernet(await asyncio.to_thread(kms.unwrap, row.wrapped))
        _data_keys[key_id] = cipher
    return cipher.decrypt(encrypted.encode()).decode()


def encrypt_bytes(data: bytes, db: Querier, scope: str) -> bytes:
    key_id, fernet = _data_key(db, scope)
    return ENVELOPE + key_id.encode() + b":" + fernet.encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    key_id, _, rest = token[len(ENVELOPE) :].partition(b":")
    return _data_key_by_id(key_id.decode()).decrypt(rest)


def secret_values(sandbox: Sandbox, db: Querier) -> dict[str, str]:
    # vault secrets first so a sandbox's own secret of the same name wins
    env = {row.name: decrypt(row.ciphertext) for row in db.list_sandbox_vault_secrets(sandbox_id=sandbox.id)}
    for row in db.list_sandbox_secrets(sandbox_id=sandbox.id):
        if row.enabled:
            env[row.name] = decrypt(db.get_sandbox_secret(id=row.id).secret_ref)
    return env


# secret values per sandbox, kept so tool output is redacted without decrypting every secret on every call:
# what it booted with (still in its env after a later change) and what is attached now (cleared on any change)
_booted: dict[str, list[str]] = {}
_attached: dict[str, list[str]] = {}


def remember_secrets(sandbox_id: str, values: list[str]):
    _booted[sandbox_id] = values
    _attached.pop(sandbox_id, None)


def forget_secrets(sandbox_id: str):
    _booted.pop(sandbox_id, None)
    _attached.pop(sandbox_id, None)


def secrets_changed(sandbox_id: str | None = None):
    """Drops cached attached values for one sandbox, or for all after a vault secret (shared by many) changes."""
    if sandbox_id is None:
        _attached.clear()
    else:
        _attached.pop(sandbox_id, None)


def redaction_values(sandbox: Sandbox, db: Querier) -> list[str]:
    """The secret values to mask in a sandbox's tool output."""
    attached = _attached.get(sandbox.id)
    if attached is None:
        attached = _attached[sandbox.id] = [*secret_values(sandbox, db).values()]
        stored = json.loads(sandbox.config or "{}").get(VNC_PASSWORD)
        if stored:
            attached.append(decrypt(stored))
    return [*_booted.get(sandbox.id, []), *attached]


def vnc_password(sandbox: Sandbox) -> str:
    """The password of the sandbox's x11vnc."""
    stored = json.loads(sandbox.config or "{}").get(VNC_PASSWORD)
    if not stored:
        raise RuntimeError("the sandbox has no VNC password; restart the sandbox")
    return decrypt(stored)


def ensure_vnc_password(sandbox: Sandbox, db: Querier) -> str:
    """Makes the sandbox's x11vnc password on its first boot and keeps it, encrypted, in its config. Only the API
    knows it: the viewer proxy logs in with it and offers the browser no-auth."""
    config = json.loads(sandbox.config or "{}")
    if VNC_PASSWORD not in config:
        # VNC authentication only uses the first 8 characters
        config[VNC_PASSWORD] = encrypt(secrets.token_urlsafe(6), db, sandbox.workspace_id)
        db.update_sandbox(name=sandbox.name, resources=sandbox.resources, config=json.dumps(config), id=sandbox.id)
    return decrypt(config[VNC_PASSWORD])


def secret_env(sandbox: Sandbox, db: Querier) -> dict[str, str]:
    for row in db.list_sandbox_vault_secrets(sandbox_id=sandbox.id):
        db.mark_vault_secret_used(id=row.id)
    db.mark_sandbox_vault_secrets_used(sandbox_id=sandbox.id)
    return secret_values(sandbox, db)


def redact(value: Any, secrets: list[str]) -> Any:
    """Masks secret values anywhere in a tool result so they never reach logs or agents."""
    secrets = sorted((s for s in secrets if len(s) >= MIN_REDACT_LENGTH), key=len, reverse=True)
    if not secrets:
        return value

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            for s in secrets:
                v = v.replace(s, REDACTED)
            return v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(walk(x) for x in v)
        return v

    return walk(value)


def rotate(db: Querier) -> dict[str, int]:
    """Rewraps every workspace's data key with the current ZOO_SECRETS_KEY or ZOO_KMS."""
    counts = {"data_keys": 0}
    for row in list(db.list_secret_keys()):
        db.rewrap_secret_key(wrapped=kms.wrap(kms.unwrap(row.wrapped)), id=row.id)
        _data_keys.pop(row.id, None)
        counts["data_keys"] += 1
    return counts


def enforce(sandbox: Sandbox, db: Querier):
    # imported here: the runtime backends import this module (through server.nodes)
    from server.runtime import apply_apps, apply_network

    if sandbox.status != "running" or not sandbox.runtime_id:
        return
    policy = db.get_sandbox_network_policy(sandbox_id=sandbox.id)
    if policy is not None:
        rules = [(r.rule_type, r.value, r.effect) for r in db.list_sandbox_network_rules(policy_id=policy.id)]
        apply_network(sandbox.runtime_id, policy.default_action, bool(policy.allow_dns), rules)
    apply_apps(
        sandbox.runtime_id,
        {
            p.app_slug: p.effect
            for p in db.list_sandbox_app_permissions(sandbox_id=sandbox.id)
            if p.action == APP_ACTION
        },
    )


def audit(
    db: Querier,
    user: Member,
    action: str,
    resource_type: str,
    resource_id: str | None,
    sandbox_id: str | None = None,
    **metadata,
):
    """Records who touched which secret or profile. Never pass a secret value in metadata."""
    db.create_audit_log(
        CreateAuditLogParams(
            id=str(uuid.uuid4()),
            workspace_id=user.workspace_id,
            actor_id=user.id,
            sandbox_id=sandbox_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=json.dumps(metadata),
        )
    )
