import { useEffect, useRef, useState } from "react"

import type RFB from "@novnc/novnc"

// Full-screen view of the sandbox's VNC session in the gateway, through the API websocket; reconnects while
// `active` is true.
export function VncScreen({
  socketUrl,
  active,
  message,
}: {
  socketUrl: () => Promise<string>
  active: boolean
  message: string | null
}) {
  const screenRef = useRef<HTMLDivElement>(null)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const screen = screenRef.current
    if (!active || !screen) return

    let rfb: RFB | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let closed = false

    async function connect() {
      const { default: RFBClient } = await import("@novnc/novnc")
      let url: string
      try {
        url = await socketUrl()
      } catch {
        if (!closed) retry = setTimeout(connect, 1500)
        return
      }
      if (closed || !screen) return
      rfb = new RFBClient(screen, url)
      rfb.scaleViewport = true
      rfb.background = "#000"
      rfb.addEventListener("connect", () => {
        setConnected(true)
        rfb?.focus()
      })
      rfb.addEventListener("disconnect", () => {
        setConnected(false)
        if (!closed) retry = setTimeout(connect, 1500)
      })
    }

    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      rfb?.disconnect()
    }
  }, [active, socketUrl])

  const shown = message ?? (connected ? null : "Connecting…")

  return (
    <div className="relative h-svh w-screen overflow-hidden bg-black">
      <div ref={screenRef} className="h-full w-full" />
      {shown && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-zinc-400">
          {shown}
        </div>
      )}
    </div>
  )
}
