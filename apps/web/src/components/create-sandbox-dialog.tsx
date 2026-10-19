import { Link } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useEffect, useState } from "react"

import { FieldError } from "@/components/auth-shell"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  createSandboxSchema,
  useCreateSandbox,
  useProfiles,
  useVaultSecrets,
  usePlatforms,
  useServers,
} from "@/lib/api_client"

const KINDS = [
  { value: "desktop", label: "Desktop · full XFCE desktop" },
  { value: "browser", label: "Browser · Firefox with browser tools" },
  { value: "code", label: "Code · shell, files and Claude Code, no display" },
  { value: "macos", label: "macOS · full macOS desktop on a Mac server" },
  {
    value: "windows",
    label: "Windows · full Windows desktop on a Windows server",
  },
]

// Kinds that run as VMs on a server of their own platform rather than in Docker.
const VM_KINDS = ["macos", "windows"]

// sizes are for Linux sandboxes; macOS and Windows VMs have fixed ones
const LIMITS = [
  { key: "cpus", label: "CPUs", placeholder: "2", step: 0.5, linuxOnly: true },
  {
    key: "memory_mb",
    label: "Memory (MB)",
    placeholder: "2048",
    step: 256,
    linuxOnly: true,
  },
  {
    key: "disk_gb",
    label: "Disk (GB)",
    placeholder: "Host's",
    step: 1,
    linuxOnly: true,
  },
  {
    key: "idle_timeout_minutes",
    label: "Stop when idle (min)",
    placeholder: "Workspace's",
    step: 1,
    linuxOnly: false,
  },
  {
    key: "max_lifetime_minutes",
    label: "Stop after (min)",
    placeholder: "Workspace's",
    step: 1,
    linuxOnly: false,
  },
] as const
type LimitKey = (typeof LIMITS)[number]["key"]
const NO_LIMITS = Object.fromEntries(LIMITS.map((l) => [l.key, ""])) as Record<
  LimitKey,
  string
>

function osOf(kind: string): "linux" | "macos" | "windows" {
  return kind === "macos" || kind === "windows" ? kind : "linux"
}

const LOCAL = "local"
const AUTO = "auto"
const NO_PROFILE = "none"

