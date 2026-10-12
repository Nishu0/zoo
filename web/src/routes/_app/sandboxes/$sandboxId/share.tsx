import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { ShareTab } from "@/components/sandbox/share"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/share")({
  component: ShareRoute,
})

function ShareRoute() {
  const sandbox = useCurrentSandbox()
  return <ShareTab sandbox={sandbox} />
}
