import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { AppsTab } from "@/components/sandbox/apps"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/apps")({
  component: AppsRoute,
})

function AppsRoute() {
  const sandbox = useCurrentSandbox()
  return (
    <AppsTab sandboxId={sandbox.id} running={sandbox.status === "running"} />
  )
}
