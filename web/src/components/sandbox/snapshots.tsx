import { History, RotateCcw, Trash2 } from "lucide-react"
import { useState } from "react"
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
  useCreateSnapshot,
  useRemoveSnapshot,
  useRestoreSnapshot,
  useSnapshots,
} from "@/lib/api_client"
import type { Sandbox } from "@/lib/api_client"
import { formatBytes, relativeTime } from "@/lib/utils"

export const SNAPSHOT_STATES = {
  creating: "Taking…",
  ready: "Ready",
  failed: "Failed",
}

export function SnapshotsTab({ sandbox }: { sandbox: Sandbox }) {
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
