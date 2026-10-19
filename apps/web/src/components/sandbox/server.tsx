import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useMoveSandbox, useServers } from "@/lib/api_client"
import type { Sandbox } from "@/lib/api_client"

export function ServerTab({ sandbox }: { sandbox: Sandbox }) {
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
