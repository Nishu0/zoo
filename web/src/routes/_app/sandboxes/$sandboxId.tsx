import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import {
  Activity,
  AppWindow,
  Bot,
  Box,
  Cpu,
  Download,
  Check,
  Cog,
  Copy,
  ExternalLink,
  Hand,
  Link2,
  KeyRound,
  Globe,
  History,
  ImageIcon,
  Info,
  Loader2,
  MemoryStick,
  MessagesSquare,
  MousePointerClick,
  Play,
  Plus,
  RotateCcw,
  Send,
  ShieldCheck,
  Square,
  Trash2,
  Vault,
  Workflow,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { z } from "zod"

import { EmptyState, Page } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { StatCard } from "@/components/stat-card"
import { StatusBadge } from "@/components/status-badge"
import { TerminalView } from "@/components/terminal-view"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  agentChannelInputSchema,
  agentSettingsInputSchema,
  api,
  apiUrl,
  displayStatus,
  queryKeys,
  useAddAgentChannel,
  useAgent,
  useAgentChannels,
  useAgentSettings,
  useIntegrations,
  useRemoveAgentChannel,
  useUpdateAgentChannel,
  useResetAgent,
  useSaveAgentSettings,
  useStopAgent,
  useTakeover,
  useViewerLinks,
  useCreateViewerLink,
  useRevokeViewerLink,
  downloadBackup,
  networkRuleInputSchema,
  secretInputSchema,
  useAddRule,
  useApps,
  useDeleteSandbox,
  useExecutions,
  useRemoveSecret,
  useSandboxAction,
  useSecrets,
  useAttachedSecrets,
  useToggleAttachedSecret,
  useVaultSecrets,
  useSetSecret,
  useMonitoring,
  useNetwork,
  usePermissions,
  useRemoveRule,
  useSandbox,
  useSetApp,
  useSetNetwork,
  useSetPermission,
  useApplyProfile,
  useCaptureProfile,
  useMoveSandbox,
  useProfileApps,
  useProfileVersions,
  useProfiles,
  useRemoveProfileVersion,
  useServers,
  useCreateSnapshot,
  useRemoveSnapshot,
  useRestoreSnapshot,
  useSnapshots,
} from "@/lib/api_client"
import type {
  AgentChannel,
  AgentEvent,
  AgentProviderId,
  ViewerLinkMode,
  ChatPlatform,
  NetworkRuleInput,
  Profile,
  Sandbox,
} from "@/lib/api_client"
import { formatBytes, relativeTime, timeAgo } from "@/lib/utils"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId")({
  component: SandboxDetailPage,
})

const RULE_TYPES = [
  { value: "domain", label: "Domain" },
  { value: "ip", label: "IP" },
  { value: "cidr", label: "CIDR" },
]

const EFFECTS = [
  { value: "allow", label: "Allow" },
  { value: "deny", label: "Deny" },
]

function SandboxDetailPage() {
  const { sandboxId } = Route.useParams()
  const navigate = useNavigate()
  const sandbox = useSandbox(sandboxId)
  const remove = useDeleteSandbox()
  const start = useSandboxAction("start")
  const stop = useSandboxAction("stop")

  if (sandbox.isPending) {
    return (
      <Page icon={Box} title={<Skeleton className="h-6 w-40" />}>
        <Skeleton className="h-64 rounded-xl" />
      </Page>
    )
  }

  if (sandbox.error) {
    return (
      <Page icon={Box} title="Sandbox">
        <EmptyState
          icon={Box}
          title="Sandbox not found"
          description={sandbox.error.message}
          action={
            <Button variant="outline" render={<Link to="/sandboxes" />}>
              Back to sandboxes
            </Button>
          }
        />
      </Page>
    )
  }

  const data = sandbox.data
  const running = data.status === "running"
  const failed = data.status === "failed"
  const startable = data.status === "stopped" || failed
  const busy = data.job !== null

  return (
    <Page
      icon={Box}
      title={
        <>
          {data.name}
          <StatusBadge
            status={remove.isPending ? "deleting" : displayStatus(data)}
          />
        </>
      }
      description={<span className="font-mono">{data.id}</span>}
      action={
        <>
          {running ? (
            <Button
              variant="outline"
              disabled={stop.isPending || busy}
              onClick={() => stop.mutate(data.id)}
            >
              <Square />
              Stop
            </Button>
          ) : (
            <Button
              variant="outline"
              disabled={!startable || start.isPending || busy}
              onClick={() => start.mutate(data.id)}
            >
              {failed ? <RotateCcw /> : <Play />}
              {failed ? "Retry" : "Start"}
            </Button>
          )}
          <Button
            variant="outline"
            disabled={!running}
            onClick={() => void downloadBackup(data.id, data.name)}
          >
            <Download />
            Export .tar
          </Button>
          <Button
            variant="outline"
            disabled={remove.isPending}
            onClick={() =>
              remove.mutate(data.id, {
                onSuccess: () => navigate({ to: "/sandboxes" }),
              })
            }
          >
            <Trash2 />
            Delete
          </Button>
          <Button
            disabled={!running || data.kind === "code"}
            render={
              <Link
                to="/view/$sandboxId"
                params={{ sandboxId: data.id }}
                target="_blank"
              />
            }
          >
            <ExternalLink />
            Open desktop
          </Button>
        </>
      }
    >
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          {data.kind !== "code" && (
            <TabsTrigger value="agent">Agent</TabsTrigger>
          )}
          {data.kind !== "macos" && data.kind !== "windows" && (
            <TabsTrigger value="terminal">Terminal</TabsTrigger>
          )}
          <TabsTrigger value="permissions">Permissions</TabsTrigger>
          <TabsTrigger value="network">Network</TabsTrigger>
          <TabsTrigger value="apps">Apps</TabsTrigger>
          <TabsTrigger value="secrets">Secrets</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          {data.kind !== "code" && (
            <TabsTrigger value="profiles">Profiles</TabsTrigger>
          )}
          <TabsTrigger value="snapshots">Snapshots</TabsTrigger>
          {data.kind !== "code" && (
            <TabsTrigger value="share">Share</TabsTrigger>
          )}
          <TabsTrigger value="server">Server</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="mt-4">
          <OverviewTab sandbox={data} />
        </TabsContent>
        <TabsContent value="agent" className="mt-4">
          <AgentTab sandbox={data} />
        </TabsContent>
        <TabsContent value="terminal" className="mt-4">
          <TerminalView sandboxId={data.id} running={running} />
        </TabsContent>
        <TabsContent value="permissions" className="mt-4">
          <PermissionsTab sandboxId={data.id} />
        </TabsContent>
        <TabsContent value="network" className="mt-4">
          <NetworkTab sandboxId={data.id} />
        </TabsContent>
        <TabsContent value="apps" className="mt-4">
          <AppsTab sandboxId={data.id} running={running} />
        </TabsContent>
        <TabsContent value="secrets" className="mt-4 flex flex-col gap-4">
          <VaultSecretsCard sandboxId={data.id} />
          <SecretsTab sandboxId={data.id} />
        </TabsContent>
        <TabsContent value="activity" className="mt-4">
          <ActivityTab sandboxId={data.id} />
        </TabsContent>
        <TabsContent value="profiles" className="mt-4">
          <ProfilesTab
            sandboxId={data.id}
            running={running}
            platform={
              data.kind === "macos" || data.kind === "windows"
                ? data.kind
                : "linux"
            }
          />
        </TabsContent>
        <TabsContent value="snapshots" className="mt-4">
          <SnapshotsTab sandbox={data} />
        </TabsContent>
        <TabsContent value="share" className="mt-4">
          <ShareTab sandbox={data} />
        </TabsContent>
        <TabsContent value="server" className="mt-4">
          <ServerTab sandbox={data} />
        </TabsContent>
      </Tabs>
    </Page>
  )
}