function Choice({
  id,
  label,
  items,
  value,
  onChange,
}: {
  id: string
  label: string
  items: Array<{ value: string; label: string; disabled?: boolean }>
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select
        items={items}
        value={value}
        onValueChange={(next) => next && onChange(next)}
      >
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((item) => (
            <SelectItem
              key={item.value}
              value={item.value}
              disabled={item.disabled}
            >
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

const OPEN_EVENT = "zoo:create-sandbox"

/** Opens the app-wide create dialog (the palette and the `c` shortcut use it). */
export function openCreateSandbox() {
  window.dispatchEvent(new Event(OPEN_EVENT))
}

/**
 * The create-a-sandbox dialog. With `global`, it has no button of its own and opens on openCreateSandbox(); the app
 * layout mounts one of those.
 */
export function CreateSandboxDialog({
  global = false,
}: { global?: boolean } = {}) {
  const create = useCreateSandbox()
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!global) return
    const show = () => setOpen(true)
    window.addEventListener(OPEN_EVENT, show)
    return () => window.removeEventListener(OPEN_EVENT, show)
  }, [global])
  const servers = useServers()
  const platforms = usePlatforms()
  const profiles = useProfiles()
  const vault = useVaultSecrets()
  const [secretIds, setSecretIds] = useState<Array<string>>([])
  const [name, setName] = useState("")
  const [kind, setKind] = useState("desktop")
  const [server, setServer] = useState(LOCAL)
  const [profile, setProfile] = useState(NO_PROFILE)
  const [admin, setAdmin] = useState("standard")
  // empty: the default
  const [limits, setLimits] = useState<Record<LimitKey, string>>(NO_LIMITS)
  const [error, setError] = useState<string>()
  const vm = VM_KINDS.includes(kind)
  const kindProfiles = (profiles.data ?? []).filter(
    (p) => p.platform === osOf(kind)
  )
  const platformServers = (servers.data ?? []).filter((s) =>
    s.capabilities.includes(osOf(kind))
  )
  // sandbox OSes no server here can run yet; until /platforms answers, offer everything
  const missing = (platforms.data ?? []).filter((p) => !p.available)
  const kinds = KINDS.map((k) => {
    const lacking = missing.find((p) => p.id === osOf(k.value))
    return lacking
      ? {
          ...k,
          label: `${lacking.name} · add a ${lacking.host} to enable`,
          disabled: true,
        }
      : k
  })

  function changeKind(next: string) {
    setKind(next)
    setServer(VM_KINDS.includes(next) ? AUTO : LOCAL)
    setProfile(NO_PROFILE)
    setAdmin("standard")
  }

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) {
      setName("")
      setKind("desktop")
      setServer(LOCAL)
      setProfile(NO_PROFILE)
      setAdmin("standard")
      setLimits(NO_LIMITS)
      setSecretIds([])
      setError(undefined)
      create.reset()
    }
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const parsed = createSandboxSchema.safeParse({
      name: name || undefined,
      kind,
      server_id: server === LOCAL ? null : server,
      profile_ids: profile === NO_PROFILE ? [] : [profile],
      secret_ids: secretIds,
      admin: kind === "macos" && admin === "admin" ? true : undefined,
      ...Object.fromEntries(
        LIMITS.filter((l) => limits[l.key] && !(vm && l.linuxOnly)).map((l) => [
          l.key,
          Number(limits[l.key]),
        ])
      ),
    })
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message)
      return
    }
    create.mutate(parsed.data, { onSuccess: () => handleOpenChange(false) })
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {!global && (
        <DialogTrigger render={<Button />}>
          <Plus />
          Create Sandbox
        </DialogTrigger>
      )}
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Create a sandbox</DialogTitle>
            <DialogDescription>
              Pick what you need and where it should run.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sandbox-name">Name</Label>
              <Input
                id="sandbox-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. research-box"
                maxLength={100}
                autoFocus
              />
            </div>
            <Choice
              id="sandbox-kind"
              label="Type"
              items={kinds}
              value={kind}
              onChange={changeKind}
            />
            {missing.length > 0 && (
              <p className="-mt-2 text-xs text-muted-foreground">
                {missing.map((p) => (
                  <span key={p.id} className="block">
                    <Link to="/servers" className="underline">
                      Add a {p.host}
                    </Link>{" "}
                    to enable {p.name} sandboxes.
                  </span>
                ))}
              </p>
            )}
            <Choice
              id="sandbox-server"
              label="Server"
              items={[
                ...(vm ? [] : [{ value: LOCAL, label: "This machine" }]),
                ...(platformServers.length || vm
                  ? [{ value: AUTO, label: "Least busy server" }]
                  : []),
                ...platformServers.map((s) => ({
                  value: s.id,
                  label: s.name,
                })),
              ]}
              value={server}
              onChange={setServer}
            />
            {kind === "macos" && (
              <Choice
                id="sandbox-admin"
                label="Guest user"
                items={[
                  { value: "standard", label: "Standard user (policies hold)" },
                  {
                    value: "admin",
                    label: "Administrator with sudo (can undo policies)",
                  },
                ]}
                value={admin}
                onChange={setAdmin}
              />
            )}
            {kind !== "code" && kindProfiles.length > 0 && (
              <Choice
                id="sandbox-profile"
                label="Profile"
                items={[
                  { value: NO_PROFILE, label: "Fresh profile" },
                  ...kindProfiles.map((p) => ({
                    value: p.id,
                    label: `${p.name} · ${p.app}`,
                  })),
                ]}
                value={profile}
                onChange={setProfile}
              />
            )}
            {(vault.data?.length ?? 0) > 0 && (
              <div className="flex flex-col gap-1.5">
                <Label>Secrets from vault</Label>
                <div className="flex flex-wrap gap-1.5">
                  {(vault.data ?? []).map((s) => {
                    const on = secretIds.includes(s.id)
                    return (
                      <Button
                        key={s.id}
                        type="button"
                        size="sm"
                        variant={on ? "default" : "outline"}
                        className="font-mono"
                        aria-pressed={on}
                        onClick={() =>
                          setSecretIds((ids) =>
                            on ? ids.filter((i) => i !== s.id) : [...ids, s.id]
                          )
                        }
                      >
                        {s.name}
                      </Button>
                    )
                  })}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {LIMITS.filter((l) => !(vm && l.linuxOnly)).map((l) => (
                <div key={l.key} className="flex flex-col gap-1.5">
                  <Label htmlFor={`sandbox-${l.key}`}>{l.label}</Label>
                  <Input
                    id={`sandbox-${l.key}`}
                    type="number"
                    min={l.step}
                    step={l.step}
                    value={limits[l.key]}
                    placeholder={l.placeholder}
                    onChange={(e) =>
                      setLimits({ ...limits, [l.key]: e.target.value })
                    }
                  />
                </div>
              ))}
            </div>
            <FieldError message={error ?? create.error?.message} />
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" type="button" />}>
              Cancel
            </DialogClose>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
