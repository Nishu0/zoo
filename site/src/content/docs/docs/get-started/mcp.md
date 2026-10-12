---
title: Connect Claude Code or Claude Desktop
description: Point any MCP client at Zoo's MCP endpoint and it gets every sandbox tool — with an API key, over streamable HTTP.
---

Zoo's MCP server is at `/mcp/` (streamable HTTP) and requires an API key. It exposes every
[sandbox tool](/docs/reference/tools/), each taking a `sandbox_id`, plus `list_sandboxes`,
`create_sandbox` and `get_sandbox`.

## Claude Desktop

Add Zoo to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zoo": {
      "type": "http",
      "url": "http://localhost:8000/mcp/",
      "headers": { "Authorization": "Bearer zoo_..." }
    }
  }
}
```

Replace the URL with your server's (`https://your.domain/mcp/` behind
[Caddy](/docs/guides/domain-https-backups/)) and `zoo_...` with an API key from
**Profile → API keys**.

## Claude Code

Claude Code reads the same `mcpServers` shape from a `.mcp.json` at the project root, so
every session in that project gets the tools:

```json
{
  "mcpServers": {
    "zoo": {
      "type": "http",
      "url": "http://localhost:8000/mcp/",
      "headers": { "Authorization": "Bearer zoo_..." }
    }
  }
}
```

## What the agent can do

`list_sandboxes` and `create_sandbox` let the agent get its own computer; every other tool
targets one sandbox. Tools are grouped by category — observe, mouse, keyboard, windows,
apps, browser, web, shell and files — and each is gated by the sandbox's
[permissions](/docs/guides/policies/#tool-permissions). The full list, with arguments,
permissions and which sandbox kinds support each tool, is generated from the code:
[Tools reference](/docs/reference/tools/).

:::note
The MCP server serves the same tools as the REST API and the Python SDK — one registry,
three doors. Anything you can do from the dashboard, an agent can do here.
:::

