import { useQueryClient } from "@tanstack/react-query"
import {
  Bot,
  Hand,
  ImageIcon,
  Info,
  Loader2,
  MousePointerClick,
  Play,
  Send,
  Square,
  Trash2,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { AgentChannels } from "@/components/sandbox/agent-channels"
import { AgentModel } from "@/components/sandbox/agent-model"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  api,
  queryKeys,
  useAgent,
  useResetAgent,
  useStopAgent,
  useTakeover,
} from "@/lib/api_client"
import type { AgentEvent, ChatPlatform, Sandbox } from "@/lib/api_client"

export type ChatItem = {
  kind: string
  text: string
  source?: string
  // the stored message, for its screenshot
  id?: string
  screenshot?: boolean
}

export type RunUsage = {
  steps: number
  tokens: number
  cost: number
  elapsed: number
  max_steps: number
  max_seconds: number
  max_tokens: number
  state?: string
}

export function duration(seconds: number) {
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

export function compact(n: number) {
  return n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(1)}M`
    : n >= 1000
      ? `${Math.round(n / 1000)}k`
      : String(n)
}

export const PLATFORMS: Array<{
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

/** The task a demo sandbox suggests in its Agent tab. */
export const DEMO_TASK =
  "Open Firefox, go to news.ycombinator.com, open the top story and summarise it in three bullet points."

export function AgentTab({ sandbox }: { sandbox: Sandbox }) {
  return (
    <div className="flex flex-col gap-4">
      <AgentChat sandbox={sandbox} />
      <AgentModel />
      <AgentChannels sandboxId={sandbox.id} />
    </div>
  )
}

export function AgentChat({ sandbox }: { sandbox: Sandbox }) {
  const queryClient = useQueryClient()
  // A run being followed live: `cutoff` is how many stored messages precede it, `events` what it has streamed.
  const [live, setLive] = useState<{
    cutoff: number
    events: Array<ChatItem>
    usage: RunUsage | null
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  // a demo sandbox (from onboarding) starts with a task to try
  const [draft, setDraft] = useState(
    sandbox.labels.demo === "true" ? DEMO_TASK : ""
  )
  const agent = useAgent(sandbox.id)
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

export function RunLimits({ usage, busy }: { usage: RunUsage; busy: boolean }) {
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

export function StepScreenshot({
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

export function ChatLine({
  item,
  sandboxId,
}: {
  item: ChatItem
  sandboxId: string
}) {
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
