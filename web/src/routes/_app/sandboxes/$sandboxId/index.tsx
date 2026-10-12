import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { OverviewTab } from "@/components/sandbox/overview"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/")({
  component: IndexRoute,
})

function IndexRoute() {
  const sandbox = useCurrentSandbox()
  return <OverviewTab sandbox={sandbox} />
}
