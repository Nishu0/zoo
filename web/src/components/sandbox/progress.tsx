import { Check, Loader2, RotateCcw, TriangleAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useSandboxAction } from "@/lib/api_client"
import type { Sandbox } from "@/lib/api_client"
import { cn } from "@/lib/utils"

const STEPS = [
  { key: "queued", label: "Queued" },
  { key: "creating", label: "Creating the sandbox" },
  { key: "booting", label: "Starting the desktop and guest agent" },
  { key: "running", label: "Running" },
] as const

function current(sandbox: Sandbox): number {
  if (sandbox.status === "running") return 3
  if (sandbox.status === "provisioning")
    return sandbox.job?.state === "running" ? 1 : 2
  return 0
}

/** Where a starting sandbox is, and what went wrong with one that failed, with Retry. */
export function SandboxProgress({ sandbox }: { sandbox: Sandbox }) {
  const start = useSandboxAction("start")
  if (sandbox.status === "failed" && !sandbox.job) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <span className="flex items-center gap-2">
          <TriangleAlert className="size-4 shrink-0" />
          {sandbox.error_message ?? "The sandbox failed to start."}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={start.isPending}
          onClick={() => start.mutate(sandbox.id)}
        >
          <RotateCcw />
          Retry
        </Button>
      </div>
    )
  }
  if (sandbox.status !== "pending" && sandbox.status !== "provisioning") {
    return null
  }
  const at = current(sandbox)
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
      <ol className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {STEPS.map((step, i) => (
          <li
            key={step.key}
            className={cn(
              "flex items-center gap-1.5",
              i > at && "text-muted-foreground"
            )}
          >
            {i < at ? (
              <Check className="size-3.5 text-emerald-500" />
            ) : i === at ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <span className="size-3.5 rounded-full border border-border" />
            )}
            {step.label}
          </li>
        ))}
      </ol>
      {sandbox.job && sandbox.job.attempts > 1 && (
        <p className="text-xs text-amber-500">
          Attempt {sandbox.job.attempts} of {sandbox.job.max_attempts}
          {sandbox.job.last_error ? `: ${sandbox.job.last_error}` : ""}
        </p>
      )}
      {sandbox.job?.waiting && sandbox.job.last_error && (
        <p className="text-xs text-muted-foreground">
          {sandbox.job.last_error}
        </p>
      )}
    </div>
  )
}
