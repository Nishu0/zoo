"""Zoo for LangGraph (and any LangChain agent): the sandbox's tools as LangChain StructuredTools.

from langgraph.prebuilt import create_react_agent
from zoo_sdk.adapters.langgraph import zoo_tools

agent = create_react_agent(model, zoo_tools(box))
agent.invoke({"messages": [("user", "...")]})
"""
# pyright: reportMissingImports=false

import asyncio
from typing import Any

from langchain_core.tools import StructuredTool

from zoo_sdk.adapters import TOOLS, AnySandbox, ToolSpec, run, text


def zoo_tools(box: AnySandbox, names: list[str] | None = None) -> list[StructuredTool]:
    """The sandbox's tools (all of them, or those `names`); both invoke and ainvoke work."""

    def make(spec: ToolSpec) -> StructuredTool:
        async def arun(**kwargs: Any) -> str:
            return text(await run(box, spec, kwargs))

        def srun(**kwargs: Any) -> str:
            return asyncio.run(arun(**kwargs))

        return StructuredTool.from_function(
            func=srun,
            coroutine=arun,
            name=spec.name,
            description=spec.description,
            args_schema=spec.parameters,
        )

    return [make(spec) for spec in TOOLS if names is None or spec.name in names]
