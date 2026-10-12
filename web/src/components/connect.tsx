import { Check, Copy, KeyRound, Plug, TriangleAlert } from "lucide-react"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { API_BASE, MCP_URL, useCreateApiKey } from "@/lib/api_client"
import type { Sandbox } from "@/lib/api_client"

const KEY_PLACEHOLDER = "zoo_YOUR_KEY"

/** A block of code with a copy button. */
export function CopyBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="relative">
      <pre className="max-h-72 overflow-auto rounded-lg border border-border bg-muted/40 p-3 pr-10 font-mono text-xs leading-relaxed">
        {code}
      </pre>
      <Button
        variant="ghost"
        size="icon-sm"
        className="absolute top-1.5 right-1.5"
        aria-label="Copy"
        onClick={() => {
          void navigator.clipboard.writeText(code)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
      >
        {copied ? <Check /> : <Copy />}
      </Button>
    </div>
  )
}

/** A secret shown once: copy it now, it can't be shown again. */
export function SecretOnce({
  value,
  what = "key",
}: {
  value: string
  what?: string
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
      <p className="flex items-center gap-2 text-sm text-amber-500">
        <TriangleAlert className="size-4 shrink-0" />
        Copy this {what} now. You won't be able to see it again.
      </p>
      <CopyBlock code={value} />
    </div>
  )
}

export function mcpConfig(key: string) {
  return JSON.stringify(
    {
      mcpServers: {
        zoo: {
          type: "http",
          url: MCP_URL,
          headers: { Authorization: `Bearer ${key}` },
        },
      },
    },
    null,
    2
  )
}

export function snippets(sandbox: Pick<Sandbox, "id" | "kind">, key: string) {
  const id = sandbox.id
  const desktop = sandbox.kind !== "code"
  return {
    mcp: mcpConfig(key),
    curl: [
      `export ZOO_API_KEY=${key}`,
      "",
      `curl -s ${API_BASE}/sandboxes/${id}/exec \\`,
      `  -H "Authorization: Bearer $ZOO_API_KEY" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"command": "uname -a"}'`,
      ...(desktop
        ? [
            "",
            `curl -s -X POST ${API_BASE}/sandboxes/${id}/screenshot \\`,
            `  -H "Authorization: Bearer $ZOO_API_KEY" -o screen.png`,
          ]
        : []),
    ].join("\n"),
    python: [
      "# pip install zoo-sdk",
      "from zoo_sdk import Zoo",
      "",
      `zoo = Zoo(api_key="${key}", base_url="${API_BASE}")`,
      `box = zoo.sandbox("${id}")`,
      'for chunk in box.exec("uname -a", stream=True):',
      '    print(chunk.data, end="")',
      ...(desktop ? ['open("screen.png", "wb").write(box.screenshot())'] : []),
    ].join("\n"),
    typescript: [
      "// npm install @zoo/sdk",
      'import { Zoo } from "@zoo/sdk"',
      "",
      `const zoo = new Zoo({ apiKey: "${key}", baseUrl: "${API_BASE}" })`,
      `const box = await zoo.sandbox("${id}")`,
      'console.log((await box.exec("uname -a")).stdout)',
    ].join("\n"),
  }
}

/** "Connect" on a sandbox: ready-to-paste MCP, curl, Python and TypeScript, with a new key filled in on request. */
const CONNECT_EVENT = "zoo:connect"

/** Opens the Connect dialog of the sandbox page that's showing (the palette uses it). */
export function openConnect() {
  window.dispatchEvent(new Event(CONNECT_EVENT))
}

export function ConnectDialog({ sandbox }: { sandbox: Sandbox }) {
  const create = useCreateApiKey()
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener(CONNECT_EVENT, show)
    return () => window.removeEventListener(CONNECT_EVENT, show)
  }, [])
  const [key, setKey] = useState<string | null>(null)
  const code = snippets(sandbox, key ?? KEY_PLACEHOLDER)
  const tabs = [
    { value: "mcp", label: "MCP", code: code.mcp },
    { value: "curl", label: "curl", code: code.curl },
    { value: "python", label: "Python", code: code.python },
    { value: "typescript", label: "TypeScript", code: code.typescript },
  ]
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" />}>
        <Plug />
        Connect
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Connect to {sandbox.name}</DialogTitle>
          <DialogDescription>
            Paste one of these into your agent or code. They need an API key;
            create one scoped to this sandbox, or use one from Settings.
          </DialogDescription>
        </DialogHeader>
        {key ? (
          <SecretOnce value={key} />
        ) : (
          <Button
            variant="outline"
            className="self-start"
            disabled={create.isPending}
            onClick={() =>
              create.mutate(
                {
                  name: `connect-${sandbox.name}`.slice(0, 100),
                  sandbox_id: sandbox.id,
                  label_selector: null,
                  read_only: false,
                  expires_at: null,
                },
                { onSuccess: (data) => setKey(data.key) }
              )
            }
          >
            <KeyRound />
            Create a key for this sandbox
          </Button>
        )}
        {create.error && (
          <p className="text-sm text-destructive">{create.error.message}</p>
        )}
        <Tabs defaultValue="mcp">
          <TabsList>
            {tabs.map((t) => (
              <TabsTrigger key={t.value} value={t.value}>
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>
          {tabs.map((t) => (
            <TabsContent key={t.value} value={t.value} className="mt-3">
              <CopyBlock code={t.code} />
            </TabsContent>
          ))}
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
