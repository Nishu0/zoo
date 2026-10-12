import { forwardRef, useEffect, useImperativeHandle, useRef } from "react"
import type { CSSProperties } from "react"

// What the embed page (GET /embed/{token} on the Zoo API) reports to the page that frames it.
export type ZooViewerEvent =
  | { type: "connect" }
  | { type: "disconnect" }
  | { type: "takeover" }
  | { type: "release" }
  | { type: "agent"; state: "idle" | "running" | "paused" }

export type ZooViewerHandle = {
  // Pause the agent and hand the screen to the person at the viewer (control links only).
  takeover: () => void
  // Give the screen back to the agent.
  release: () => void
}

export type ZooViewerProps = {
  // A viewer link's url, from POST /sandboxes/{id}/viewer-links. Create it on your server: the API key that
  // creates links must never reach the browser.
  url: string
  title?: string
  className?: string
  style?: CSSProperties
  onEvent?: (event: ZooViewerEvent) => void
  onTakeover?: () => void
  onRelease?: () => void
}

// A sandbox's live screen. A view link only watches; a control link can type and click, and acting while an agent
// runs takes the screen over from it until it is handed back. The Zoo API's ZOO_EMBED_ORIGINS must list this
// page's origin, or the browser refuses to show the frame.
export const ZooViewer = forwardRef<ZooViewerHandle, ZooViewerProps>(function ZooViewer(
  { url, title = "Sandbox screen", className, style, onEvent, onTakeover, onRelease },
  ref,
) {
  const frame = useRef<HTMLIFrameElement>(null)
  const origin = new URL(url).origin

  useImperativeHandle(
    ref,
    () => ({
      takeover: () => frame.current?.contentWindow?.postMessage({ source: "zoo", type: "takeover" }, origin),
      release: () => frame.current?.contentWindow?.postMessage({ source: "zoo", type: "release" }, origin),
    }),
    [origin],
  )

  useEffect(() => {
    function listen(message: MessageEvent) {
      if (message.origin !== origin || message.source !== frame.current?.contentWindow) return
      const event = message.data as (ZooViewerEvent & { source?: string }) | null
      if (event?.source !== "zoo") return
      onEvent?.(event)
      if (event.type === "takeover") onTakeover?.()
      if (event.type === "release") onRelease?.()
    }
    window.addEventListener("message", listen)
    return () => window.removeEventListener("message", listen)
  }, [origin, onEvent, onTakeover, onRelease])

  return (
    <iframe
      ref={frame}
      src={url}
      title={title}
      className={className}
      style={{ border: 0, width: "100%", height: "100%", background: "#000", ...style }}
      allow="clipboard-read; clipboard-write"
    />
  )
})
