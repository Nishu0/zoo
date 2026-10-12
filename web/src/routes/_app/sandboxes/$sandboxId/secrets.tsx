import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { SecretsTab, VaultSecretsCard } from "@/components/sandbox/secrets"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/secrets")({
  component: SecretsRoute,
})

function SecretsRoute() {
  const sandbox = useCurrentSandbox()
  return (
    <div className="flex flex-col gap-4">
      <VaultSecretsCard sandboxId={sandbox.id} />
      <SecretsTab sandboxId={sandbox.id} />
    </div>
  )
}
