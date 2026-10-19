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
