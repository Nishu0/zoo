import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { TerminalView } from "@/components/terminal-view"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/terminal")({
  component: TerminalRoute,
})

function TerminalRoute() {
  const sandbox = useCurrentSandbox()
  return (
    <TerminalView
      sandboxId={sandbox.id}
      running={sandbox.status === "running"}
    />
  )
}
