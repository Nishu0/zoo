"""The warm pool: Linux sandboxes booted ahead of a create, with no owner, home data or secrets.

Each pooled container is booted under the id its sandbox will have, so its name, home volume and guest token are
already the sandbox's. A create claims one by taking that id and hands it over in the request: the secrets where
the guest reads them (/run/zoo/env.json), the owner's policy (a pooled one has no network until then) and profiles,
in parallel, and the sandbox is running when the create returns. Only new sandboxes are served: starting a stopped one still boots
a fresh container on its volume. On Kubernetes, pooled pods wait with their processes stopped (kube.pause).
"""

import asyncio
import json
import math
import secrets
import threading
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from db.connection import db_manager
from db.generated.models import PoolSandbox
from db.generated.query import CreatePoolSandboxParams, Querier
from logger.logger import logger
from server import metrics, runtime
from server.guest import guest_env, hub
from server.security import SYSTEM, VNC_PASSWORD, decrypt, encrypt

KINDS = ("desktop", "browser", "code")
MAX_SIZE = 10
# a failed boot holds its pool back this long, so a broken image or host isn't booted in a loop
FAILED_TTL = 300
READY_TIMEOUT = 90
# how often the pool refills; its size is at least the p99 of creates per kind in one such interval over the last hour
REFILL_SECONDS = 10


def pool_id(kind: str, server_id: str | None) -> str:
    return f"{kind}:{server_id or 'local'}"


def demand(db: Querier) -> dict[str, int]:
    """The p99 of creates per kind in one refill interval over the last hour; intervals with none count as 0."""
    intervals = 3600 // REFILL_SECONDS
    counts: dict[str, list[int]] = defaultdict(list)
    for row in db.list_recent_creates(interval_seconds=REFILL_SECONDS):
        if row.kind in KINDS:
            counts[row.kind].append(int(row.creates))
    rank = min(intervals, math.ceil(0.99 * intervals)) - 1
    return {kind: min(MAX_SIZE, sorted([0] * (intervals - len(found)) + found)[rank]) for kind, found in counts.items()}


def age(row) -> float:
    return (datetime.now(UTC) - row.updated_at).total_seconds()


def guest_ready(server) -> bool:
    """Pooled sandboxes need the guest: it is how a claimed one gets its secrets."""
    return bool(guest_env("", remote=server is not None))


