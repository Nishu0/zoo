import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { ServerTab } from "@/components/sandbox/server"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/server")({
  component: ServerRoute,
})

function ServerRoute() {
  const sandbox = useCurrentSandbox()
  return <ServerTab sandbox={sandbox} />
}
