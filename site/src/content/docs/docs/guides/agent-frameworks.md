---
title: Agent frameworks
description: Use Zoo sandboxes as tools in the Claude Agent SDK, the OpenAI Agents SDK, LangGraph and the Vercel AI SDK.
---

Each adapter turns a sandbox into the framework's own tools: `exec`, `read_file`, `write_file`, `screenshot`,
`click`, `type_text`, `press_key` and `open_url`. They take a `Sandbox` or an `AsyncSandbox`. Full examples are in
`examples/`.

## Claude Agent SDK

```python
# pip install 'zoo-sdk[claude]'
from claude_agent_sdk import ClaudeAgentOptions, query
from zoo_sdk import AsyncZoo
from zoo_sdk.adapters.claude import zoo_server

async with AsyncZoo() as zoo, await zoo.sandbox(kind="desktop") as box:
    options = ClaudeAgentOptions(mcp_servers={"zoo": zoo_server(box)}, allowed_tools=["mcp__zoo__*"])
    async for message in query(prompt="Open news.ycombinator.com and read the top title", options=options):
        print(message)
```

## OpenAI Agents SDK

`zoo_tools(box)` gives `FunctionTool`s; `ZooComputer(box)` is an `AsyncComputer` for `ComputerTool`.

```python
# pip install 'zoo-sdk[openai]'
from agents import Agent, ComputerTool, Runner
from zoo_sdk.adapters.openai_agents import ZooComputer, zoo_tools

agent = Agent(name="operator", model="computer-use-preview",
              tools=[*zoo_tools(box, ["exec"]), ComputerTool(ZooComputer(box))])
await Runner.run(agent, "Search for Zoo sandboxes and summarise the first result")
```

## LangGraph

```python
# pip install 'zoo-sdk[langgraph]' langgraph
from langgraph.prebuilt import create_react_agent
from zoo_sdk.adapters.langgraph import zoo_tools

agent = create_react_agent(model, zoo_tools(box, ["exec", "read_file", "write_file"]))
```

## Vercel AI SDK

```ts
// npm install @zoo/sdk ai zod
import { generateText, stepCountIs } from "ai"
import { Zoo } from "@zoo/sdk"
import { zooTools } from "@zoo/sdk/ai"

await using box = await new Zoo().sandbox({ kind: "desktop" })
const { text } = await generateText({ model, tools: zooTools(box), stopWhen: stepCountIs(25), prompt: "..." })
```
