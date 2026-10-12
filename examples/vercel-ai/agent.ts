// A Vercel AI SDK agent driving a Zoo desktop. bun install && ZOO_API_KEY=... ANTHROPIC_API_KEY=... bun agent.ts
import { anthropic } from "@ai-sdk/anthropic"
import { Zoo } from "@zoo/sdk"
import { zooTools } from "@zoo/sdk/ai"
import { generateText, stepCountIs } from "ai"

const zoo = new Zoo()
await using box = await zoo.sandbox({ kind: "desktop" })

const { text } = await generateText({
  model: anthropic("claude-sonnet-5-5"),
  tools: zooTools(box),
  stopWhen: stepCountIs(25),
  prompt: "Open https://news.ycombinator.com in the sandbox's browser and tell me the top story's title.",
})
console.log(text)
