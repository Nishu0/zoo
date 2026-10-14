import { Link, createFileRoute } from "@tanstack/react-router"
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  Play,
  Plus,
  Server,
  Square,
  Terminal,
  Trash2,
} from "lucide-react"
import { useState } from "react"

import { EmptyState, Page } from "@/components/page"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useBaseAction,
  useBaseSetup,
  useBaseStatus,
  useCreateNodeToken,
  useKubernetes,
  useKubernetesCheck,
  useMonitoring,
  usePool,
  useRemoveServer,
  useServerStatus,
  useServers,
  useSetPool,
} from "@/lib/api_client"
import type {
  Kubernetes,
  NodeInfo,
  PoolEntry,
  ServerPlatform,
} from "@/lib/api_client"
import { formatBytes, relativeTime } from "@/lib/utils"

export const Route = createFileRoute("/_app/servers")({
  component: ServersPage,
})

function ServersPage() {
  const monitoring = useMonitoring()
  const kubernetes = useKubernetes()
  // nodes join and report on their own, so the list keeps itself current
  const servers = useServers()
  const host = monitoring.data?.host

  return (
    <Page
      icon={Server}
      title="Remote Servers"
      description="Mac and Windows hosts that run your sandboxes."
    >
      {monitoring.isPending ? (
        <Skeleton className="h-36 rounded-xl" />
      ) : monitoring.error || !host ? (
        <EmptyState
          icon={Server}
          title={
            kubernetes.data?.enabled
              ? "Can't reach the Kubernetes cluster"
              : "Can't reach the Docker host"
          }
          description={monitoring.error?.message}
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-3">
                <div className="flex size-9 items-center justify-center rounded-lg border border-border bg-muted">
                  <Server className="size-4" />
                </div>
                <div>
                  <div className="font-medium">{host.name}</div>
                  <div className="text-xs text-muted-foreground">
                    Local · {host.os}
                  </div>
                </div>
              </div>
              <StatusBadge status="online" />
            </div>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-xs text-muted-foreground">CPU</dt>
                <dd>
                  {host.cpus} cores · {host.architecture}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Memory</dt>
                <dd>{formatBytes(host.memory_total)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  {kubernetes.data?.enabled ? "Kubernetes" : "Docker"}
                </dt>
                <dd>{host.docker_version}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  {kubernetes.data?.enabled ? "Sandbox pods" : "Containers"}
                </dt>
                <dd>{host.containers_running} running</dd>
              </div>
            </dl>
            {kubernetes.data?.enabled && (
              <KubernetesPanel status={kubernetes.data} />
            )}
          </div>
        </div>
      )}
      {servers.data?.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {servers.data.map((s) => (
            <RemoteServerCard
              key={s.id}
              id={s.id}
              name={s.name}
              platform={s.platform}
              node={s.node}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Server}
          title="No remote servers yet"
          description="Add a Mac for macOS sandboxes or a Windows machine for Windows sandboxes. Linux sandboxes run on this installation's Docker or Kubernetes runtime."
        />
      )}
      <PoolPanel />
      <AddServerCard />
    </Page>
  )
}

const POOL_KINDS: Record<PoolEntry["kind"], string> = {
  desktop: "Desktop",
  browser: "Browser",
  code: "Code",
}

function PoolPanel() {
  const pool = usePool()
  if (!pool.data?.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>Warm pool</CardTitle>
        <CardDescription>
          Linux sandboxes kept booted ahead of time, so a new sandbox starts in
          about a second. Each idle one uses a container&apos;s memory.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-normal">Host</th>
                <th className="py-2 pr-4 font-normal">Kind</th>
                <th className="py-2 pr-4 font-normal">Size</th>
                <th className="py-2 pr-4 font-normal">Idle</th>
                <th className="py-2 pr-4 font-normal">Booting</th>
                <th className="py-2 pr-4 font-normal">Claimed</th>
              </tr>
            </thead>
            <tbody>
              {pool.data.map((e) => (
                <PoolRow
                  key={`${e.kind}:${e.server_id ?? "local"}`}
                  entry={e}
                />
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}

function PoolRow({ entry }: { entry: PoolEntry }) {
  const setPool = useSetPool()
  const [size, setSize] = useState(String(entry.size))
  const value = Number(size)
  const valid = Number.isInteger(value) && value >= 0 && value <= 10
  const save = () => {
    if (valid && value !== entry.size)
      setPool.mutate({
        kind: entry.kind,
        server_id: entry.server_id,
        size: value,
      })
  }
  return (
    <tr className="border-t border-border align-top">
      <td className="py-2 pr-4">{entry.server_name}</td>
      <td className="py-2 pr-4">{POOL_KINDS[entry.kind]}</td>
      <td className="py-2 pr-4">
        <Input
          type="number"
          min={0}
          max={10}
          className="h-8 w-20"
          value={size}
          aria-invalid={!valid}
          onChange={(ev) => setSize(ev.target.value)}
          onBlur={save}
          onKeyDown={(ev) => ev.key === "Enter" && save()}
        />
        {setPool.error && (
          <div className="mt-1 text-xs text-destructive">
            {setPool.error.message}
          </div>
        )}
      </td>
      <td className="py-2 pr-4">{entry.idle}</td>
      <td className="py-2 pr-4">{entry.booting}</td>
      <td className="py-2 pr-4">
        {entry.claimed}
        {entry.error && (
          <div className="mt-1 max-w-xs text-xs text-destructive">
            Boot failed: {entry.error}
          </div>
        )}
      </td>
    </tr>
  )
}

function RemoteServerCard({
  id,
  name,
  platform,
  node,
}: {
  id: string
  name: string
  platform: ServerPlatform
  node: NodeInfo | null
}) {
  const status = useServerStatus(id)
  const remove = useRemoveServer()
  const s = status.data

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted">
            <Server className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="font-medium">{name}</div>
            <div className="truncate text-xs text-muted-foreground">
              {PLATFORM_LABELS[platform]}
            </div>
            <div className="text-xs text-muted-foreground">
              Runs {PLATFORM_LABELS[platform]} sandboxes
            </div>
          </div>
        </div>
        <StatusBadge
          status={
            status.isPending ? "pending" : s?.online ? "online" : "offline"
          }
        />
      </div>
      {s?.online ? (
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs text-muted-foreground">CPU</dt>
            <dd>{s.cpus} cores</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Memory</dt>
            <dd>{formatBytes(s.memory_total ?? 0)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">OS</dt>
            <dd>{s.os}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Isolation</dt>
            <dd>
              {platform === "windows"
                ? s.microvm
                  ? "Hyper-V VM"
                  : "Hyper-V missing"
                : s.microvm
                  ? "microVM"
                  : "runtime missing"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Sandboxes</dt>
            <dd>{s.sandboxes} running</dd>
          </div>
        </dl>
      ) : (
        s?.error && (
          <p className="line-clamp-3 text-xs text-destructive">{s.error}</p>
        )
      )}
      {node && <NodePanel node={node} />}
      {s?.online && <BaseVmPanel id={id} platform={platform} />}
      {remove.error && (
        <p className="text-xs text-destructive">{remove.error.message}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={remove.isPending}
          onClick={() => remove.mutate(id)}
        >
          <Trash2 />
          Remove
        </Button>
      </div>
    </div>
  )
}

function Meter({
  label,
  free,
  total,
}: {
  label: string
  free: number
  total: number
}) {
  const used = total > 0 ? Math.min(100, ((total - free) / total) * 100) : 0
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span>
          {formatBytes(free)} of {formatBytes(total)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full ${used > 90 ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${used}%` }}
        />
      </div>
    </div>
  )
}

function NodePanel({ node }: { node: NodeInfo }) {
  const failing = node.checks.filter((c) => !c.ok)
  const runtimes = node.drivers.filter((d) => d.available).map((d) => d.name)
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">zoo-node</span>
        <span className="text-xs text-muted-foreground">
          {node.connected ? "connected" : "offline"}
          {node.version && ` · ${node.version}`}
          {node.seen_at && ` · seen ${relativeTime(node.seen_at)}`}
        </span>
      </div>
      {node.memory_total != null && node.memory_available != null && (
        <Meter
          label="Free memory"
          free={node.memory_available}
          total={node.memory_total}
        />
      )}
      {node.disk_total != null && node.disk_free != null && (
        <Meter
          label="Free disk"
          free={node.disk_free}
          total={node.disk_total}
        />
      )}
      <div className="text-xs text-muted-foreground">
        {runtimes.length
          ? `Runtimes: ${runtimes.join(", ")}`
          : "No runtime found"}
        {` · ${node.sandboxes} running`}
        {node.load != null && ` · load ${Math.round(node.load * 100)}%`}
      </div>
      {failing.map((c) => (
        <p key={c.name} className="text-xs text-destructive">
          {c.name}: {c.detail || "failing"}
        </p>
      ))}
      {!node.connected && (
        <p className="text-xs text-muted-foreground">
          The node dials out to the control plane; check it&apos;s running on
          the host (
          {node.os === "darwin"
            ? "~/.zoo-node/node.log"
            : "the zoo-node service"}
          ).
        </p>
      )}
    </div>
  )
}

function KubernetesPanel({ status }: { status: Kubernetes }) {
  const check = useKubernetesCheck()
  const failing = status.checks.filter((c) => !c.ok)
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">Kubernetes</span>
        <span className="text-xs text-muted-foreground">
          {status.namespace} · {status.runtime_class}
        </span>
      </div>
      {status.checks.length === 0 ? (
        <p className="text-xs text-muted-foreground">Checking the cluster…</p>
      ) : failing.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Ready to run sandboxes: {status.checks.map((c) => c.name).join(", ")}
        </p>
      ) : (
        failing.map((c) => (
          <p key={c.name} className="text-xs text-destructive">
            {c.name}: {c.detail || "failing"}
          </p>
        ))
      )}
      {check.data &&
        check.data.checks
          .filter((c) => c.name.startsWith("node ") && c.ok)
          .map((c) => (
            <p key={c.name} className="text-xs text-muted-foreground">
              {c.name}: {c.detail}
            </p>
          ))}
      {check.error && (
        <p className="text-xs text-destructive">{check.error.message}</p>
      )}
      <div>
        <Button
          size="sm"
          variant="outline"
          disabled={check.isPending}
          onClick={() => check.mutate()}
        >
          {check.isPending ? "Starting a pod on every node…" : "Check nodes"}
        </Button>
      </div>
    </div>
  )
}

const BASE_LABELS = {
  missing: "Not installed",
  installing: "Installing",
  failed: "Install failed",
  stopped: "Ready",
  running: "Running",
}

function BaseVmPanel({
  id,
  platform,
}: {
  id: string
  platform: "macos" | "windows"
}) {
  const base = useBaseStatus(id)
  const action = useBaseAction(id)
  const setup = useBaseSetup()
  const [iso, setIso] = useState("")
  const [edition, setEdition] = useState("")
  const b = base.data
  const error = base.error ?? action.error ?? setup.error
  const windows = platform === "windows"

  function run(next: "install" | "start" | "stop") {
    action.mutate({
      id,
      action: next,
      ...(next === "install" && windows && { iso, edition }),
    })
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">Base VM</span>
        <span className="text-xs text-muted-foreground">
          {b ? BASE_LABELS[b.state] : "…"}
          {b &&
            (b.state === "stopped" || b.state === "running") &&
            (b.ready ? " · ready" : " · setup needed")}
        </span>
      </div>
      {b?.state === "stopped" && !b.ready && (
        <p className="text-xs text-muted-foreground">
          {windows
            ? "Start the base VM and wait for its setup to finish before creating Windows sandboxes."
            : "Start the base VM and finish its setup before creating macOS sandboxes."}
        </p>
      )}
      {b?.state === "installing" && (
        <div className="flex flex-col gap-1">
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${b.progress ?? 0}%` }}
            />
          </div>
          <span className="text-xs text-muted-foreground">
            {b.message}
            {b.progress != null && ` · ${b.progress.toFixed(1)}%`}
          </span>
        </div>
      )}
      {b?.state === "failed" && b.message && (
        <p className="line-clamp-3 text-xs text-destructive">{b.message}</p>
      )}
      {b?.state === "running" && windows && !b.ready && b.message && (
        <p className="text-xs text-muted-foreground">{b.message}</p>
      )}
      {b?.state === "running" && windows && b.ready && (
        <p className="text-xs text-muted-foreground">
          Setup is done. Open the screen to install apps or change settings,
          then Stop. Stopping saves the base as the template new sandboxes start
          from.
        </p>
      )}
      {b?.state === "running" && !windows && (
        <ol className="flex list-decimal flex-col gap-1 pl-4 text-xs text-muted-foreground">
          <li>
            Open the screen and finish macOS setup with a user named admin.
          </li>
          <li>
            Click Run setup and type the admin password in the Terminal it
            opens.
          </li>
          <li>Grant Accessibility to /bin/zsh, install any apps, then Stop.</li>
        </ol>
      )}
      {windows && (b?.state === "missing" || b?.state === "failed") && (
        <div className="flex flex-col gap-2">
          <Input
            placeholder="ISO path on the server, or https:// URL"
            value={iso}
            onChange={(event) => setIso(event.target.value)}
          />
          <Input
            placeholder="Edition (optional, e.g. Pro)"
            value={edition}
            onChange={(event) => setEdition(event.target.value)}
          />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {(b?.state === "missing" || b?.state === "failed") && (
          <Button
            size="sm"
            disabled={action.isPending || (windows && !iso.trim())}
            onClick={() => run("install")}
          >
            <Download />
            {b.state === "failed"
              ? "Retry install"
              : windows
                ? "Install Windows"
                : "Install macOS"}
          </Button>
        )}
        {b?.state === "stopped" && (
          <Button
            size="sm"
            variant="outline"
            disabled={action.isPending}
            onClick={() => run("start")}
          >
            <Play />
            {action.isPending ? "Booting…" : "Start"}
          </Button>
        )}
        {b?.state === "running" && (
          <>
            <Button
              size="sm"
              render={
                <Link
                  to="/base/$serverId"
                  params={{ serverId: id }}
                  target="_blank"
                />
              }
            >
              <ExternalLink />
              Open screen
            </Button>
            {!windows && (
              <Button
                size="sm"
                variant="outline"
                disabled={setup.isPending}
                onClick={() => setup.mutate(id)}
              >
                <Terminal />
                {setup.isPending ? "Typing…" : "Run setup"}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={action.isPending}
              onClick={() => run("stop")}
            >
              <Square />
              {action.isPending ? "Stopping…" : "Stop"}
            </Button>
          </>
        )}
      </div>
      {error && <p className="text-xs text-destructive">{error.message}</p>}
    </div>
  )
}

const PLATFORM_LABELS: Record<ServerPlatform, string> = {
  macos: "macOS",
  windows: "Windows",
}

const HOSTS: Array<{ value: ServerPlatform; label: string }> = [
  { value: "macos", label: "macOS" },
  { value: "windows", label: "Windows" },
]

function CopyLine({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-muted p-3">
      <code className="min-w-0 flex-1 font-mono text-xs break-all">{text}</code>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => {
          void navigator.clipboard.writeText(text)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
      >
        {copied ? <Check /> : <Copy />}
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  )
}

function Step({
  n,
  title,
  children,
}: {
  n: number
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="flex gap-3">
      <div className="flex size-6 shrink-0 items-center justify-center rounded-full border border-border text-xs font-medium">
        {n}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="text-sm font-medium">{title}</div>
        {children}
      </div>
    </div>
  )
}

function AddServerCard() {
  const [host, setHost] = useState<ServerPlatform>("macos")
  const [name, setName] = useState("")
  const token = useCreateNodeToken()

  function pick(next: ServerPlatform) {
    setHost(next)
    token.reset()
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a server</CardTitle>
        <CardDescription>
          Join a Mac or Windows machine with zoo-node. The node connects to this
          control plane, so the host needs no inbound port.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault()
            if (name.trim()) token.mutate({ name: name.trim(), platform: host })
          }}
        >
          <Step n={1} title="Pick the machine's OS">
            <Label htmlFor="server-os">Operating system</Label>
            <Select
              items={HOSTS}
              value={host}
              disabled={token.isPending}
              onValueChange={(next) => {
                if (next === "macos" || next === "windows") pick(next)
              }}
            >
              <SelectTrigger id="server-os" className="sm:w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {HOSTS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Step>
          <Step n={2} title="Name it">
            <Label htmlFor="server-name">Server name</Label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                id="server-name"
                placeholder={host === "macos" ? "mac-mini-1" : "windows-host-1"}
                value={name}
                required
                maxLength={100}
                disabled={token.isPending}
                onChange={(event) => {
                  setName(event.target.value)
                  token.reset()
                }}
                className="sm:w-64"
              />
              <Button type="submit" disabled={!name.trim() || token.isPending}>
                <Plus data-icon="inline-start" />
                {token.isPending ? "Creating…" : "Get the command"}
              </Button>
            </div>
            {token.error && (
              <p role="alert" className="text-xs text-destructive">
                {token.error.message}
              </p>
            )}
          </Step>
        </form>
        {token.data && (
          <Step
            n={3}
            title={
              host === "windows"
                ? "Run this in an elevated PowerShell"
                : "Run this on the Mac"
            }
          >
            <CopyLine text={token.data.install_command} />
            <p className="text-xs text-muted-foreground">
              Already have zoo-node and {host === "macos" ? "zoovm" : "Hyper-V"}
              ? Join with:
            </p>
            <CopyLine text={token.data.join_command} />
            <p className="text-xs text-muted-foreground">
              The token works once and expires{" "}
              {relativeTime(token.data.expires_at)}. The server shows up above
              when it joins.
            </p>
          </Step>
        )}
      </CardContent>
    </Card>
  )
}
