import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useCallback, useEffect } from "react"

import { VncScreen } from "@/components/vnc-screen"
import {
  baseSocketUrl,
  useBaseStatus,
  useEventStream,
  useMe,
} from "@/lib/api_client"

export const Route = createFileRoute("/base/$serverId")({
  component: BaseView,
})

function BaseView() {
  const { serverId } = Route.useParams()
  const navigate = useNavigate()
  const { data: user, isPending } = useMe()
  useEventStream(Boolean(user))
  const base = useBaseStatus(serverId)
  const socketUrl = useCallback(() => baseSocketUrl(serverId), [serverId])

  useEffect(() => {
    if (!isPending && !user) {
      navigate({ to: "/login", replace: true })
    }
  }, [isPending, user, navigate])

  const message = base.error
    ? base.error.message
    : base.data && base.data.state !== "running"
      ? `Base VM is ${base.data.state}. Start it from Remote Servers.`
      : null

  return (
    <VncScreen
      socketUrl={socketUrl}
      active={!!user && base.data?.state === "running"}
      message={message}
    />
  )
}
