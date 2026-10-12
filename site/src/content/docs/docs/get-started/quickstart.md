---
title: Quickstart
description: Install Zoo, get an API key and let an agent drive a desktop, in about five minutes.
---

## 1. Install

On an Ubuntu or Debian server with KVM (no KVM: it falls back to `runc` and says so):

```bash
curl -fsSL https://github.com/chann44/zoo/releases/latest/download/install.sh | sudo bash -s -- --admin-email you@example.com
```

Or on your laptop, from a clone: `docker compose up -d`.

## 2. Sign up

Open the dashboard (`http://<server>:3000`) and sign up. The first run takes you through three steps:

1. **Create a sandbox.** Pick *Start with a demo* for a desktop with a task ready in its Agent tab.
2. **Copy an API key.** It's shown once; keep it somewhere safe.
3. **Copy the MCP config.** The key is already filled in.

## 3. Connect an agent

Paste the MCP config into Claude Desktop, Claude Code (`.mcp.json`) or Cursor:

```json
{
  "mcpServers": {
    "zoo": {
      "type": "http",
      "url": "http://<server>:8000/v1/mcp/",
      "headers": { "Authorization": "Bearer zoo_YOUR_KEY" }
    }
  }
}
```

Ask it to *"create a desktop sandbox, open Firefox and find today's top story on Hacker News"*, and watch it work in
the dashboard. Every sandbox's **Connect** button has the same config, plus curl, Python and TypeScript snippets.

## 4. Or drive it from code

```bash
pip install 'zoo-sdk[cli]'
zoo login                          # the URL and your key
zoo create --kind desktop          # prints its id
zoo exec <id> -- uname -a
zoo view <id>                      # the live screen in your browser
```

```python
from zoo_sdk import Zoo

with Zoo().sandbox(kind="desktop") as box:   # deleted when the block ends
    print(box.exec("uname -a")["stdout"])
    open("screen.png", "wb").write(box.screenshot())
```

```ts
import { Zoo } from "@zoo/sdk"

await using box = await new Zoo().sandbox({ kind: "desktop" })
console.log((await box.exec("uname -a")).stdout)
```

## Next

- [The `zoo` CLI](/docs/reference/cli/) and [API conventions](/docs/reference/conventions/): errors, versions,
  idempotency keys, pagination
