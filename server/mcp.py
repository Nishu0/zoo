import inspect

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from db.connection import db_manager
from server.auth_api import AuthApi
from server.registry import TOOLS, Tool
from server.sandbox_api import CreateSandboxRequest, SandboxApi, to_response


def build_mcp(auth: AuthApi, sandboxes: SandboxApi) -> MCPServer:
    mcp = MCPServer("zoo")

    def user_of(ctx: Context):
        header = (ctx.headers or {}).get("authorization", "")
        with db_manager.session() as db:
            workspace = (ctx.headers or {}).get("x-zoo-workspace") or None
            user = auth.user_from_token(header.removeprefix("Bearer ").strip(), db, workspace)
        if user is None:
            raise ToolError("unauthorized: pass Authorization: Bearer <zoo api key>")
        return user

    def wrap(tool: Tool):
        async def handler(ctx: Context, sandbox_id: str, **kwargs):
            try:
                return await sandboxes.run_tool(user_of(ctx), sandbox_id, tool.name, kwargs, "mcp")
            except HTTPException as e:
                raise ToolError(e.detail)

        params = [
            inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context),
            inspect.Parameter("sandbox_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
            *[p.replace(kind=inspect.Parameter.POSITIONAL_OR_KEYWORD) for p in tool.params],
        ]
        handler.__signature__ = inspect.Signature(params)
        handler.__annotations__ = {p.name: p.annotation for p in params}
        handler.__name__ = tool.name
        return handler

    for tool in TOOLS.values():
        mcp.add_tool(
            wrap(tool),
            name=tool.name,
            description=f"{tool.category}: {tool.name.replace('_', ' ')} (requires {tool.permission}.{tool.action})",
        )

    @mcp.tool()
    def list_sandboxes(ctx: Context) -> list[dict]:
        user = user_of(ctx)
        with db_manager.session() as db:
            sandboxes = db.list_sandboxes_by_workspace(workspace_id=user.workspace_id)
            return [to_response(s, db).model_dump() for s in sandboxes if user.key_sandbox_id in (None, s.id)]

    @mcp.tool()
    async def create_sandbox(
        ctx: Context, name: str | None = None, kind: str = "desktop", server_id: str | None = None
    ) -> dict:
        user = user_of(ctx)
        with db_manager.session() as db:
            try:
                sandbox = sandboxes.create(CreateSandboxRequest(name=name, kind=kind, server_id=server_id), user, db)
                response = to_response(sandbox, db).model_dump()
            except HTTPException as e:
                raise ToolError(e.detail)
        sandboxes.jobs.kick()
        return response

    @mcp.tool()
    def get_sandbox(ctx: Context, sandbox_id: str) -> dict:
        user = user_of(ctx)
        with db_manager.session() as db:
            try:
                return to_response(sandboxes.owned(sandbox_id, user, db), db).model_dump()
            except HTTPException as e:
                raise ToolError(e.detail)

    return mcp
