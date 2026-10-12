"""Zoo sandboxes as tools for agent frameworks. Each adapter turns the same tool set (TOOLS) into the framework's
own tool type; they take a `Sandbox` or an `AsyncSandbox`.

- zoo_sdk.adapters.claude: Claude Agent SDK (an in-process MCP server)
- zoo_sdk.adapters.openai_agents: OpenAI Agents SDK (function tools, and a computer for ComputerTool)
- zoo_sdk.adapters.langgraph: LangChain tools, for LangGraph agents

The framework is only imported by its adapter, so install just the one you use (pip install 'zoo-sdk[claude]')."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from zoo_sdk.aio import AsyncSandbox
from zoo_sdk.client import Sandbox

AnySandbox = Sandbox | AsyncSandbox


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # JSON schema of the arguments
    parameters: dict[str, Any]
    # the sandbox tool it calls, with these arguments
    call: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


INT = {"type": "integer"}
STR = {"type": "string"}

TOOLS = [
    ToolSpec(
        "exec",
        "Run a shell command in the sandbox; returns exit_code, stdout, stderr and timed_out.",
        _schema({"command": STR, "timeout": {"type": "integer", "minimum": 1, "maximum": 300}}, ["command"]),
        lambda a: ("execute_command", {"command": a["command"], "timeout": a.get("timeout", 60)}),
    ),
    ToolSpec(
        "read_file",
        "Read a text file from the sandbox (absolute, or relative to the home folder).",
        _schema({"path": STR}, ["path"]),
        lambda a: ("read_file", {"path": a["path"]}),
    ),
    ToolSpec(
        "write_file",
        "Write a text file into the sandbox.",
        _schema({"path": STR, "content": STR}, ["path", "content"]),
        lambda a: ("write_file", {"path": a["path"], "content": a["content"]}),
    ),
    ToolSpec(
        "screenshot",
        "Take a screenshot of the sandbox's desktop (PNG).",
        _schema({}, []),
        lambda a: ("screenshot", {}),
    ),
    ToolSpec(
        "click",
        "Click at a point on the screen.",
        _schema({"x": INT, "y": INT, "button": {"enum": ["left", "middle", "right"]}}, ["x", "y"]),
        lambda a: ("click", {"x": a["x"], "y": a["y"], "button": a.get("button", "left")}),
    ),
    ToolSpec(
        "type_text",
        "Type text at the keyboard focus.",
        _schema({"text": STR}, ["text"]),
        lambda a: ("type_text", {"text": a["text"]}),
    ),
    ToolSpec(
        "press_key",
        "Press a key or combination, e.g. Return or ctrl+l.",
        _schema({"key": STR}, ["key"]),
        lambda a: ("press_key", {"key": a["key"]}),
    ),
    ToolSpec(
        "open_url",
        "Open a URL in the sandbox's browser.",
        _schema({"url": STR}, ["url"]),
        lambda a: ("open_url", {"url": a["url"]}),
    ),
]


async def call(box: AnySandbox, name: str, args: dict[str, Any]) -> Any:
    """Calls a sandbox tool from async code, whichever kind of sandbox it is."""
    if isinstance(box, AsyncSandbox):
        return await box.tool(name, **args)
    return await asyncio.to_thread(lambda: box.tool(name, **args))


async def run(box: AnySandbox, spec: ToolSpec, args: dict[str, Any]) -> Any:
    tool, tool_args = spec.call(args)
    return await call(box, tool, tool_args)


def text(result: Any) -> str:
    return result if isinstance(result, str) else json.dumps(result, default=str)
