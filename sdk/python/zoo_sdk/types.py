"""Shapes of what the API returns, for type checkers. They are plain dicts at runtime."""

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

SandboxKind = Literal["desktop", "code", "browser", "macos", "windows"]
SandboxStatus = Literal["pending", "provisioning", "running", "stopped", "failed", "deleting", "deleted"]


class SandboxData(TypedDict):
    id: str
    name: str
    kind: str
    status: str
    server_id: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    labels: NotRequired[dict[str, str]]
    job: NotRequired[dict | None]
    cpus: NotRequired[float]
    memory_mb: NotRequired[int]


class ExecResult(TypedDict):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool


@dataclass
class ExecChunk:
    """A piece of a streamed command: output as it arrives (`stdout`/`stderr`, with `data`), then `exit`."""

    stream: Literal["stdout", "stderr", "exit"]
    data: str = ""
    exit_code: int | None = None

    @property
    def timed_out(self) -> bool:
        return self.exit_code == 124
