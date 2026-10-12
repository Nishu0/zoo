"""Zoo for the Claude Agent SDK: the sandbox's tools as an in-process MCP server.

from claude_agent_sdk import ClaudeAgentOptions, query
from zoo_sdk.adapters.claude import zoo_server

options = ClaudeAgentOptions(mcp_servers={"zoo": zoo_server(box)}, allowed_tools=["mcp__zoo__*"])
async for message in query(prompt="...", options=options):
    ...
"""
# pyright: reportMissingImports=false

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from zoo_sdk.adapters import TOOLS, AnySandbox, ToolSpec, run, text


def zoo_tools(box: AnySandbox) -> list[Any]:
    def make(spec: ToolSpec):
        @tool(spec.name, spec.description, spec.parameters)
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await run(box, spec, args)
            except Exception as e:
                return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
            if spec.name == "screenshot":
                return {"content": [{"type": "image", "data": result, "mimeType": "image/png"}]}
            return {"content": [{"type": "text", "text": text(result)}]}

        return handler

    return [make(spec) for spec in TOOLS]


def zoo_server(box: AnySandbox, name: str = "zoo") -> Any:
    """An MCP server config for ClaudeAgentOptions(mcp_servers={name: ...}); its tools are mcp__<name>__<tool>."""
    return create_sdk_mcp_server(name=name, version="1.0.0", tools=zoo_tools(box))
