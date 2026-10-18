"""Which sandboxes each kind of host can run.

Hosts join through zoo-node. Native VMs use their host OS; Linux workloads
use a Linux Docker engine on any host, or the installation's configured runtime.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    id: str
    name: str  # the sandbox OS, as the dashboard shows it
    host: str  # what to add to run it, as in "Add a Mac"
    kinds: tuple[str, ...]  # the sandbox kinds of this OS
    runs: tuple[str, ...]  # the sandbox OSes a host of this platform can run
    requirements: str


LINUX_PLATFORM = Platform(
    "linux",
    "Linux",
    "Linux server",
    ("desktop", "browser", "code"),
    ("linux",),
    "Linux Docker engine, or the configured Docker or Kubernetes runtime",
)

PLATFORMS = {
    "linux": LINUX_PLATFORM,
    "macos": Platform(
        "macos",
        "macOS",
        "Mac",
        ("macos",),
        ("macos", "linux"),
        "Native macOS: Apple Silicon, macOS 14+, zoovm. Linux desktop/browser/code: a Linux Docker engine.",
    ),
    "windows": Platform(
        "windows",
        "Windows",
        "Windows machine",
        ("windows",),
        ("windows", "linux"),
        "Native Windows: Windows Pro/Enterprise/Education or Server with Hyper-V. Linux desktop/browser/code: a Linux Docker engine.",
    ),
}

INSTALL_URL = os.environ.get("ZOO_INSTALL_URL")


def os_of(kind: str) -> str:
    return next((p.id for p in PLATFORMS.values() if kind in p.kinds), "linux")


def parse(capabilities: str) -> set[str]:
    return {c for c in capabilities.split(",") if c}


def capabilities_of(platform: str) -> str:
    """The sandbox OSes a registered node host can run."""
    return ",".join(PLATFORMS[platform].runs)


def install_command(platform: str, public_key: str, api_url: str) -> str:
    """Sets up a node host and authorizes the control plane's SSH identity."""
    if platform not in PLATFORMS:
        raise ValueError("node hosts must be linux, macos or windows")
    source = INSTALL_URL or f"{api_url.rstrip('/')}/v1/nodes/install"
    if platform == "windows":
        return (
            f'powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm {source}/install-node.ps1)))'
            f" -Key '{public_key}' -ControlPlane '{api_url}'\""
        )
    return (
        f"curl -fsSL {source}/install.sh | sudo bash -s -- --node --key '{public_key}' --control-plane '{api_url}'"
    )
