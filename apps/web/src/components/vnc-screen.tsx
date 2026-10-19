import { useEffect, useRef, useState } from "react"

import type RFB from "@novnc/novnc"

import "./vnc-screen.css"

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

    let retry: ReturnType<typeof setTimeout> | undefined
    let closed = false
    let attempt = 0
    let disposeClient = () => {}

    function disconnect() {
      const dispose = disposeClient
      disposeClient = () => {}
      dispose()
    }

    async function connect() {
      const current = ++attempt
      clearTimeout(retry)
      disconnect()
      setConnected(false)
      try {
        const { default: RFBClient } = await import("@novnc/novnc")
        const url = await socketUrl()
        if (closed || current !== attempt) return

        // Give each client its own target so delayed teardown cannot remove a new client's canvas.
        const target = document.createElement("div")
        target.className = "h-full w-full"
        screen!.append(target)
        disposeClient = () => target.remove()
        const client: RFB = new RFBClient(target, url)
        client.scaleViewport = true
        // Scoped CSS supplies a larger native pointer. Disable noVNC's tiny
        // software dot so touch-capable browsers do not draw a second cursor.
        client.showDotCursor = false
        client.background = "#000"
        const onConnect = () => {
          if (closed || current !== attempt) return
          setConnected(true)
          client.focus()
        }
        const onDisconnect = () => {
          if (closed || current !== attempt) return
          setConnected(false)
          disconnect()
          retry = setTimeout(connect, 1500)
        }
        client.addEventListener("connect", onConnect)
        client.addEventListener("disconnect", onDisconnect)
        disposeClient = () => {
          client.removeEventListener("connect", onConnect)
          client.removeEventListener("disconnect", onDisconnect)
          client.disconnect()
          target.remove()
        }
      } catch {
        if (!closed && current === attempt) {
          disconnect()
          retry = setTimeout(connect, 1500)
        }
      }
    }

    function onVisible() {
      if (document.visibilityState === "visible") connect()
    }

    document.addEventListener("visibilitychange", onVisible)
    window.addEventListener("online", connect)
    window.addEventListener("pageshow", onVisible)
    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      document.removeEventListener("visibilitychange", onVisible)
      window.removeEventListener("online", connect)
      window.removeEventListener("pageshow", onVisible)
      disconnect()
    }
  }, [active, socketUrl])

  const shown = message ?? (connected ? null : "Connecting…")

  return (
    <div className="relative h-svh w-screen overflow-hidden bg-black">
      <div ref={screenRef} className="zoo-vnc-screen h-full w-full" />
      {shown && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-zinc-400">
          {shown}
        </div>
      )}
    </div>
  )
}
