import { useEffect, useRef, useState } from "react"
import { RotateCcw } from "lucide-react"

import "@xterm/xterm/css/xterm.css"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { terminalSocketUrl } from "@/lib/api_client"

type Status = "connecting" | "open" | "closed"

// A shell in the sandbox, streamed through its guest. Each session is a fresh login shell.
export function TerminalView({
  sandboxId,
  running,
}: {
  sandboxId: string
  running: boolean
}) {
  const screenRef = useRef<HTMLDivElement>(null)
  const [session, setSession] = useState(0)
  const [status, setStatus] = useState<Status>("connecting")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const screen = screenRef.current
    if (!running || !screen) return

    let disposed = false
    const isDisposed = () => disposed
    let socket: WebSocket | null = null
    let cleanup = () => {}

    async function start(el: HTMLDivElement) {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ])
      if (isDisposed()) return
      const term = new Terminal({
        cursorBlink: true,
        fontSize: 13,
        fontFamily:
          "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
        theme: { background: "#0a0a0a" },
      })
      const fit = new FitAddon()
      term.loadAddon(fit)
      term.open(el)
      fit.fit()
      const observer = new ResizeObserver(() => fit.fit())
      observer.observe(el)
      cleanup = () => {
        observer.disconnect()
        term.dispose()
      }

      let url: string
      try {
        url = await terminalSocketUrl(sandboxId, term.cols, term.rows)
      } catch (e) {
        if (isDisposed()) return
        setError(e instanceof Error ? e.message : "Couldn't open a terminal")
        setStatus("closed")
        return
      }
      if (isDisposed()) return

      const ws = new WebSocket(url)
      socket = ws
      ws.binaryType = "arraybuffer"
      const encoder = new TextEncoder()
      ws.onopen = () => {
        setStatus("open")
        term.focus()
      }
      ws.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
        if (typeof event.data !== "string") {
          term.write(new Uint8Array(event.data))
          return
        }
        const message = JSON.parse(event.data) as {
          type?: string
          code?: number | null
        }
        if (message.type === "exit") {
          term.write(
            `\r\n[session ended${message.code == null ? "" : ` with code ${message.code}`}]\r\n`
          )
        }
      }
      ws.onclose = (event) => {
        if (isDisposed()) return
        setStatus("closed")
        if (event.reason) setError(event.reason)
      }
      term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(encoder.encode(data))
      })
      term.onResize(({ cols, rows }) => {
        if (ws.readyState === WebSocket.OPEN)
          ws.send(JSON.stringify({ type: "resize", cols, rows }))
      })
    }

    void start(screen)
    return () => {
      disposed = true
      socket?.close()
      cleanup()
    }
  }, [sandboxId, running, session])

  function reconnect() {
    setError(null)
    setStatus("connecting")
    setSession((n) => n + 1)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Terminal</CardTitle>
        <CardDescription>
          {!running
            ? "Start the sandbox to open a terminal."
            : (error ??
              (status === "open"
                ? "A login shell as the sandbox user."
                : status === "connecting"
                  ? "Connecting…"
                  : "Disconnected."))}
        </CardDescription>
        <CardAction>
          <Button
            variant="outline"
            size="sm"
            disabled={!running || status === "connecting"}
            onClick={reconnect}
          >
            <RotateCcw />
            New session
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {running && (
          <div className="h-[480px] overflow-hidden rounded-md bg-[#0a0a0a] p-2">
            <div key={session} ref={screenRef} className="h-full w-full" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
