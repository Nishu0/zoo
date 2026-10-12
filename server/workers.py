"""Process roles and leases for singleton work.

ZOO_ROLE picks what a process runs: `all` (the default) serves the API and runs the background work, `api` only
serves requests, and `worker` runs the background work (lifecycle jobs and the warm pool). Scale out with any
number of `api` replicas and one `worker`. `gateway` holds the guest and node connections for the others, which
set ZOO_GATEWAY to reach them (server/gateway.py).
"""

import os
import socket
import uuid

from db.connection import db_manager
from server.jobs import stamp

ROLE = os.environ.get("ZOO_ROLE", "all")
if ROLE not in ("all", "api", "worker", "gateway"):
    raise RuntimeError(f"ZOO_ROLE must be all, api, worker or gateway, not {ROLE!r}")
# identifies this process in leases.holder
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def works() -> bool:
    """Whether this process runs background work."""
    return ROLE in ("all", "worker")


def hold(name: str, ttl: float) -> bool:
    """Takes or renews the lease `name` for `ttl` seconds; False while another live process holds it."""
    with db_manager.session() as db:
        return db.acquire_lease(name=name, holder=WORKER_ID, expires_at=stamp(ttl)) == WORKER_ID


def release(name: str):
    with db_manager.session() as db:
        db.release_lease(name=name, holder=WORKER_ID)
