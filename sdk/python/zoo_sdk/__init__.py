"""Python client for Zoo sandboxes. `Zoo` is synchronous, `AsyncZoo` the asyncio twin; both read ZOO_API_KEY and
ZOO_URL, time out every call, retry when it's safe, and raise `ZooError` subclasses carrying the API's error code."""

from zoo_sdk.aio import AsyncSandbox, AsyncZoo
from zoo_sdk.client import Sandbox, Zoo
from zoo_sdk.errors import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ValidationError,
    ZooError,
)
from zoo_sdk.types import ExecChunk, ExecResult, SandboxData

__all__ = [
    "APIConnectionError",
    "APITimeoutError",
    "AsyncSandbox",
    "AsyncZoo",
    "AuthenticationError",
    "ConflictError",
    "ExecChunk",
    "ExecResult",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "Sandbox",
    "SandboxData",
    "ServerError",
    "ValidationError",
    "Zoo",
    "ZooError",
]
