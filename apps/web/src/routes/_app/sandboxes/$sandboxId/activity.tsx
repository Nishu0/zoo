import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { ActivityTab } from "@/components/sandbox/activity"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/activity")({
  component: ActivityRoute,
})

function ActivityRoute() {
  const sandbox = useCurrentSandbox()
  return <ActivityTab sandboxId={sandbox.id} />
}
