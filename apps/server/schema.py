from pydantic import BaseModel, Field


class ExecRequest(BaseModel):
    command: str
    timeout: int = Field(default=30, ge=1, le=300)


class ExecResponse(BaseModel):
    sandbox_id: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class BatchExecRequest(BaseModel):
    """Several commands in one HTTP round trip: the write → run → read cycle costs one tool call
    instead of three (the MCP-BatchIt pattern). Commands run in order."""
    commands: list[ExecRequest] = Field(min_length=1, max_length=25)
    stop_on_error: bool = True


class BatchExecResponse(BaseModel):
    sandbox_id: str
    results: list[ExecResponse]
    stopped_early: bool = False
    total_ms: float
