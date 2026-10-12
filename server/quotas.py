"""Workspace quotas: how many sandboxes may run at once, their CPUs and memory together, and storage.

Each workspace gets a row when it is made, with the defaults from ZOO_DEFAULT_QUOTA_RUNNING_SANDBOXES,
ZOO_DEFAULT_QUOTA_CPUS, ZOO_DEFAULT_QUOTA_MEMORY_MB and ZOO_DEFAULT_QUOTA_STORAGE_GB (unset: no limit). Instance
admins (ADMIN_EMAILS) change a workspace's limits; members see them with their usage.

Labels have quotas too (label_quotas): limits for the sandboxes carrying one label value, such as an end user's, or
for each value of a key without limits of its own (value '*'). Workspace admins set them. A sandbox counts against
the quota of every label it carries.

Creating or starting a sandbox checks the quotas in the same transaction, after locking the workspace's row, so
requests that race each other take turns and can't together pass a limit."""

import os

from fastapi import HTTPException

from db.generated.query import EnsureWorkspaceQuotaParams, Querier, WorkspaceUsageRow
from server import sizes


def default(name: str) -> str | None:
    return os.environ.get(f"ZOO_DEFAULT_QUOTA_{name}") or None


RUNNING, CPUS, MEMORY, STORAGE = (default(n) for n in ("RUNNING_SANDBOXES", "CPUS", "MEMORY_MB", "STORAGE_GB"))


def ensure(db: Querier, workspace_id: str):
    """Gives a new workspace the default limits."""
    db.ensure_workspace_quota(
        EnsureWorkspaceQuotaParams(
            workspace_id=workspace_id,
            max_running_sandboxes=int(RUNNING) if RUNNING else None,
            max_cpus=float(CPUS) if CPUS else None,
            max_memory_mb=int(MEMORY) if MEMORY else None,
            max_storage_gb=int(STORAGE) if STORAGE else None,
        )
    )


def usage(db: Querier, workspace_id: str) -> WorkspaceUsageRow:
    row = db.workspace_usage(unsized_gb=sizes.UNSIZED_DISK_GB, workspace_id=workspace_id)
    assert row is not None
    return row


def storage_gb(row: WorkspaceUsageRow) -> float:
    return row.disk_gb + (row.profile_bytes + row.snapshot_bytes) / (1 << 30)


def check(db: Querier, workspace_id: str, size: sizes.Size, new: bool, labels: dict[str, str] | None = None):
    """409 unless the workspace, and each of the sandbox's labels, has room for one more running sandbox of `size`;
    `new` also counts its disk. Call inside the transaction that creates or starts it."""
    db.lock_workspace(id=workspace_id)
    disk = (size.disk_gb or sizes.UNSIZED_DISK_GB) if new else 0
    quota = db.get_workspace_quota(workspace_id=workspace_id)
    if quota is not None:
        used = usage(db, workspace_id)
        compare("the workspace's", quota, used.running, used.cpus, used.memory_mb, storage_gb(used), size, disk)
    for key, value in (labels or {}).items():
        quota = db.get_label_quota(workspace_id=workspace_id, key=key, value=value)
        if quota is None:
            continue
        row = db.label_usage(unsized_gb=sizes.UNSIZED_DISK_GB, key=key, value=value, workspace_id=workspace_id)
        assert row is not None
        storage = row.disk_gb + row.snapshot_bytes / (1 << 30)
        compare(f"{key}={value}'s", quota, row.running, row.cpus, row.memory_mb, storage, size, disk)


def compare(whose: str, quota, running: int, cpus: float, memory_mb: int, storage: float, size: sizes.Size, disk: int):
    """409 when one more running sandbox of `size` (and `disk` more GB) would pass one of `quota`'s limits."""
    checks = [
        ("running sandboxes", quota.max_running_sandboxes, running + 1, running),
        ("CPUs", quota.max_cpus, cpus + size.cpus, cpus),
        ("memory (MB)", quota.max_memory_mb, memory_mb + size.memory_mb, memory_mb),
        ("storage (GB)", quota.max_storage_gb, storage + disk, round(storage, 1)),
    ]
    for name, limit, wanted, now in checks:
        if limit is not None and wanted > limit:
            raise HTTPException(status_code=409, detail=f"{whose} quota is {limit:g} {name}; it uses {now:g} already")
