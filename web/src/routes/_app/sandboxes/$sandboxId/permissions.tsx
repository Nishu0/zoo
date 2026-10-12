import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { PermissionsTab } from "@/components/sandbox/permissions"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/permissions")({
  component: PermissionsRoute,
})

function PermissionsRoute() {
  const sandbox = useCurrentSandbox()
  return <PermissionsTab sandboxId={sandbox.id} />
}
