import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { NetworkTab } from "@/components/sandbox/network"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/network")({
  component: NetworkRoute,
})

function NetworkRoute() {
  const sandbox = useCurrentSandbox()
  return <NetworkTab sandboxId={sandbox.id} />
}
