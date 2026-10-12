/**
 * Zoo tools for the Vercel AI SDK: a model drives a sandbox's shell, files and screen.
 *
 * ```ts
 * import { generateText, stepCountIs } from "ai"
 * import { zooTools } from "@zoo/sdk/ai"
 * const box = await zoo.sandbox({ kind: "desktop" })
 * await generateText({ model, tools: zooTools(box), stopWhen: stepCountIs(20), prompt: "..." })
 * ```
 */
import { tool } from "ai"
import { z } from "zod"

import type { Sandbox } from "./client.js"

export function zooTools(box: Sandbox) {
  return {
    exec: tool({
      description: "Run a shell command in the sandbox and return its exit code, stdout and stderr.",
      inputSchema: z.object({
        command: z.string(),
        timeout: z.number().int().min(1).max(300).default(60),
      }),
      execute: async ({ command, timeout }) => box.exec(command, timeout),
    }),
    readFile: tool({
      description: "Read a text file from the sandbox (absolute, or relative to the home folder).",
      inputSchema: z.object({ path: z.string() }),
      execute: async ({ path }) => new TextDecoder().decode(await box.readFile(path)),
    }),
    writeFile: tool({
      description: "Write a text file into the sandbox, making its folder.",
      inputSchema: z.object({ path: z.string(), content: z.string() }),
      execute: async ({ path, content }) => box.writeFile(path, content),
    }),
    screenshot: tool({
      description: "Take a screenshot of the sandbox's desktop.",
      inputSchema: z.object({}),
      execute: async () => box.tool<string>("screenshot"),
      toModelOutput: (data: string) => ({
        type: "content",
        value: [{ type: "media", data, mediaType: "image/png" }],
      }),
    }),
    click: tool({
      description: "Click at a point on the screen.",
      inputSchema: z.object({
        x: z.number().int(),
        y: z.number().int(),
        button: z.enum(["left", "right", "middle"]).default("left"),
      }),
      execute: async (args) => box.tool("click", args),
    }),
    typeText: tool({
      description: "Type text at the keyboard focus.",
      inputSchema: z.object({ text: z.string() }),
      execute: async ({ text }) => box.tool("type_text", { text }),
    }),
    pressKey: tool({
      description: "Press a key or combination, e.g. Return or ctrl+l.",
      inputSchema: z.object({ key: z.string() }),
      execute: async ({ key }) => box.tool("press_key", { key }),
    }),
  }
}