class Pool:
    def __init__(self):
        self.booting: set[str] = set()
        # claims per pool since the API started
        self.claimed: dict[str, int] = defaultdict(int)
        self.lock = threading.Lock()
        # tests fill the pool in the calling thread
        self.inline = False

    def claim(self, db: Querier, kind: str, server_id: str | None) -> PoolSandbox | None:
        if kind not in KINDS:
            return None
        row = db.claim_pool_sandbox(kind=kind, server_id=server_id or "", image=runtime.default_image(kind))
        if row is not None:
            self.claimed[pool_id(kind, server_id)] += 1
            self.kick()
        return row

    def kick(self):
        if not self.inline:
            threading.Thread(target=self.fill, daemon=True).start()

    async def run(self, interval: float = REFILL_SECONDS):
        while True:
            await asyncio.to_thread(self.fill)
            await asyncio.sleep(interval)

    def fill(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            self._fill()
        except Exception as e:
            logger.error("pool fill failed", extra={"error": str(e)})
        finally:
            self.lock.release()

    def _fill(self):
        runtime.refill_volumes()
        with db_manager.session() as db:
            sizes = {s.id: s.size for s in db.list_pool_settings()}
            # the configured size is the minimum; recent demand raises it
            for kind, wanted in demand(db).items():
                key = pool_id(kind, None)
                sizes[key] = max(sizes.get(key, 0), wanted)
            rows = list(db.list_pool_sandboxes())
            servers = {s.id: s for s in db.list_all_servers()}
        pools: dict[str, list[PoolSandbox]] = defaultdict(list)
        for row in rows:
            server = servers.get(row.server_id) if row.server_id else None
            if row.id in self.booting:
                pools[pool_id(row.kind, row.server_id)].append(row)
            elif row.status == "booting":
                # left mid-boot by an API restart
                self.discard(row, server)
            elif row.status == "failed":
                if age(row) > FAILED_TTL:
                    self.discard(row, server)
                else:
                    pools[pool_id(row.kind, row.server_id)].append(row)
            elif row.image != runtime.default_image(row.kind) or not self.alive(row):
                self.discard(row, server)
            else:
                pools[pool_id(row.kind, row.server_id)].append(row)
        for key, size in sizes.items():
            kind, _, server_id = key.partition(":")
            server = None if server_id == "local" else servers.get(server_id)
            members = pools.pop(key, [])
            if any(r.status == "failed" for r in members) or (server_id != "local" and server is None):
                continue
            idle = [r for r in members if r.status == "idle"]
            for row in idle[size:]:
                self.discard(row, server)
            missing = size - len(members)
            if missing > 0 and guest_ready(server):
                for _ in range(missing):
                    self.start(kind, server)
        # pools whose setting was removed
        for members in pools.values():
            for row in members:
                if row.id not in self.booting:
                    self.discard(row, servers.get(row.server_id) if row.server_id else None)

    def drain(self, server):
        """Removes a server's pooled sandboxes before the server itself goes."""
        with db_manager.session() as db:
            rows = [r for r in db.list_pool_sandboxes() if r.server_id == server.id]
        for row in rows:
            self.discard(row, server)

    def alive(self, row: PoolSandbox) -> bool:
        try:
            return bool(row.runtime_id) and runtime.is_running(row.runtime_id)
        except Exception:
            # an unreachable host: keep the row and look again next time
            return True

    def start(self, kind: str, server):
        sandbox_id = str(uuid.uuid4())
        config = {"home_claim": f"zoo-home-{sandbox_id}"}
        with db_manager.session() as db:
            if kind != "code":
                # VNC authentication only uses the first 8 characters; no workspace owns the sandbox yet
                config[VNC_PASSWORD] = encrypt(secrets.token_urlsafe(6), db, SYSTEM)
            row = db.create_pool_sandbox(
                CreatePoolSandboxParams(
                    id=sandbox_id,
                    kind=kind,
                    server_id=server.id if server else None,
                    image=runtime.default_image(kind),
                    config=json.dumps(config),
                )
            )
        if row is None:
            raise RuntimeError("the pool sandbox wasn't saved")
        self.booting.add(row.id)
        if self.inline:
            self.boot(row, server)
        else:
            threading.Thread(target=self.boot, args=(row, server), daemon=True).start()

    def boot(self, row: PoolSandbox, server):
        desktop = row.kind != "code"
        began = time.monotonic()
        try:
            env = guest_env(row.id, remote=server is not None)
            if desktop:
                env["ZOO_VNC_PASSWORD"] = decrypt(json.loads(row.config)[VNC_PASSWORD])
            runtime_id, host = runtime.run_container(f"zoo-sandbox-{row.id}", row.image, row.id, env, server, desktop)
            hub.bind(runtime_id, row.id)
            with db_manager.session() as db:
                db.set_pool_sandbox_runtime(runtime_id=runtime_id, runtime_host=host, id=row.id)
            # no network until it is claimed; the hand-off applies its owner's policy
            runtime.apply_network(runtime_id, "deny", False, [])
            if desktop:
                ready = hub.wait_for_desktop(row.id, READY_TIMEOUT)
            else:
                ready = hub.wait_for_guest(row.id, READY_TIMEOUT)
            if not ready:
                raise RuntimeError("did not come up in time")
            if runtime.is_kube():
                # a pod can't be paused, so its processes are: it keeps its memory and stops using CPU
                runtime.pause(runtime_id)
            with db_manager.session() as db:
                db.set_pool_sandbox_idle(id=row.id)
            metrics.pool_refill.record(time.monotonic() - began, {"kind": row.kind})
            logger.info("pool sandbox ready", extra={"sandbox_id": row.id, "kind": row.kind})
        except Exception as e:
            logger.error("pool boot failed", extra={"sandbox_id": row.id, "error": str(e)})
            with db_manager.session() as db:
                db.set_pool_sandbox_failed(error_message=str(e), id=row.id)
                failed = next((r for r in db.list_pool_sandboxes() if r.id == row.id), None)
            if failed is not None:
                self.remove(failed, server)
        finally:
            self.booting.discard(row.id)

    def discard(self, row: PoolSandbox, server):
        """Removes a pooled sandbox nobody has claimed. Claims delete the row too, so only one of them wins."""
        with db_manager.session() as db:
            deleted = db.delete_pool_sandbox(id=row.id)
        if deleted is not None:
            self.remove(deleted, server)

    def remove(self, row: PoolSandbox, server):
        try:
            if row.runtime_id:
                runtime.remove_container(row.runtime_id)
            else:
                runtime.remove_named_container(f"zoo-sandbox-{row.id}")
        except Exception as e:
            logger.debug("pool container already gone", extra={"sandbox_id": row.id, "error": str(e)})
        try:
            runtime.remove_volume_id(row.id, json.loads(row.config))
        except Exception as e:
            logger.warning("pool volume cleanup failed", extra={"sandbox_id": row.id, "error": str(e)})

    def status(self, keys: list[tuple[str, str | None]]) -> list[dict]:
        with db_manager.session() as db:
            sizes = {s.id: s.size for s in db.list_pool_settings()}
            rows = list(db.list_pool_sandboxes())
        out = []
        for kind, server_id in keys:
            key = pool_id(kind, server_id)
            members = [r for r in rows if pool_id(r.kind, r.server_id) == key]
            failed = next((r for r in members if r.status == "failed"), None)
            out.append(
                {
                    "kind": kind,
                    "server_id": server_id,
                    "size": sizes.get(key, 0),
                    "idle": sum(r.status == "idle" for r in members),
                    "booting": sum(r.status == "booting" for r in members),
                    "claimed": self.claimed.get(key, 0),
                    "error": failed.error_message if failed else None,
                }
            )
        return out
