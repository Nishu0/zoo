"""What a Linux sandbox may use: CPUs, memory and, optionally, disk.

Every sandbox has a size, chosen when it is created (2 CPUs and 2 GB by default) and capped by ZOO_MAX_SANDBOX_CPUS,
ZOO_MAX_SANDBOX_MEMORY_MB and ZOO_MAX_SANDBOX_DISK_GB. CPUs and memory are always enforced: Docker's nano_cpus and
mem_limit, a pod's limits. Disk is enforced only when asked for: on Kubernetes it is the home claim's size, on Docker
the container's storage_opt, which needs a storage driver with quotas (overlay2 on XFS with pquota); a host without
one fails the boot rather than run the sandbox unlimited. Without a disk size the sandbox gets the host's usual disk
(ZOO_KUBERNETES_HOME_SIZE on Kubernetes).

macOS and Windows VMs keep their own fixed sizes, so a size other than the default is refused for them."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Size:
    cpus: float = 2
    memory_mb: int = 2048
    disk_gb: int | None = None

    @property
    def memory_bytes(self) -> int:
        return self.memory_mb << 20


DEFAULT = Size()
MAX_CPUS = float(os.environ.get("ZOO_MAX_SANDBOX_CPUS", "8"))
MAX_MEMORY_MB = int(os.environ.get("ZOO_MAX_SANDBOX_MEMORY_MB", "16384"))
MAX_DISK_GB = int(os.environ.get("ZOO_MAX_SANDBOX_DISK_GB", "200"))
# what a sandbox without a disk size counts as against a workspace's storage quota
UNSIZED_DISK_GB = int(os.environ.get("ZOO_SANDBOX_DISK_GB", "20"))


def of(sandbox) -> Size:
    """The size a sandbox row (or anything with cpus, memory_mb and disk_gb) records."""
    return Size(sandbox.cpus, sandbox.memory_mb, sandbox.disk_gb)
