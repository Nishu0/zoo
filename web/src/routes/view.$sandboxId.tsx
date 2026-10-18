import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useCallback, useEffect } from "react"

import { VncScreen } from "@/components/vnc-screen"
import { sandboxSocketUrl, useMe, useSandbox } from "@/lib/api_client"

export const Route = createFileRoute("/view/$sandboxId")({
  component: SandboxView,
})

function SandboxView() {
  const { sandboxId } = Route.useParams()
  const navigate = useNavigate()
  const { data: user, isPending } = useMe()
  const sandbox = useSandbox(sandboxId)
  const socketUrl = useCallback(() => sandboxSocketUrl(sandboxId), [sandboxId])

  useEffect(() => {
    if (!isPending && !user) {
      navigate({ to: "/login", replace: true })
    }
  }, [isPending, user, navigate])

  const message = sandbox.error
    ? sandbox.error.message
    : sandbox.data?.status === "failed"
      ? (sandbox.data.error_message ?? "Sandbox failed to start.")
      : sandbox.data && sandbox.data.status !== "running"
        ? sandbox.data.job?.waiting && sandbox.data.job.last_error
          ? sandbox.data.job.last_error.replace(/^Waiting:\s*/, "")
          : `Sandbox is ${sandbox.data.status}…`
        : null

  return (
    <VncScreen
      socketUrl={socketUrl}
      active={!!user && sandbox.data?.status === "running"}
      message={message}
    />
  )
}
