import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { AgentTab } from "@/components/sandbox/agent"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/agent")({
  component: AgentRoute,
})

function AgentRoute() {
  const sandbox = useCurrentSandbox()
  return <AgentTab sandbox={sandbox} />
}
