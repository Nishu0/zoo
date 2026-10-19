import { Cpu, MemoryStick, RotateCcw, Workflow } from "lucide-react"
import { StatCard } from "@/components/stat-card"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  displayStatus,
  useMonitoring,
  useSandboxAction,
} from "@/lib/api_client"
import type { Sandbox } from "@/lib/api_client"
import { formatBytes, timeAgo } from "@/lib/utils"

export function NewImageNotice({ sandbox }: { sandbox: Sandbox }) {
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

export function OverviewTab({ sandbox }: { sandbox: Sandbox }) {
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