function NewImageNotice({ sandbox }: { sandbox: Sandbox }) {
  const upgrade = useSandboxAction("upgrade")
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-sm text-sky-300">
      <span>
        A newer sandbox image is available. Restarting keeps the home directory;
        anything running is closed.
      </span>
      <Button
        size="sm"
        variant="outline"
        disabled={upgrade.isPending || !!sandbox.job}
        onClick={() => upgrade.mutate(sandbox.id)}
      >
        <RotateCcw />
        Restart on new image
      </Button>
    </div>
  )
}

function OverviewTab({ sandbox }: { sandbox: Sandbox }) {
  const monitoring = useMonitoring()
  const usage = monitoring.data?.usage.find((u) => u.sandbox_id === sandbox.id)

  const details = [
    { label: "Status", value: <StatusBadge status={displayStatus(sandbox)} /> },
    sandbox.kind === "macos" || sandbox.kind === "windows"
      ? {
          label: "Base VM",
          value: (
            <span className="font-mono text-xs">
              {sandbox.base_version ?? "—"}
              {sandbox.boot_seconds !== null &&
                ` · booted in ${sandbox.boot_seconds}s`}
            </span>
          ),
        }
      : {
          label: "Image",
          value: (
            <span className="font-mono text-xs">{sandbox.image ?? "—"}</span>
          ),
        },
    { label: "Created", value: timeAgo(sandbox.created_at) },
    {
      label: "Started",
      value: sandbox.started_at ? timeAgo(sandbox.started_at) : "—",
    },
  ]

  return (
    <div className="flex flex-col gap-4">
      {sandbox.error_message && !sandbox.job && (
        <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {sandbox.error_message}
        </p>
      )}
      {sandbox.job?.last_error &&
        (sandbox.job.waiting ? (
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
            {sandbox.job.last_error}
          </p>
        ) : (
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
            Attempt {sandbox.job.attempts} of {sandbox.job.max_attempts} failed,
            retrying: {sandbox.job.last_error}
          </p>
        ))}
      {sandbox.recovered_at && (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
          Zoo restarted this VM {timeAgo(sandbox.recovered_at)} after it stopped
          responding (no screen updates and no guest heartbeat).
        </p>
      )}
      {sandbox.image_outdated && <NewImageNotice sandbox={sandbox} />}
      {sandbox.unreachable && (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
          The host running this sandbox isn't answering. The sandbox may still
          be running; Zoo keeps checking.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="CPU"
          value={usage ? `${usage.cpu_percent.toFixed(1)}%` : "—"}
          icon={Cpu}
          hint="of 2 vCPU limit"
        />
        <StatCard
          label="Memory"
          value={usage ? formatBytes(usage.memory_usage) : "—"}
          icon={MemoryStick}
          hint={usage ? `of ${formatBytes(usage.memory_limit)}` : undefined}
        />
        <StatCard
          label="Processes"
          value={usage ? String(usage.pids) : "—"}
          icon={Workflow}
        />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          {details.map((item) => (
            <div key={item.label} className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">
                {item.label}
              </span>
              <span className="text-sm">{item.value}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

type ChatItem = {
  kind: string
  text: string
  source?: string
  // the stored message, for its screenshot
  id?: string
  screenshot?: boolean
}

type RunUsage = {
  steps: number
  tokens: number
  cost: number
  elapsed: number
  max_steps: number
  max_seconds: number
  max_tokens: number
  state?: string
}

function duration(seconds: number) {
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

function compact(n: number) {
  return n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(1)}M`
    : n >= 1000
      ? `${Math.round(n / 1000)}k`
      : String(n)
}

const PLATFORMS: Array<{
  value: ChatPlatform
  label: string
  placeholder: string
  env: string
}> = [
  {
    value: "slack",
    label: "Slack",
    placeholder: "Channel ID, e.g. C0123ABCD",
    env: "SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET",
  },
  {
    value: "discord",
    label: "Discord",
    placeholder: "Channel ID",
    env: "DISCORD_BOT_TOKEN",
  },
  {
    value: "whatsapp",
    label: "WhatsApp",
    placeholder: "Phone number, e.g. +15551234567",
    env: "WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN and WHATSAPP_APP_SECRET",
  },
]

function AgentTab({ sandbox }: { sandbox: Sandbox }) {
  return (
    <div className="flex flex-col gap-4">
      <AgentChat sandbox={sandbox} />
      <AgentModel />
      <AgentChannels sandboxId={sandbox.id} />
    </div>
  )
}

function AgentChat({ sandbox }: { sandbox: Sandbox }) {
  const queryClient = useQueryClient()
  // A run being followed live: `cutoff` is how many stored messages precede it, `events` what it has streamed.
  const [live, setLive] = useState<{
    cutoff: number
    events: Array<ChatItem>
    usage: RunUsage | null
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState("")
  const agent = useAgent(sandbox.id, live === null)
  const stop = useStopAgent(sandbox.id)
  const takeover = useTakeover(sandbox.id)
  const reset = useResetAgent(sandbox.id)
  const controller = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const running = sandbox.status === "running"

  async function follow(
    cutoff: number,
    open: (
      onEvent: (event: AgentEvent) => void,
      signal: AbortSignal
    ) => Promise<void>
  ) {
    const abort = new AbortController()
    controller.current = abort
    setError(null)
    setLive({ cutoff, events: [], usage: null })
    try {
      await open((event) => {
        if (event.type === "done") return
        if (event.type === "usage") {
          setLive((l) => l && { ...l, usage: event })
          return
        }
        setLive(
          (l) =>
            l && {
              ...l,
              events: [
                ...l.events,
                {
                  kind: event.type,
                  text: event.text,
                  id: event.id,
                  screenshot: event.screenshot,
                },
              ],
            }
        )
      }, abort.signal)
    } catch (e) {
      if (!abort.signal.aborted) {
        setError(e instanceof Error ? e.message : "The agent stream failed")
      }
    } finally {
      if (!abort.signal.aborted) {
        await queryClient.refetchQueries({
          queryKey: queryKeys.agent(sandbox.id),
        })
        await queryClient.invalidateQueries({
          queryKey: queryKeys.executions(sandbox.id),
        })
        setLive(null)
      }
    }
  }

  const messages = agent.data?.messages
  const busy = live !== null || agent.data?.running === true
  // what the last take over / hand back said, else what the run said when it was last fetched
  const paused = takeover.data
    ? takeover.data.paused_at !== null
    : agent.data?.run?.paused_at != null

  useEffect(() => {
    if (!busy) takeover.reset()
  }, [busy])

  // Follow runs started elsewhere (another tab, the API, Slack, Discord or WhatsApp).
  useEffect(() => {
    if (!agent.data?.running || live !== null || !messages) return
    const lastUser = messages.map((m) => m.kind).lastIndexOf("user")
    void follow(Math.max(lastUser, 0), (onEvent, signal) =>
      api.agent.attach(sandbox.id, onEvent, signal)
    )
  }, [agent.data?.running])

  useEffect(() => () => controller.current?.abort(), [])

  const items: Array<ChatItem> = [
    ...(messages ?? []).slice(0, live ? live.cutoff : undefined).map((m) => ({
      kind: m.kind,
      text: m.content,
      source: m.source,
      id: m.id,
      screenshot: m.has_screenshot,
    })),
    ...(live?.events ?? []),
  ]

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" })
  }, [items.length])

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = draft.trim()
    if (!message || busy) return
    setDraft("")
    void follow(messages?.length ?? 0, (onEvent, signal) =>
      api.agent.send(sandbox.id, message, onEvent, signal)
    )
  }

  const last = items.at(-1)
  const run = agent.data?.run
  const usage: RunUsage | null =
    live?.usage ??
    (run
      ? {
          steps: run.steps,
          tokens: run.tokens,
          cost: run.cost,
          elapsed: run.elapsed_seconds,
          max_steps: run.max_steps,
          max_seconds: run.max_seconds,
          max_tokens: run.max_tokens,
          state: run.state,
        }
      : null)

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <Bot className="size-4" />
            Computer-use agent
          </CardTitle>
          <CardDescription>
            A{" "}
            <a
              href="https://github.com/trycua/cua"
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              CUA
            </a>{" "}
            agent that sees and drives this sandbox's screen
            {agent.data && (
              <>
                {" "}
                with <span className="font-mono">{agent.data.model}</span>
              </>
            )}
            . Watch it live with Open desktop.
          </CardDescription>
        </div>
        <Button
          variant="ghost"
          size="sm"
          disabled={busy || reset.isPending || !messages?.length}
          onClick={() => reset.mutate()}
        >
          <Trash2 />
          Clear
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex h-[28rem] flex-col gap-2 overflow-y-auto rounded-lg border border-border p-3">
          {agent.isPending ? (
            <Skeleton className="h-16" />
          ) : items.length === 0 ? (
            <p className="m-auto max-w-sm text-center text-sm text-muted-foreground">
              Describe a task, like "open Firefox and find the weather in
              Paris". The agent takes screenshots and clicks and types until
              it's done.
            </p>
          ) : (
            items.map((item, i) => (
              <ChatLine key={item.id ?? i} item={item} sandboxId={sandbox.id} />
            ))
          )}
          {busy && last?.kind !== "text" && (
            <span className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              Working…
            </span>
          )}
          <div ref={bottom} />
        </div>
        {usage && <RunLimits usage={usage} busy={busy} />}
        {(error ?? agent.error?.message ?? reset.error?.message) && (
          <p className="text-sm text-destructive">
            {error ??
              agent.error?.message ??
              reset.error?.message ??
              takeover.error?.message}
          </p>
        )}
        <form onSubmit={onSubmit} className="flex gap-2">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              running
                ? "Tell the agent what to do…"
                : "Start the sandbox to chat"
            }
            disabled={!running || busy}
            className="flex-1"
          />
          {busy && (
            <Button
              type="button"
              variant="outline"
              disabled={takeover.isPending}
              title={
                paused
                  ? "Let the agent carry on from the screen as it is now"
                  : "Pause the agent before its next action and use the screen yourself"
              }
              onClick={() => takeover.mutate(!paused)}
            >
              {paused ? <Play /> : <Hand />}
              {paused ? "Hand back" : "Take over"}
            </Button>
          )}
          {busy ? (
            <Button
              type="button"
              variant="outline"
              disabled={stop.isPending}
              onClick={() => stop.mutate()}
            >
              <Square />
              Stop
            </Button>
          ) : (
            <Button type="submit" disabled={!running || !draft.trim()}>
              <Send />
              Send
            </Button>
          )}
        </form>
      </CardContent>
    </Card>
  )
}

function RunLimits({ usage, busy }: { usage: RunUsage; busy: boolean }) {
  const parts = [
    {
      label: "Actions",
      value: `${usage.steps} / ${usage.max_steps}`,
      ratio: usage.steps / usage.max_steps,
    },
    {
      label: "Tokens",
      value: `${compact(usage.tokens)} / ${compact(usage.max_tokens)}`,
      ratio: usage.tokens / usage.max_tokens,
    },
    {
      label: "Time",
      value: `${duration(usage.elapsed)} / ${duration(usage.max_seconds)}`,
      ratio: usage.elapsed / usage.max_seconds,
    },
  ]
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
      <span className="font-medium text-foreground">
        {busy ? "This run" : "Last run"}
        {!busy && usage.state ? ` · ${usage.state}` : ""}
      </span>
      {parts.map((p) => (
        <span key={p.label} className="flex items-center gap-2">
          {p.label}
          <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-muted">
            <span
              className={`absolute inset-y-0 left-0 rounded-full ${p.ratio >= 0.9 ? "bg-destructive" : "bg-primary"}`}
              style={{ width: `${Math.min(p.ratio, 1) * 100}%` }}
            />
          </span>
          <span className="font-mono">{p.value}</span>
        </span>
      ))}
      {usage.cost > 0 && (
        <span className="font-mono">${usage.cost.toFixed(4)}</span>
      )}
    </div>
  )
}

function StepScreenshot({
  sandboxId,
  messageId,
}: {
  sandboxId: string
  messageId: string
}) {
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let current: string | null = null
    let cancelled = false
    api.agent
      .screenshot(sandboxId, messageId)
      .then((u) => {
        if (cancelled) URL.revokeObjectURL(u)
        else setUrl((current = u))
      })
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Couldn't load it")
      )
    return () => {
      cancelled = true
      if (current) URL.revokeObjectURL(current)
    }
  }, [sandboxId, messageId])
  if (error) return <p className="text-xs text-destructive">{error}</p>
  if (!url) return <Skeleton className="h-40 w-full max-w-md" />
  return (
    <a href={url} target="_blank" rel="noreferrer" className="block max-w-md">
      <img
        src={url}
        alt="The screen the agent saw before this action"
        className="rounded-md border border-border"
      />
    </a>
  )
}

function ChatLine({ item, sandboxId }: { item: ChatItem; sandboxId: string }) {
  const [open, setOpen] = useState(false)
  const external =
    item.source && PLATFORMS.find((p) => p.value === item.source)?.label
  switch (item.kind) {
    case "user":
      return (
        <div className="flex flex-col items-end gap-1 self-end">
          {external && <Badge variant="outline">{external}</Badge>}
          <p className="max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm whitespace-pre-wrap text-primary-foreground">
            {item.text}
          </p>
        </div>
      )
    case "text":
      return (
        <p className="max-w-[80%] self-start rounded-lg bg-muted px-3 py-2 text-sm whitespace-pre-wrap">
          {item.text}
        </p>
      )
    case "action":
      return (
        <div className="flex flex-col gap-1">
          <span className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
            <MousePointerClick className="size-3 shrink-0" />
            {item.text}
            {item.screenshot && item.id && (
              <button
                type="button"
                className="flex items-center gap-1 font-sans underline-offset-2 hover:underline"
                onClick={() => setOpen((o) => !o)}
              >
                <ImageIcon className="size-3" />
                {open ? "Hide screen" : "Screen"}
              </button>
            )}
          </span>
          {open && item.id && (
            <StepScreenshot sandboxId={sandboxId} messageId={item.id} />
          )}
        </div>
      )
    case "status":
      return (
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          <Info className="size-3 shrink-0" />
          {item.text}
        </span>
      )
    case "reasoning":
      return (
        <p className="text-xs whitespace-pre-wrap text-muted-foreground italic">
          {item.text}
        </p>
      )
    default:
      return <p className="text-xs text-destructive">{item.text}</p>
  }
}

function AgentModel() {
  const settings = useAgentSettings()
  const save = useSaveAgentSettings()
  const [provider, setProvider] = useState<AgentProviderId>("anthropic")
  const [model, setModel] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [apiBase, setApiBase] = useState("")
  // "" means a key typed in (or none); otherwise the vault secret to use
  const [keySecret, setKeySecret] = useState("")
  const [maxSteps, setMaxSteps] = useState("")
  const [maxMinutes, setMaxMinutes] = useState("")
  const [maxTokens, setMaxTokens] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const vault = useVaultSecrets()

  const data = settings.data
  // Load the saved choice into the form; with none saved, start from the first provider's defaults.
  useEffect(() => {
    if (!data) return
    const current =
      data.providers.find((p) => p.id === data.provider) ?? data.providers[0]
    setProvider(current.id)
    setModel(data.provider ? data.model : current.default_model)
    setApiBase(data.api_base ?? "")
    setApiKey("")
    setKeySecret(data.api_key_secret?.id ?? "")
    // a limit equal to the server's cap is shown blank: "use the cap"
    const own = (value: number, cap: number) =>
      value < cap ? String(value) : ""
    setMaxSteps(own(data.limits.max_steps, data.caps.max_steps))
    setMaxMinutes(
      data.limits.max_seconds < data.caps.max_seconds
        ? String(Math.round(data.limits.max_seconds / 60))
        : ""
    )
    setMaxTokens(own(data.limits.max_tokens, data.caps.max_tokens))
  }, [data])

  if (!data) {
    return settings.error ? null : <Skeleton className="h-48" />
  }

  const providers = data.providers.map((p) => ({ value: p.id, label: p.label }))
  const selected = data.providers.find((p) => p.id === provider)!
  const keySaved = data.has_api_key && data.provider === provider
  const secretItems = [
    { value: "", label: "No vault secret" },
    ...(vault.data ?? []).map((v) => ({ value: v.id, label: v.name })),
  ]

  function onProviderChange(next: AgentProviderId) {
    const target = data!.providers.find((p) => p.id === next)!
    setProvider(next)
    setModel(next === data!.provider ? data!.model : target.default_model)
    setApiBase(next === data!.provider ? (data!.api_base ?? "") : "")
    setApiKey("")
    setKeySecret(
      next === data!.provider ? (data!.api_key_secret?.id ?? "") : ""
    )
    setSaved(false)
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const number = (value: string) =>
      value.trim() ? Number(value.trim()) : undefined
    const minutes = number(maxMinutes)
    const parsed = agentSettingsInputSchema.safeParse({
      provider,
      model,
      api_key: apiKey.trim() || undefined,
      // a typed key wins; otherwise the chosen vault secret, or "" to drop the key
      api_key_secret_id: apiKey.trim() ? undefined : keySecret,
      api_base: apiBase,
      max_steps: number(maxSteps),
      max_seconds: minutes === undefined ? undefined : Math.round(minutes * 60),
      max_tokens: number(maxTokens),
    })
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid settings")
      return
    }
    if (
      selected.needs_key &&
      !selected.server_key &&
      !parsed.data.api_key &&
      !parsed.data.api_key_secret_id
    ) {
      setError(`Enter your ${selected.label} API key.`)
      return
    }
    setError(null)
    save.mutate(parsed.data, { onSuccess: () => setSaved(true) })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cog className="size-4" />
          Model
        </CardTitle>
        <CardDescription>
          {data.provider ? (
            <>
              The agent uses <span className="font-mono">{data.model}</span>{" "}
              from {data.providers.find((p) => p.id === data.provider)?.label}.
            </>
          ) : (
            <>
              The agent uses the server default,{" "}
              <span className="font-mono">{data.default_model}</span>.
            </>
          )}{" "}
          Applies to the agent in all your sandboxes. Pick a model that supports
          computer use. Keys are kept in the vault.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            <Select
              items={providers}
              value={provider}
              onValueChange={(value) => value && onProviderChange(value)}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={model}
              onChange={(e) => {
                setModel(e.target.value)
                setSaved(false)
              }}
              placeholder={selected.default_model}
              aria-label="Model"
              className="min-w-48 flex-1 font-mono"
            />
          </div>
          {selected.needs_key && (
            <div className="flex flex-wrap gap-2">
              <Select
                items={secretItems}
                value={keySecret}
                onValueChange={(value) => {
                  setKeySecret(value ?? "")
                  setSaved(false)
                }}
              >
                <SelectTrigger className="w-56" aria-label="Key from the vault">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {secretItems.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value)
                  setSaved(false)
                }}
                aria-label="API key"
                className="min-w-48 flex-1"
                placeholder={
                  keySaved && keySecret
                    ? "Or enter a new key to save it to the vault"
                    : selected.server_key
                      ? "Optional. The server's key is used if blank"
                      : `${selected.label} API key (saved to the vault)`
                }
              />
            </div>
          )}
          <Input
            value={apiBase}
            onChange={(e) => {
              setApiBase(e.target.value)
              setSaved(false)
            }}
            aria-label="Base URL"
            placeholder={
              selected.default_base
                ? `Base URL, default ${selected.default_base}`
                : "Base URL (optional, for a proxy or gateway)"
            }
            className="font-mono"
          />
          <div className="flex flex-col gap-1.5">
            <span className="text-xs text-muted-foreground">
              Limits per task. Blank uses the server's cap.
            </span>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <Input
                inputMode="numeric"
                value={maxSteps}
                onChange={(e) => {
                  setMaxSteps(e.target.value)
                  setSaved(false)
                }}
                aria-label="Most actions per task"
                placeholder={`Actions, up to ${data.caps.max_steps}`}
              />
              <Input
                inputMode="numeric"
                value={maxMinutes}
                onChange={(e) => {
                  setMaxMinutes(e.target.value)
                  setSaved(false)
                }}
                aria-label="Most minutes per task"
                placeholder={`Minutes, up to ${Math.round(data.caps.max_seconds / 60)}`}
              />
              <Input
                inputMode="numeric"
                value={maxTokens}
                onChange={(e) => {
                  setMaxTokens(e.target.value)
                  setSaved(false)
                }}
                aria-label="Most tokens per task"
                placeholder={`Tokens, up to ${compact(data.caps.max_tokens)}`}
              />
            </div>
          </div>
          {(error ?? save.error?.message) && (
            <p className="text-sm text-destructive">
              {error ?? save.error?.message}
            </p>
          )}
          <div className="flex items-center justify-end gap-2">
            {saved && !save.isPending && (
              <span className="text-xs text-muted-foreground">Saved</span>
            )}
            {data.provider && (
              <Button
                type="button"
                variant="ghost"
                disabled={save.isPending}
                onClick={() => {
                  setSaved(false)
                  save.mutate(null)
                }}
              >
                Use server default
              </Button>
            )}
            <Button type="submit" disabled={save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" />}
              Save
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function AgentChannels({ sandboxId }: { sandboxId: string }) {
  const channels = useAgentChannels(sandboxId)
  const integrations = useIntegrations()
  const add = useAddAgentChannel(sandboxId)
  const remove = useRemoveAgentChannel(sandboxId)
  const update = useUpdateAgentChannel(sandboxId)
  const [platform, setPlatform] = useState<ChatPlatform>("slack")
  const [externalId, setExternalId] = useState("")
  const [allowed, setAllowed] = useState("")
  const [error, setError] = useState<string | null>(null)

  const selected = PLATFORMS.find((p) => p.value === platform)!
  const integration = integrations.data?.find((i) => i.platform === platform)

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const parsed = agentChannelInputSchema.safeParse({
      platform,
      external_id: externalId,
      allowed_users: platform === "whatsapp" ? [] : splitUsers(allowed),
    })
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid channel")
      return
    }
    if (platform !== "whatsapp" && parsed.data.allowed_users.length === 0) {
      setError(
        "List the user IDs that may command this sandbox, or * for anyone in the channel."
      )
      return
    }
    setError(null)
    add.mutate(parsed.data, {
      onSuccess: () => {
        setExternalId("")
        setAllowed("")
      },
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessagesSquare className="size-4" />
          Chat channels
        </CardTitle>
        <CardDescription>
          Messages in a linked Slack or Discord channel, or from a linked
          WhatsApp number, go to this sandbox's agent and its progress streams
          back. In Slack and Discord only the user IDs you allow can command it
          (* lets anyone in the channel); a WhatsApp link only answers its own
          number. Send <span className="font-mono">stop</span> to cancel a task
          or <span className="font-mono">reset</span> to clear the conversation.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form onSubmit={onSubmit} className="flex flex-wrap gap-2">
          <Select
            items={PLATFORMS}
            value={platform}
            onValueChange={(value) => value && setPlatform(value)}
          >
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PLATFORMS.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={externalId}
            onChange={(e) => setExternalId(e.target.value)}
            placeholder={selected.placeholder}
            className="min-w-48 flex-1"
          />
          {platform !== "whatsapp" && (
            <Input
              value={allowed}
              onChange={(e) => setAllowed(e.target.value)}
              placeholder={
                platform === "slack"
                  ? "Allowed user IDs, e.g. U0123ABCD, U0456EFGH, or *"
                  : "Allowed user IDs, comma-separated, or *"
              }
              aria-label="Allowed user IDs"
              className="min-w-64 basis-full"
            />
          )}
          <Button type="submit" disabled={add.isPending}>
            <Plus />
            Link
          </Button>
        </form>
        {integration && !integration.configured && (
          <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            {selected.label} isn't set up on this server yet. Set{" "}
            <span className="font-mono">{selected.env}</span> on the API
            {integration.webhook_path && (
              <>
                {" "}
                and point the webhook at{" "}
                <span className="font-mono break-all">
                  {apiUrl(integration.webhook_path)}
                </span>
              </>
            )}
            .
          </p>
        )}
        {(error ??
          add.error?.message ??
          remove.error?.message ??
          update.error?.message) && (
          <p className="text-sm text-destructive">
            {error ??
              add.error?.message ??
              remove.error?.message ??
              update.error?.message}
          </p>
        )}
        {channels.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {channels.data.map((c) => (
              <ChannelRow
                key={c.id}
                channel={c}
                saving={update.isPending}
                removing={remove.isPending}
                onSave={(users) =>
                  update.mutate({ channelId: c.id, allowedUsers: users })
                }
                onRemove={() => remove.mutate(c.id)}
              />
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No linked channels.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function splitUsers(value: string) {
  return value
    .split(/[\s,]+/)
    .map((u) => u.trim())
    .filter(Boolean)
}

function ChannelRow({
  channel,
  saving,
  removing,
  onSave,
  onRemove,
}: {
  channel: AgentChannel
  saving: boolean
  removing: boolean
  onSave: (users: Array<string>) => void
  onRemove: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(channel.allowed_users.join(", "))
  const anyone = channel.allowed_users.includes("*")
  return (
    <div className="flex flex-col gap-2 px-3 py-2 text-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant="secondary">
            {PLATFORMS.find((p) => p.value === channel.platform)?.label}
          </Badge>
          <span className="font-mono">{channel.external_id}</span>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          Linked {timeAgo(channel.created_at)}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Unlink channel"
            disabled={removing}
            onClick={onRemove}
          >
            <Trash2 />
          </Button>
        </div>
      </div>
      {channel.platform !== "whatsapp" &&
        (editing ? (
          <form
            className="flex flex-wrap gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              const users = splitUsers(draft)
              if (!users.length) return
              onSave(users)
              setEditing(false)
            }}
          >
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label="Allowed user IDs"
              className="min-w-48 flex-1 font-mono"
            />
            <Button
              type="submit"
              size="sm"
              disabled={saving || !splitUsers(draft).length}
            >
              Save
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setEditing(false)}
            >
              Cancel
            </Button>
          </form>
        ) : (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {anyone ? (
              <span className="text-amber-600 dark:text-amber-400">
                Anyone in the channel can command this sandbox
              </span>
            ) : (
              <span>
                Allowed:{" "}
                <span className="font-mono">
                  {channel.allowed_users.join(", ")}
                </span>
              </span>
            )}
            <button
              type="button"
              className="underline underline-offset-2"
              onClick={() => {
                setDraft(channel.allowed_users.join(", "))
                setEditing(true)
              }}
            >
              Edit
            </button>
          </div>
        ))}
    </div>
  )
}

function PolicyNotice() {
  return (
    <p className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
      <ShieldCheck className="size-4 shrink-0" />
      Applied live to the running sandbox and re-applied on every start. Domains
      are resolved to IPs when the rule is applied.
    </p>
  )
}

function PermissionsTab({ sandboxId }: { sandboxId: string }) {
  const permissions = usePermissions(sandboxId)
  const setPermission = useSetPermission(sandboxId)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" />
          Agent permissions
        </CardTitle>
        <CardDescription>
          What agents and API clients may do in this sandbox. Enforced by the
          zoo API.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col divide-y divide-border">
        {permissions.isPending && <Skeleton className="h-24" />}
        {permissions.error && (
          <p className="text-sm text-destructive">
            {permissions.error.message}
          </p>
        )}
        {permissions.data?.map((p) => (
          <div
            key={`${p.permission}.${p.action}`}
            className="flex items-center justify-between py-3 first:pt-0 last:pb-0"
          >
            <div>
              <p className="text-sm font-medium">{p.label}</p>
              <p className="font-mono text-xs text-muted-foreground">
                {p.permission}.{p.action}
              </p>
            </div>
            <Switch
              checked={p.effect === "allow"}
              disabled={setPermission.isPending}
              onCheckedChange={(checked) =>
                setPermission.mutate({
                  permission: p.permission,
                  action: p.action,
                  effect: checked ? "allow" : "deny",
                })
              }
            />
          </div>
        ))}
        {setPermission.error && (
          <p className="pt-3 text-sm text-destructive">
            {setPermission.error.message}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function NetworkTab({ sandboxId }: { sandboxId: string }) {
  const network = useNetwork(sandboxId)
  const setNetwork = useSetNetwork(sandboxId)
  const addRule = useAddRule(sandboxId)
  const removeRule = useRemoveRule(sandboxId)
  const [rule, setRule] = useState<NetworkRuleInput>({
    rule_type: "domain",
    value: "",
    effect: "allow",
  })
  const [ruleError, setRuleError] = useState<string>()

  function onAddRule(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const parsed = networkRuleInputSchema.safeParse(rule)
    if (!parsed.success) {
      setRuleError(z.flattenError(parsed.error).fieldErrors.value?.[0])
      return
    }
    setRuleError(undefined)
    addRule.mutate(parsed.data, {
      onSuccess: () => setRule((r) => ({ ...r, value: "" })),
    })
  }

  if (network.isPending) return <Skeleton className="h-64 rounded-xl" />
  if (network.error)
    return <p className="text-sm text-destructive">{network.error.message}</p>

  const policy = network.data
  const error = setNetwork.error ?? addRule.error ?? removeRule.error

  return (
    <div className="flex flex-col gap-4">
      <PolicyNotice />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Globe className="size-4" />
            Egress policy
          </CardTitle>
          <CardDescription>
            Default action for outbound traffic that no rule matches.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col divide-y divide-border">
          <div className="flex items-center justify-between pb-3">
            <div>
              <p className="text-sm font-medium">Block by default</p>
              <p className="text-xs text-muted-foreground">
                Only destinations allowed below are reachable.
              </p>
            </div>
            <Switch
              checked={policy.default_action === "deny"}
              disabled={setNetwork.isPending}
              onCheckedChange={(checked) =>
                setNetwork.mutate({
                  default_action: checked ? "deny" : "allow",
                  allow_dns: policy.allow_dns,
                })
              }
            />
          </div>
          <div className="flex items-center justify-between pt-3">
            <div>
              <p className="text-sm font-medium">Allow DNS</p>
              <p className="text-xs text-muted-foreground">
                Let the sandbox resolve hostnames.
              </p>
            </div>
            <Switch
              checked={policy.allow_dns}
              disabled={setNetwork.isPending}
              onCheckedChange={(checked) =>
                setNetwork.mutate({
                  default_action: policy.default_action,
                  allow_dns: checked,
                })
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Rules</CardTitle>
          <CardDescription>
            Allow or deny specific destinations.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <form onSubmit={onAddRule} className="flex flex-wrap gap-2">
            <Select
              items={RULE_TYPES}
              value={rule.rule_type}
              onValueChange={(value) =>
                value && setRule((r) => ({ ...r, rule_type: value }))
              }
            >
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RULE_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={rule.value}
              onChange={(e) =>
                setRule((r) => ({ ...r, value: e.target.value }))
              }
              placeholder={
                rule.rule_type === "domain"
                  ? "github.com"
                  : rule.rule_type === "ip"
                    ? "1.1.1.1"
                    : "10.0.0.0/8"
              }
              className="h-8 min-w-48 flex-1"
            />
            <Select
              items={EFFECTS}
              value={rule.effect}
              onValueChange={(value) =>
                value && setRule((r) => ({ ...r, effect: value }))
              }
            >
              <SelectTrigger className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EFFECTS.map((e) => (
                  <SelectItem key={e.value} value={e.value}>
                    {e.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button type="submit" disabled={addRule.isPending}>
              <Plus />
              Add rule
            </Button>
          </form>
          {(ruleError || error) && (
            <p className="text-sm text-destructive">
              {ruleError ?? error?.message}
            </p>
          )}
          {policy.rules.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No rules yet.
            </p>
          ) : (
            <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
              {policy.rules.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span
                      className={
                        r.effect === "allow"
                          ? "w-12 text-xs font-medium text-emerald-400"
                          : "w-12 text-xs font-medium text-red-400"
                      }
                    >
                      {r.effect}
                    </span>
                    <span className="w-14 text-xs text-muted-foreground uppercase">
                      {r.rule_type}
                    </span>
                    <span className="truncate font-mono">{r.value}</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Remove rule"
                    disabled={removeRule.isPending}
                    onClick={() => removeRule.mutate(r.id)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function AppsTab({
  sandboxId,
  running,
}: {
  sandboxId: string
  running: boolean
}) {
  const apps = useApps(sandboxId, running)
  const setApp = useSetApp(sandboxId)

  if (!running) {
    return (
      <EmptyState
        icon={AppWindow}
        title="Sandbox isn't running"
        description="Apps are read from the running desktop."
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <PolicyNotice />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AppWindow className="size-4" />
            Installed apps
          </CardTitle>
          <CardDescription>
            Desktop apps found in this sandbox and whether they may be launched.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col divide-y divide-border">
          {apps.isPending && <Skeleton className="h-48" />}
          {apps.error && (
            <p className="text-sm text-destructive">{apps.error.message}</p>
          )}
          {apps.data?.map((app) => (
            <div
              key={app.binary}
              className="flex items-center justify-between py-2.5 first:pt-0 last:pb-0"
            >
              <div>
                <p className="text-sm font-medium">{app.name}</p>
                <p className="font-mono text-xs text-muted-foreground">
                  {app.binary}
                </p>
              </div>
              <Switch
                checked={app.effect === "allow"}
                disabled={setApp.isPending}
                onCheckedChange={(checked) =>
                  setApp.mutate({
                    binary: app.binary,
                    effect: checked ? "allow" : "deny",
                  })
                }
              />
            </div>
          ))}
          {setApp.error && (
            <p className="pt-3 text-sm text-destructive">
              {setApp.error.message}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function SecretsTab({ sandboxId }: { sandboxId: string }) {
  const secrets = useSecrets(sandboxId)
  const setSecret = useSetSecret(sandboxId)
  const removeSecret = useRemoveSecret(sandboxId)
  const [error, setError] = useState<string | null>(null)

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const parsed = secretInputSchema.safeParse(
      Object.fromEntries(new FormData(form))
    )
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid secret")
      return
    }
    setError(null)
    setSecret.mutate(parsed.data, { onSuccess: () => form.reset() })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4" />
          Secrets
        </CardTitle>
        <CardDescription>
          Encrypted at rest and injected as environment variables. Changes apply
          on the next start.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
          <Input name="name" placeholder="API_TOKEN" className="sm:w-56" />
          <Input
            name="value"
            type="password"
            placeholder="Value"
            className="flex-1"
          />
          <Button type="submit" disabled={setSecret.isPending}>
            <Plus />
            Save secret
          </Button>
        </form>
        {(error ?? setSecret.error) && (
          <p className="text-sm text-destructive">
            {error ?? setSecret.error?.message}
          </p>
        )}
        {secrets.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {secrets.data.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <span className="font-mono">{s.name}</span>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  Added {timeAgo(s.created_at)}
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Remove secret"
                    disabled={removeSecret.isPending}
                    onClick={() => removeSecret.mutate(s.id)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No secrets yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function VaultSecretsCard({ sandboxId }: { sandboxId: string }) {
  const vault = useVaultSecrets()
  const attached = useAttachedSecrets(sandboxId)
  const toggle = useToggleAttachedSecret(sandboxId)
  const on = new Set((attached.data ?? []).map((s) => s.id))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Vault className="size-4" />
          From vault
        </CardTitle>
        <CardDescription>
          Shared secrets from your{" "}
          <Link to="/vault" className="underline underline-offset-2">
            vault
          </Link>
          . Only the ones switched on reach this sandbox, on its next start.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {toggle.error && (
          <p className="text-sm text-destructive">{toggle.error.message}</p>
        )}
        {vault.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {vault.data.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="font-mono">{s.name}</span>
                  {s.description && (
                    <span className="truncate text-xs text-muted-foreground">
                      {s.description}
                    </span>
                  )}
                </div>
                <Switch
                  checked={on.has(s.id)}
                  disabled={toggle.isPending || attached.isLoading}
                  onCheckedChange={(checked) =>
                    toggle.mutate({ id: s.id, attach: checked })
                  }
                />
              </div>
            ))}
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-muted-foreground">
            Your vault is empty.{" "}
            <Link to="/vault" className="underline underline-offset-2">
              Add a secret
            </Link>
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function ActivityTab({ sandboxId }: { sandboxId: string }) {
  const executions = useExecutions(sandboxId)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="size-4" />
          Activity
        </CardTitle>
        <CardDescription>
          Every tool call made through the API, MCP or the agent.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {executions.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {executions.data.map((e) => (
              <div key={e.id} className="flex flex-col gap-1 px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono">{e.tool_name}</span>
                  <span className="text-xs text-muted-foreground">
                    <span
                      className={
                        e.status === "failed"
                          ? "text-red-400"
                          : e.status === "completed"
                            ? "text-emerald-400"
                            : ""
                      }
                    >
                      {e.status}
                    </span>{" "}
                    · {timeAgo(e.created_at)}
                  </span>
                </div>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {e.error_message ?? e.input}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No tool calls yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function ProfilesTab({
  sandboxId,
  running,
  platform,
}: {
  sandboxId: string
  running: boolean
  platform: string
}) {
  const allProfiles = useProfiles()
  const profiles = {
    data: allProfiles.data?.filter((p) => p.platform === platform),
  }
  const apps = useProfileApps(platform)
  const capture = useCaptureProfile(sandboxId)
  const apply = useApplyProfile(sandboxId)
  const [app, setApp] = useState("firefox")
  const [name, setName] = useState("")
  const appItems = Object.keys(apps.data ?? { firefox: "" }).map((a) => ({
    value: a,
    label: a,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>App profiles</CardTitle>
        <CardDescription>
          Save an app's logins, cookies and settings from this sandbox, then
          load them into any sandbox. Saving under an existing name adds a
          version; load the latest or any earlier one. Loading is refused while
          the app is running, so quit it first.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault()
            if (!name.trim()) return
            capture.mutate(
              { name: name.trim(), app },
              { onSuccess: () => setName("") }
            )
          }}
        >
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Profile name"
            className="flex-1"
          />
          <Select
            items={appItems}
            value={app}
            onValueChange={(next) => next && setApp(next)}
          >
            <SelectTrigger className="sm:w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {appItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="submit"
            disabled={!running || capture.isPending || !name.trim()}
          >
            <Plus />
            {capture.isPending ? "Saving…" : "Save from sandbox"}
          </Button>
        </form>
        {(capture.error ?? apply.error) && (
          <p className="text-sm text-destructive">
            {(capture.error ?? apply.error)?.message}
          </p>
        )}
        {profiles.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {profiles.data.map((p) => (
              <ProfileRow
                key={p.id}
                profile={p}
                running={running}
                apply={apply}
                saving={capture.isPending}
                onSaveVersion={() =>
                  capture.mutate({ name: p.name, app: p.app, profile_id: p.id })
                }
              />
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No saved profiles yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function ProfileRow({
  profile,
  running,
  apply,
  saving,
  onSaveVersion,
}: {
  profile: Profile
  running: boolean
  apply: ReturnType<typeof useApplyProfile>
  saving: boolean
  onSaveVersion: () => void
}) {
  const versions = useProfileVersions(profile.versions > 1 ? profile.id : null)
  const removeVersion = useRemoveProfileVersion()
  // "" loads the latest
  const [version, setVersion] = useState("")
  const items = [
    { value: "", label: `Latest (v${profile.version})` },
    ...(versions.data ?? []).slice(1).map((v) => ({
      value: String(v.version),
      label: `v${v.version} · ${timeAgo(v.created_at)}`,
    })),
  ]
  const mine = apply.variables?.profileId === profile.id
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
      <div>
        <div className="font-medium">{profile.name}</div>
        <div className="text-xs text-muted-foreground">
          {profile.app} · v{profile.version}
          {profile.versions > 1 && ` (${profile.versions} versions)`} ·{" "}
          {formatBytes(profile.size_bytes)} ·{" "}
          {timeAgo(profile.updated_at ?? profile.created_at)}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {profile.versions > 1 && (
          <Select
            items={items}
            value={version}
            onValueChange={(next) => setVersion(next ?? "")}
          >
            <SelectTrigger className="w-44" aria-label="Version to load">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {items.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {version && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete v${version}`}
            disabled={removeVersion.isPending}
            onClick={() =>
              removeVersion.mutate(
                { id: profile.id, version: Number(version) },
                { onSuccess: () => setVersion("") }
              )
            }
          >
            <Trash2 />
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={!running || saving}
          onClick={onSaveVersion}
          title="Save this sandbox's app data as a new version"
        >
          Save new version
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!running || apply.isPending}
          onClick={() =>
            apply.mutate({
              profileId: profile.id,
              version: version ? Number(version) : undefined,
            })
          }
        >
          {apply.isPending && mine
            ? "Loading…"
            : apply.isSuccess && mine
              ? "Loaded"
              : "Load"}
        </Button>
      </div>
      {removeVersion.error && (
        <p className="basis-full text-xs text-destructive">
          {removeVersion.error.message}
        </p>
      )}
    </div>
  )
}

const SNAPSHOT_STATES = {
  creating: "Taking…",
  ready: "Ready",
  failed: "Failed",
}

function SnapshotsTab({ sandbox }: { sandbox: Sandbox }) {
  const snapshots = useSnapshots(sandbox.id)
  const create = useCreateSnapshot()
  const restore = useRestoreSnapshot()
  const remove = useRemoveSnapshot()
  const [name, setName] = useState("")
  const vm = sandbox.kind === "macos" || sandbox.kind === "windows"
  const busy = sandbox.job !== null
  const stopped = sandbox.status === "stopped"
  const canTake = !busy && (stopped || (!vm && sandbox.status === "running"))
  const error = create.error ?? restore.error ?? remove.error

  return (
    <Card>
      <CardHeader>
        <CardTitle>Snapshots</CardTitle>
        <CardDescription>
          Copies of the {vm ? "VM's disk" : "home folder"}, kept on the
          sandbox&apos;s server.{" "}
          {vm
            ? "Stop the VM to take one."
            : "Taken while it runs, a snapshot is like pulling the plug: files being written may be cut short."}{" "}
          Restoring replaces the {vm ? "disk" : "home folder"} and needs the
          sandbox stopped. A sandbox with snapshots stays on its server.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate(
              { sandboxId: sandbox.id, name: name.trim() },
              { onSuccess: () => setName("") }
            )
          }}
        >
          <Input
            placeholder="Name (optional)"
            value={name}
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
            className="sm:w-64"
          />
          <Button type="submit" disabled={!canTake || create.isPending}>
            <History />
            Take snapshot
          </Button>
        </form>
        {!canTake && !busy && (
          <p className="text-sm text-muted-foreground">
            {vm
              ? "Stop the sandbox to snapshot its disk."
              : `The sandbox is ${sandbox.status}.`}
          </p>
        )}
        {error && <p className="text-sm text-destructive">{error.message}</p>}
        {snapshots.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : !snapshots.data?.length ? (
          <p className="text-sm text-muted-foreground">No snapshots yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {snapshots.data.map((snap) => (
              <li
                key={snap.id}
                className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium">{snap.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {SNAPSHOT_STATES[snap.state]}
                    {snap.state === "ready" &&
                      ` · ${formatBytes(snap.size_bytes)}`}
                    {` · ${relativeTime(snap.created_at)}`}
                  </div>
                  {snap.error && (
                    <div className="text-xs text-destructive">{snap.error}</div>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={
                      snap.state !== "ready" ||
                      !stopped ||
                      busy ||
                      restore.isPending
                    }
                    title={stopped ? undefined : "Stop the sandbox to restore"}
                    onClick={() =>
                      restore.mutate({ sandboxId: sandbox.id, id: snap.id })
                    }
                  >
                    <RotateCcw />
                    Restore
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={snap.state === "creating" || remove.isPending}
                    onClick={() =>
                      remove.mutate({ sandboxId: sandbox.id, id: snap.id })
                    }
                  >
                    <Trash2 />
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

const LINK_EXPIRY = [
  { value: "15", label: "15 minutes" },
  { value: "60", label: "1 hour" },
  { value: "480", label: "8 hours" },
  { value: "1440", label: "24 hours" },
]

const LINK_MODES: Record<ViewerLinkMode, string> = {
  view: "View only",
  control: "View and control",
}

function ShareTab({ sandbox }: { sandbox: Sandbox }) {
  const links = useViewerLinks(sandbox.id)
  const create = useCreateViewerLink(sandbox.id)
  const revoke = useRevokeViewerLink(sandbox.id)
  const [mode, setMode] = useState<ViewerLinkMode>("view")
  const [expiry, setExpiry] = useState("60")
  const [endUser, setEndUser] = useState("")
  const [copied, setCopied] = useState<string | null>(null)
  const made = create.data
  const error = create.error ?? revoke.error ?? links.error

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(what)
    } catch {
      setCopied(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Share the screen</CardTitle>
        <CardDescription>
          A link opens this sandbox&apos;s screen without signing in: for an end
          user, a support person, or an iframe in your own app (the API&apos;s
          ZOO_EMBED_ORIGINS must list its origin). View-only links can&apos;t
          type or click. With control, acting while the agent runs takes the
          screen over until it is handed back.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-2 sm:flex-row sm:flex-wrap"
          onSubmit={(event) => {
            event.preventDefault()
            setCopied(null)
            create.mutate({
              mode,
              expires_in_minutes: Number(expiry),
              end_user: endUser.trim() || null,
            })
          }}
        >
          <Select
            value={mode}
            onValueChange={(value) => value && setMode(value)}
          >
            <SelectTrigger className="sm:w-48">
              <SelectValue>{LINK_MODES[mode]}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(LINK_MODES).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={expiry}
            onValueChange={(value) => value && setExpiry(value)}
          >
            <SelectTrigger className="sm:w-40">
              <SelectValue>
                {LINK_EXPIRY.find((e) => e.value === expiry)?.label}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {LINK_EXPIRY.map((e) => (
                <SelectItem key={e.value} value={e.value}>
                  {e.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="For (optional), e.g. alice@example.com"
            value={endUser}
            maxLength={200}
            onChange={(event) => setEndUser(event.target.value)}
            className="sm:w-64"
          />
          <Button type="submit" disabled={create.isPending}>
            <Link2 />
            Create link
          </Button>
        </form>
        {made && (
          <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
            <p className="text-sm">
              Copy the link now; it isn&apos;t shown again.
            </p>
            <div className="flex gap-2">
              <Input readOnly value={made.url} className="font-mono text-xs" />
              <Button
                type="button"
                variant="outline"
                onClick={() => void copy(made.url, "url")}
              >
                {copied === "url" ? <Check /> : <Copy />}
                Copy
              </Button>
            </div>
            <div className="flex gap-2">
              <Input
                readOnly
                value={`<iframe src="${made.url}" style="width:100%;height:600px;border:0"></iframe>`}
                className="font-mono text-xs"
              />
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  void copy(
                    `<iframe src="${made.url}" style="width:100%;height:600px;border:0"></iframe>`,
                    "iframe"
                  )
                }
              >
                {copied === "iframe" ? <Check /> : <Copy />}
                Embed
              </Button>
            </div>
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error.message}</p>}
        {links.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : !links.data?.length ? (
          <p className="text-sm text-muted-foreground">No links yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {links.data.map((link) => {
              const expired =
                new Date(`${link.expires_at.replace(" ", "T")}Z`) < new Date()
              const live = !link.revoked_at && !expired
              return (
                <li
                  key={link.id}
                  className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      {LINK_MODES[link.mode]}
                      {link.end_user && (
                        <span className="text-muted-foreground">
                          for {link.end_user}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {link.revoked_at
                        ? `Revoked ${relativeTime(link.revoked_at)}`
                        : expired
                          ? `Expired ${relativeTime(link.expires_at)}`
                          : `Expires ${relativeTime(link.expires_at)}`}
                      {` · made ${relativeTime(link.created_at)}`}
                    </div>
                  </div>
                  {live && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(link.id)}
                    >
                      <Trash2 />
                      Revoke
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function ServerTab({ sandbox }: { sandbox: Sandbox }) {
  const servers = useServers()
  const move = useMoveSandbox(sandbox.id)
  const current = sandbox.server_id ?? "local"
  const [target, setTarget] = useState(current)
  const items = [
    { value: "local", label: "This machine" },
    ...(servers.data ?? []).map((s) => ({ value: s.id, label: s.name })),
  ]
  const busy =
    sandbox.status === "running" ||
    sandbox.status === "provisioning" ||
    sandbox.job !== null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Server</CardTitle>
        <CardDescription>
          Move this sandbox and its home folder to another machine. Stop it
          first.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Select
            items={items}
            value={target}
            onValueChange={(next) => next && setTarget(next)}
          >
            <SelectTrigger className="sm:w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {items.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            disabled={busy || target === current || move.isPending}
            onClick={() => move.mutate(target === "local" ? null : target)}
          >
            {move.isPending ? "Moving…" : "Move sandbox"}
          </Button>
        </div>
        {busy && (
          <p className="text-sm text-muted-foreground">
            Stop the sandbox to move it.
          </p>
        )}
        {move.error && (
          <p className="text-sm text-destructive">{move.error.message}</p>
        )}
      </CardContent>
    </Card>
  )
}
