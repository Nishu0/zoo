import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { Box, Check, KeyRound, Plug, Rocket, Sparkles } from "lucide-react"
import { useState } from "react"

import { CopyBlock, mcpConfig, SecretOnce } from "@/components/connect"
import { Page } from "@/components/page"
import { SandboxProgress } from "@/components/sandbox/progress"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  displayStatus,
  useCreateApiKey,
  useCreateSandbox,
  useFinishOnboarding,
  useSandbox,
} from "@/lib/api_client"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_app/welcome")({
  component: WelcomePage,
})

function Step({
  n,
  done,
  icon: Icon,
  title,
  description,
  children,
}: {
  n: number
  done: boolean
  icon: typeof Box
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <Card className={cn(done && "border-emerald-500/40")}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span
            className={cn(
              "flex size-6 items-center justify-center rounded-full border text-xs",
              done
                ? "border-emerald-500 bg-emerald-500 text-white"
                : "border-border"
            )}
          >
            {done ? <Check className="size-3.5" /> : n}
          </span>
          <Icon className="size-4" />
          {title}
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">{children}</CardContent>
    </Card>
  )
}

/** First run: create a sandbox, copy an API key, copy an MCP config with the key filled in. */
function WelcomePage() {
  const navigate = useNavigate()
  const createSandbox = useCreateSandbox()
  const createKey = useCreateApiKey()
  const finish = useFinishOnboarding()
  const [sandboxId, setSandboxId] = useState<string | null>(null)
  const [demo, setDemo] = useState(false)
  const [key, setKey] = useState<string | null>(null)
  const sandbox = useSandbox(sandboxId ?? "", sandboxId !== null)

  const done = (to: "sandbox" | "list") =>
    finish.mutate(undefined, {
      onSuccess: () =>
        to === "sandbox" && sandboxId
          ? navigate({
              to: demo
                ? "/sandboxes/$sandboxId/agent"
                : "/sandboxes/$sandboxId",
              params: { sandboxId },
            })
          : navigate({ to: "/sandboxes" }),
    })

  const create = (asDemo: boolean) => {
    setDemo(asDemo)
    createSandbox.mutate(
      asDemo
        ? { name: "demo", kind: "desktop", labels: { demo: "true" } }
        : { kind: "desktop" },
      { onSuccess: (data) => setSandboxId(data.id) }
    )
  }

  return (
    <Page
      icon={Rocket}
      title="Welcome to Zoo"
      description="Three steps to drive a desktop with an agent."
      action={
        <Button
          variant="ghost"
          disabled={finish.isPending}
          onClick={() => done("list")}
        >
          Skip
        </Button>
      }
    >
      <div className="flex max-w-2xl flex-col gap-4">
        <Step
          n={1}
          done={sandbox.data?.status === "running"}
          icon={Box}
          title="Create a sandbox"
          description="A Linux desktop your agent can see and use."
        >
          {sandbox.data ? (
            <>
              <div className="flex items-center gap-2 text-sm">
                <span className="font-medium">{sandbox.data.name}</span>
                <StatusBadge status={displayStatus(sandbox.data)} />
              </div>
              <SandboxProgress sandbox={sandbox.data} />
            </>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={createSandbox.isPending}
                onClick={() => create(true)}
              >
                <Sparkles />
                Start with a demo
              </Button>
              <Button
                variant="outline"
                disabled={createSandbox.isPending}
                onClick={() => create(false)}
              >
                <Box />
                Empty desktop
              </Button>
            </div>
          )}
          {demo && !sandbox.data && (
            <p className="text-xs text-muted-foreground">
              The demo comes with a task ready to run in its Agent tab.
            </p>
          )}
          {createSandbox.error && (
            <p className="text-sm text-destructive">
              {createSandbox.error.message}
            </p>
          )}
        </Step>

        <Step
          n={2}
          done={key !== null}
          icon={KeyRound}
          title="Copy an API key"
          description="Agents and code use it to reach your sandboxes."
        >
          {key ? (
            <SecretOnce value={key} />
          ) : (
            <Button
              className="self-start"
              variant="outline"
              disabled={createKey.isPending}
              onClick={() =>
                createKey.mutate(
                  {
                    name: "first key",
                    sandbox_id: null,
                    label_selector: null,
                    read_only: false,
                    expires_at: null,
                  },
                  { onSuccess: (data) => setKey(data.key) }
                )
              }
            >
              <KeyRound />
              Create my API key
            </Button>
          )}
          {createKey.error && (
            <p className="text-sm text-destructive">
              {createKey.error.message}
            </p>
          )}
        </Step>

        <Step
          n={3}
          done={false}
          icon={Plug}
          title="Connect your agent"
          description="Paste this into Claude Desktop, Claude Code (.mcp.json), Cursor or any MCP client."
        >
          <CopyBlock code={mcpConfig(key ?? "zoo_YOUR_KEY")} />
          {!key && (
            <p className="text-xs text-muted-foreground">
              Create the key in step 2 and it's filled in here.
            </p>
          )}
        </Step>

        <div className="flex justify-end gap-2">
          <Button
            disabled={finish.isPending}
            onClick={() => done(sandboxId ? "sandbox" : "list")}
          >
            {sandboxId ? "Go to my sandbox" : "Done"}
          </Button>
        </div>
      </div>
    </Page>
  )
}
