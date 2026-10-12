"""A Claude Agent SDK agent working in a Zoo desktop. pip install 'zoo-sdk[claude]'"""

import asyncio

from claude_agent_sdk import ClaudeAgentOptions, query

from zoo_sdk import AsyncZoo
from zoo_sdk.adapters.claude import zoo_server


async def main():
    async with AsyncZoo() as zoo, await zoo.sandbox(kind="desktop") as box:
        options = ClaudeAgentOptions(mcp_servers={"zoo": zoo_server(box)}, allowed_tools=["mcp__zoo__*"])
        prompt = "Open https://news.ycombinator.com in the sandbox and tell me the top story's title."
        async for message in query(prompt=prompt, options=options):
            print(message)


asyncio.run(main())
