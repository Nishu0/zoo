import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { SnapshotsTab } from "@/components/sandbox/snapshots"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/snapshots")({
  component: SnapshotsRoute,
})

function SnapshotsRoute() {
  const sandbox = useCurrentSandbox()
  return <SnapshotsTab sandbox={sandbox} />
}
