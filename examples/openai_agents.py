"""An OpenAI Agents SDK computer-use agent driving a Zoo desktop. pip install 'zoo-sdk[openai]'"""

import asyncio

from agents import Agent, ComputerTool, ModelSettings, Runner

from zoo_sdk import AsyncZoo
from zoo_sdk.adapters.openai_agents import ZooComputer, zoo_tools


async def main():
    async with AsyncZoo() as zoo, await zoo.sandbox(kind="desktop") as box:
        agent = Agent(
            name="operator",
            model="computer-use-preview",
            tools=[*zoo_tools(box, ["exec"]), ComputerTool(ZooComputer(box))],
            model_settings=ModelSettings(truncation="auto"),
        )
        result = await Runner.run(agent, "Open Firefox, search for 'Zoo sandboxes' and summarise the first result.")
        print(result.final_output)


asyncio.run(main())
