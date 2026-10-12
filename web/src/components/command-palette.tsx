import { useNavigate, useParams } from "@tanstack/react-router"
import { Box, Command, CornerDownLeft } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"

import { openConnect } from "@/components/connect"
import { openCreateSandbox } from "@/components/create-sandbox-dialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useSandboxes } from "@/lib/api_client"
import { cn } from "@/lib/utils"

type Action = { id: string; label: string; hint?: string; run: () => void }

const PAGES = [
  { to: "/sandboxes", label: "Sandboxes", keys: "g s" },
  { to: "/monitoring", label: "Monitoring", keys: "g m" },
  { to: "/vault", label: "Vault", keys: "g v" },
  { to: "/workspace", label: "Workspace", keys: "g w" },
  { to: "/audit", label: "Audit log", keys: "g a" },
  { to: "/servers", label: "Remote servers", keys: "g r" },
  { to: "/settings", label: "Profile and API keys", keys: "g p" },
] as const

export const SHORTCUTS = [
  { keys: "⌘K / Ctrl K", label: "Command palette" },
  { keys: "c", label: "Create a sandbox" },
  ...PAGES.map((p) => ({ keys: p.keys, label: `Go to ${p.label}` })),
  { keys: "?", label: "These shortcuts" },
]

/** Typing in a field shouldn't trigger shortcuts. */
function typing(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
      target.closest(".xterm, [data-vnc]") !== null)
  )
}

/** ⌘K command palette and the app's single-key shortcuts. */
export function CommandPalette() {
  const navigate = useNavigate()
  const params = useParams({ strict: false })
  const sandboxId = (params as { sandboxId?: string }).sandboxId
  const sandboxes = useSandboxes()
  const [open, setOpen] = useState(false)
  const [help, setHelp] = useState(false)
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState(0)
  const pending = useRef<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setOpen((o) => !o)
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return
      if (pending.current === "g") {
        pending.current = null
        const page = PAGES.find((p) => p.keys === `g ${e.key}`)
        if (page) {
          e.preventDefault()
          void navigate({ to: page.to })
        }
        return
      }
      if (e.key === "g") pending.current = "g"
      else if (e.key === "c") {
        e.preventDefault()
        openCreateSandbox()
      } else if (e.key === "?") setHelp(true)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [navigate])

  const actions = useMemo<Action[]>(() => {
    const list: Action[] = [
      {
        id: "create",
        label: "Create a sandbox",
        hint: "c",
        run: openCreateSandbox,
      },
    ]
    if (sandboxId) {
      list.push(
        { id: "connect", label: "Connect to this sandbox", run: openConnect },
        {
          id: "copy-id",
          label: "Copy this sandbox's id",
          run: () => void navigator.clipboard.writeText(sandboxId),
        }
      )
    }
    for (const p of PAGES)
      list.push({
        id: p.to,
        label: `Go to ${p.label}`,
        hint: p.keys,
        run: () => void navigate({ to: p.to }),
      })
    for (const s of sandboxes.data ?? [])
      list.push({
        id: `sandbox:${s.id}`,
        label: `Open ${s.name}`,
        hint: s.status,
        run: () =>
          void navigate({
            to: "/sandboxes/$sandboxId",
            params: { sandboxId: s.id },
          }),
      })
    list.push({
      id: "help",
      label: "Keyboard shortcuts",
      hint: "?",
      run: () => setHelp(true),
    })
    return list
  }, [sandboxId, sandboxes.data, navigate])

  const q = query.trim().toLowerCase()
  const shown = q
    ? actions.filter(
        (a) => a.label.toLowerCase().includes(q) || a.id.includes(q)
      )
    : actions
  const choose = (action: Action | undefined) => {
    if (!action) return
    setOpen(false)
    setQuery("")
    action.run()
  }

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          setQuery("")
          setSelected(0)
        }}
      >
        <DialogContent
          className="gap-2 p-2 sm:max-w-lg"
          showCloseButton={false}
        >
          <DialogTitle className="sr-only">Command palette</DialogTitle>
          <Input
            autoFocus
            value={query}
            placeholder="Type a command or a sandbox name…"
            onChange={(e) => {
              setQuery(e.target.value)
              setSelected(0)
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault()
                setSelected((i) => Math.min(i + 1, shown.length - 1))
              } else if (e.key === "ArrowUp") {
                e.preventDefault()
                setSelected((i) => Math.max(i - 1, 0))
              } else if (e.key === "Enter") {
                e.preventDefault()
                choose(shown[selected])
              }
            }}
          />
          <ul role="listbox" className="max-h-80 overflow-y-auto">
            {shown.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-muted-foreground">
                Nothing matches.
              </li>
            )}
            {shown.map((a, i) => (
              <li
                key={a.id}
                role="option"
                aria-selected={i === selected}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-sm",
                  i === selected && "bg-muted"
                )}
                onMouseEnter={() => setSelected(i)}
                onClick={() => choose(a)}
              >
                <span className="flex items-center gap-2 truncate">
                  {a.id.startsWith("sandbox:") ? (
                    <Box className="size-4 text-muted-foreground" />
                  ) : (
                    <Command className="size-4 text-muted-foreground" />
                  )}
                  {a.label}
                </span>
                {i === selected ? (
                  <CornerDownLeft className="size-3.5 text-muted-foreground" />
                ) : (
                  a.hint && (
                    <span className="font-mono text-xs text-muted-foreground">
                      {a.hint}
                    </span>
                  )
                )}
              </li>
            ))}
          </ul>
        </DialogContent>
      </Dialog>
      <Dialog open={help} onOpenChange={setHelp}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Keyboard shortcuts</DialogTitle>
            <DialogDescription>
              They work anywhere except while typing in a field.
            </DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            {SHORTCUTS.map((s) => (
              <div key={s.keys} className="contents">
                <dt className="font-mono text-xs text-muted-foreground">
                  {s.keys}
                </dt>
                <dd>{s.label}</dd>
              </div>
            ))}
          </dl>
        </DialogContent>
      </Dialog>
    </>
  )
}
