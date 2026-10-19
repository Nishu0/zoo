"""Applies secret changes to running sandboxes without a restart.

When a vault secret is changed, attached, detached or deleted, or a sandbox's own secret is, every running sandbox
that uses it gets its new set of secrets through its guest agent, and the next command it runs sees them:

- Linux: the guest reads /run/zoo/env.json for every command; containers carry no secrets in their own environment,
  so a removed secret is gone too.
- macOS and Windows: every command sources ~/.zoo/env (env.ps1), which is rewritten.

Programs already running keep the environment they started with; only new commands and terminals see the change.
"""

import json
import queue
import threading
from collections.abc import Iterable
from dataclasses import dataclass

from db.generated.query import Querier
from server import metrics
from server.guest import hub
from server.logger import logger
from server.runtime import SECRETS_DIR, VMS, write_secrets
from server.security import secret_values, secrets_changed


@dataclass(frozen=True)
class Push:
    sandbox_id: str
    kind: str
    runtime_id: str
    values: dict[str, str]


def plan(db: Querier, sandbox_ids: Iterable[str]) -> list[Push]:
    """What to push to each running sandbox, read in the caller's transaction so it sees the change being made."""
    pushes = []
    for sandbox_id in dict.fromkeys(sandbox_ids):
        sandbox = db.get_sandbox(id=sandbox_id)
        if sandbox is None or sandbox.status != "running" or not sandbox.runtime_id:
            continue
        pushes.append(Push(sandbox.id, sandbox.kind, sandbox.runtime_id, secret_values(sandbox, db)))
        db.mark_sandbox_vault_secrets_used(sandbox_id=sandbox.id)
    return pushes


def deliver(push: Push):
    if push.kind in VMS:
        VMS[push.kind].write_env(push.runtime_id, push.values)
        return
    guest = hub.for_sandbox(push.sandbox_id)
    if guest is not None and guest.has("files"):
        guest.call("write", {"path": f"{SECRETS_DIR}/env.json", "mode": 0o600}, json.dumps(push.values).encode())
    else:
        write_secrets(push.runtime_id, push.values)


def push_now(pushes: list[Push]) -> list[tuple[str, str]]:
    """Delivers each push; returns (sandbox id, error) for the ones that failed."""
    failed = []
    for push in pushes:
        secrets_changed(push.sandbox_id)
        try:
            deliver(push)
            logger.info("secrets updated in sandbox", extra={"sandbox_id": push.sandbox_id, "count": len(push.values)})
        except Exception as e:
            metrics.error("vault")
            logger.error("secret update failed", extra={"sandbox_id": push.sandbox_id, "error": repr(e)})
            failed.append((push.sandbox_id, str(e)))
    return failed


# tests deliver in the request, so they see the result right away
inline = False
_queue: "queue.Queue[list[Push]]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


def _drain():
    while True:
        push_now(_queue.get())


def push(pushes: list[Push]):
    """Delivers in the background, one change after another so a later change always lands last. A sandbox that
    can't be reached gets its secrets on its next boot."""
    global _worker
    if not pushes:
        return
    if inline:
        push_now(pushes)
        return
    with _lock:
        if _worker is None:
            _worker = threading.Thread(target=_drain, name="vault-push", daemon=True)
            _worker.start()
    _queue.put(pushes)
