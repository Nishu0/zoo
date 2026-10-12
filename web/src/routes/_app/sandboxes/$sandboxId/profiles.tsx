import { createFileRoute } from "@tanstack/react-router"

import { useCurrentSandbox } from "@/components/sandbox/context"
import { ProfilesTab } from "@/components/sandbox/profiles"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId/profiles")({
  component: ProfilesRoute,
})

function ProfilesRoute() {
  const sandbox = useCurrentSandbox()
  return (
    <ProfilesTab
      sandboxId={sandbox.id}
      running={sandbox.status === "running"}
      platform={
        sandbox.kind === "macos" || sandbox.kind === "windows"
          ? sandbox.kind
          : "linux"
      }
    />
  )
}
