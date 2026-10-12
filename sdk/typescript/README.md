# @zoo/sdk

TypeScript SDK for [Zoo](https://github.com/chann44/zoo): self-hosted Linux, macOS and Windows sandboxes for AI
agents. ESM, Node 20.19+, Bun, Deno and browsers.

```ts
import { Zoo } from "@zoo/sdk"

const zoo = new Zoo() // ZOO_API_KEY, ZOO_URL
await using box = await zoo.sandbox({ kind: "desktop" }) // deleted when the scope ends
for await (const chunk of box.execStream("pip install requests"))
  if (chunk.stream !== "exit") process.stdout.write(chunk.data)

// without `await using`
await zoo.withSandbox({ kind: "code" }, async (box) => box.exec("uname -a"))
```

Every call has a timeout (30 s unless it passes its own) and is retried with backoff when that's safe: connection
failures and 429 always; 5xx and timeouts for reads and for creates, which carry an `Idempotency-Key`. Errors are
`ZooError` subclasses with the API's `code`, `hint` and `docsUrl`. Request and response types for every endpoint are
exported as `paths` and `components`, generated from the API's OpenAPI schema (`bun run generate`).
