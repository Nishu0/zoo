import { cn } from "@/lib/utils"

const GREEN = "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
const AMBER = "bg-amber-500/10 text-amber-400 border-amber-500/20"
const GRAY = "bg-zinc-500/10 text-zinc-400 border-zinc-500/20"
const RED = "bg-red-500/10 text-red-400 border-red-500/20"

const STATUS_STYLES: Record<string, string> = {
  online: GREEN,
  running: GREEN,
  open: GREEN,
  degraded: AMBER,
  provisioning: AMBER,
  pending: AMBER,
  deleting: AMBER,
  stopping: AMBER,
  moving: AMBER,
  snapshotting: AMBER,
  restoring: AMBER,
  unreachable: AMBER,
  offline: GRAY,
  stopped: GRAY,
  paused: GRAY,
  closed: GRAY,
  error: RED,
  failed: RED,
}

const STATUS_DOT: Record<string, string> = {
  online: "bg-emerald-500",
  running: "bg-emerald-500",
  open: "bg-emerald-500",
  degraded: "bg-amber-500",
  provisioning: "bg-amber-500 animate-pulse",
  pending: "bg-amber-500 animate-pulse",
  deleting: "bg-amber-500 animate-pulse",
  stopping: "bg-amber-500 animate-pulse",
  moving: "bg-amber-500 animate-pulse",
  snapshotting: "bg-amber-500 animate-pulse",
  restoring: "bg-amber-500 animate-pulse",
  unreachable: "bg-amber-500",
  offline: "bg-zinc-500",
  stopped: "bg-zinc-500",
  paused: "bg-sky-400",
  closed: "bg-zinc-500",
  error: "bg-red-500",
  failed: "bg-red-500",
}

export function StatusDot({
  status,
  className,
}: {
  status: string
  className?: string
}) {
  return (
    <span
      className={cn(
        "size-2 rounded-full",
        STATUS_DOT[status] ?? "bg-zinc-500",
        className
      )}
    />
  )
}

// statuses shown as a phrase rather than one capitalized word
const LABELS: Record<string, string> = {
  paused: "Paused · resumes instantly",
}

export function StatusBadge({
  status,
  className,
}: {
  status: string
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
        !LABELS[status] && "capitalize",
        STATUS_STYLES[status] ?? GRAY,
        className
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          STATUS_DOT[status] ?? "bg-zinc-500"
        )}
      />
      {LABELS[status] ?? status}
    </span>
  )
}
