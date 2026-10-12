import { Link, createFileRoute } from "@tanstack/react-router"
import {
  AlarmClock,
  Check,
  FolderLock,
  History,
  KeyRound,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  Vault,
  X,
} from "lucide-react"
import { useState } from "react"

import { Page } from "@/components/page"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  useCreateVaultSecret,
  useProfiles,
  useRemoveProfile,
  useRemoveVaultSecret,
  useRenameProfile,
  useUpdateVaultSecret,
  useVaultActivity,
  useVaultReminders,
  useVaultSecrets,
  vaultSecretInputSchema,
} from "@/lib/api_client"
import type {
  Profile,
  SecretStatus,
  VaultActivity,
  VaultSecret,
} from "@/lib/api_client"
import { dateOf, formatBytes, relativeTime, timeAgo } from "@/lib/utils"

export const Route = createFileRoute("/_app/vault")({
  component: VaultPage,
})

function VaultPage() {
  return (
    <Page
      icon={Vault}
      title="Vault"
      description="Secrets and app profiles you can attach to any of your sandboxes."
    >
      <Tabs defaultValue="secrets">
        <TabsList>
          <TabsTrigger value="secrets">Secrets</TabsTrigger>
          <TabsTrigger value="profiles">App profiles</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>
        <TabsContent value="secrets" className="mt-4 flex flex-col gap-4">
          <Reminders />
          <SecuritySummary />
          <SecretsCard />
        </TabsContent>
        <TabsContent value="profiles" className="mt-4">
          <ProfilesCard />
        </TabsContent>
        <TabsContent value="activity" className="mt-4">
          <ActivityCard />
        </TabsContent>
      </Tabs>
    </Page>
  )
}

const STATUS: Record<
  Exclude<SecretStatus, "ok">,
  { label: string; tone: string }
> = {
  expired: { label: "Expired", tone: "border-destructive/40 text-destructive" },
  rotation_due: {
    label: "Rotation overdue",
    tone: "border-destructive/40 text-destructive",
  },
  expiring_soon: {
    label: "Expires soon",
    tone: "border-amber-500/40 text-amber-600 dark:text-amber-400",
  },
  rotation_soon: {
    label: "Rotate soon",
    tone: "border-amber-500/40 text-amber-600 dark:text-amber-400",
  },
}

function StatusBadge({ status }: { status: SecretStatus }) {
  if (status === "ok") return null
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${STATUS[status].tone}`}
    >
      {STATUS[status].label}
    </span>
  )
}

function Reminders() {
  const reminders = useVaultReminders()
  if (!reminders.data?.length) return null
  return (
    <div className="flex gap-3 rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm">
      <AlarmClock className="mt-0.5 size-4 shrink-0 text-amber-500" />
      <ul className="flex flex-col gap-1">
        {reminders.data.map((r) => (
          <li key={r.id}>
            <span className="font-mono">{r.name}</span>{" "}
            {r.message.slice(r.name.length + 1)} ({relativeTime(r.due_at)})
          </li>
        ))}
      </ul>
    </div>
  )
}

function SecuritySummary() {
  const points = [
    "Encrypted at rest with ZOO_SECRETS_KEY. Values are write-only and never sent back to the browser.",
    "A sandbox only gets the secrets you attach to it, as environment variables. Changes reach running sandboxes right away: new commands and terminals see them, programs already running keep what they started with.",
    "Secret values are masked as [redacted] in tool output and activity logs.",
    "Every change, attach, and profile load is recorded in Activity.",
  ]
  return (
    <div className="flex gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3 text-sm">
      <ShieldCheck className="mt-0.5 size-4 shrink-0 text-emerald-400" />
      <ul className="flex flex-col gap-1 text-muted-foreground">
        {points.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  )
}

function SecretsCard() {
  const secrets = useVaultSecrets()
  const create = useCreateVaultSecret()
  const [error, setError] = useState<string | null>(null)

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const fields = Object.fromEntries(new FormData(form)) as Record<
      string,
      string
    >
    const parsed = vaultSecretInputSchema.safeParse({
      name: fields.name,
      value: fields.value,
      description: fields.description,
      expires_at: fields.expires_at
        ? `${fields.expires_at}T00:00:00Z`
        : undefined,
      rotate_every_days: fields.rotate_every_days
        ? Number(fields.rotate_every_days)
        : undefined,
    })
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid secret")
      return
    }
    setError(null)
    create.mutate(
      { ...parsed.data, description: parsed.data.description || undefined },
      { onSuccess: () => form.reset() }
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4" />
          Secrets
        </CardTitle>
        <CardDescription>
          Attach them from a sandbox's Secrets tab or when you create a sandbox.
          A secret set on the sandbox itself overrides a vault secret with the
          same name. Give a secret an expiry date or a rotation interval to get
          reminders.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form onSubmit={onSubmit} className="flex flex-col gap-2">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              name="name"
              placeholder="GITHUB_TOKEN"
              className="font-mono sm:w-56"
              autoComplete="off"
            />
            <Input
              name="value"
              type="password"
              placeholder="Value"
              className="flex-1"
              autoComplete="new-password"
            />
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              name="description"
              placeholder="What it's for (optional)"
              className="flex-1"
            />
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Expires
              <Input name="expires_at" type="date" className="w-40" />
            </label>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Rotate every
              <Input
                name="rotate_every_days"
                inputMode="numeric"
                placeholder="days"
                className="w-24"
              />
              days
            </label>
            <span className="flex-1" />
            <Button type="submit" disabled={create.isPending}>
              <Plus />
              Add secret
            </Button>
          </div>
        </form>
        {(error ?? create.error) && (
          <p className="text-sm text-destructive">
            {error ?? create.error?.message}
          </p>
        )}
        {secrets.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {secrets.data.map((s) => (
              <SecretRow key={s.id} secret={s} />
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No secrets in the vault yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function SecretRow({ secret }: { secret: VaultSecret }) {
  const update = useUpdateVaultSecret()
  const remove = useRemoveVaultSecret()
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [value, setValue] = useState("")
  const [expires, setExpires] = useState(dateOf(secret.expires_at))
  const [every, setEvery] = useState(
    secret.rotate_every_days ? String(secret.rotate_every_days) : ""
  )
  const unused = secret.sandboxes.length === 0

  return (
    <div className="flex flex-col gap-2 px-3 py-2 text-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{secret.name}</span>
            <StatusBadge status={secret.status} />
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {secret.description ? `${secret.description} · ` : ""}
            Rotated {timeAgo(secret.rotated_at ?? secret.updated_at)} ·{" "}
            {secret.last_used_at
              ? `last injected ${timeAgo(secret.last_used_at)}`
              : "never injected"}
            {secret.expires_at &&
              ` · expires ${relativeTime(secret.expires_at)}`}
            {secret.rotation_due_at &&
              ` · rotation due ${relativeTime(secret.rotation_due_at)}`}
            {unused && " · not used anywhere"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Edit secret"
            onClick={() => setEditing((e) => !e)}
          >
            <Pencil />
          </Button>
          {confirming ? (
            <>
              <Button
                variant="destructive"
                size="sm"
                disabled={remove.isPending}
                onClick={() => remove.mutate(secret.id)}
              >
                Delete
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Cancel"
                onClick={() => setConfirming(false)}
              >
                <X />
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Delete secret"
              onClick={() => setConfirming(true)}
            >
              <Trash2 />
            </Button>
          )}
        </div>
      </div>
      {confirming && secret.sandboxes.length > 0 && (
        <p className="text-xs text-destructive">
          Removes it from {secret.sandboxes.length} sandbox
          {secret.sandboxes.length === 1 ? "" : "es"}, running ones right away.
        </p>
      )}
      {secret.sandboxes.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {secret.sandboxes.map((sb) => (
            <Link
              key={sb.id}
              to="/sandboxes/$sandboxId"
              params={{ sandboxId: sb.id }}
              title={
                sb.last_used_at
                  ? `Received ${timeAgo(sb.last_used_at)}`
                  : "Not received yet: it gets the secret on its next start"
              }
              className="rounded border border-border px-1.5 py-0.5 text-xs text-muted-foreground hover:text-foreground"
            >
              {sb.name}
              {sb.status && sb.status !== "running" && ` (${sb.status})`}
              {sb.last_used_at && ` · ${timeAgo(sb.last_used_at)}`}
            </Link>
          ))}
        </div>
      )}
      {editing && (
        <form
          className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"
          onSubmit={(event) => {
            event.preventDefault()
            update.mutate({
              id: secret.id,
              expires_at: expires ? `${expires}T00:00:00Z` : null,
              rotate_every_days: every ? Number(every) : null,
            })
          }}
        >
          <label className="flex items-center gap-2">
            Expires
            <Input
              type="date"
              value={expires}
              onChange={(e) => setExpires(e.target.value)}
              className="w-40"
            />
          </label>
          <label className="flex items-center gap-2">
            Rotate every
            <Input
              inputMode="numeric"
              value={every}
              onChange={(e) => setEvery(e.target.value)}
              placeholder="days"
              className="w-20"
            />
            days
          </label>
          <Button
            type="submit"
            size="sm"
            variant="outline"
            disabled={update.isPending}
          >
            Save schedule
          </Button>
        </form>
      )}
      {editing && (
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (!value) return
            update.mutate(
              { id: secret.id, value },
              {
                onSuccess: () => {
                  setValue("")
                  setEditing(false)
                },
              }
            )
          }}
        >
          <Input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="New value"
            autoComplete="new-password"
            autoFocus
          />
          <Button type="submit" disabled={!value || update.isPending}>
            Replace
          </Button>
        </form>
      )}
      {update.error && (
        <p className="text-xs text-destructive">{update.error.message}</p>
      )}
    </div>
  )
}

function ProfilesCard() {
  const profiles = useProfiles()
  const remove = useRemoveProfile()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FolderLock className="size-4" />
          App profiles
        </CardTitle>
        <CardDescription>
          Logins, cookies and settings saved from a sandbox's app, encrypted on
          disk. Save new ones or load them into a running sandbox from its
          Profiles tab, or pick one when creating a sandbox.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {remove.error && (
          <p className="pb-3 text-sm text-destructive">
            {remove.error.message}
          </p>
        )}
        {profiles.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {profiles.data.map((p) => (
              <ProfileRow
                key={p.id}
                profile={p}
                onDelete={() => remove.mutate(p.id)}
                deleting={remove.isPending}
              />
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No app profiles yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function ProfileRow({
  profile,
  onDelete,
  deleting,
}: {
  profile: Profile
  onDelete: () => void
  deleting: boolean
}) {
  const rename = useRenameProfile()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(profile.name)
  const [confirming, setConfirming] = useState(false)

  return (
    <div className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
      {editing ? (
        <form
          className="flex flex-1 gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (!name.trim()) return
            rename.mutate(
              { id: profile.id, name: name.trim() },
              { onSuccess: () => setEditing(false) }
            )
          }}
        >
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <Button
            type="submit"
            size="icon-sm"
            variant="ghost"
            aria-label="Save name"
            disabled={rename.isPending}
          >
            <Check />
          </Button>
        </form>
      ) : (
        <div className="min-w-0">
          <div className="font-medium">{profile.name}</div>
          <div className="text-xs text-muted-foreground">
            {profile.app} · v{profile.version}
            {profile.versions > 1 && ` of ${profile.versions}`} ·{" "}
            {formatBytes(profile.size_bytes)} · saved{" "}
            {timeAgo(profile.updated_at ?? profile.created_at)}
          </div>
        </div>
      )}
      <div className="flex shrink-0 items-center gap-1">
        {!editing && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Rename profile"
            onClick={() => setEditing(true)}
          >
            <Pencil />
          </Button>
        )}
        {confirming ? (
          <>
            <Button
              variant="destructive"
              size="sm"
              disabled={deleting}
              onClick={onDelete}
            >
              Delete
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Cancel"
              onClick={() => setConfirming(false)}
            >
              <X />
            </Button>
          </>
        ) : (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Delete profile"
            onClick={() => setConfirming(true)}
          >
            <Trash2 />
          </Button>
        )}
      </div>
    </div>
  )
}

const ACTION_LABELS: Record<string, string> = {
  "secret.create": "Added secret",
  "secret.rotate": "Replaced value of",
  "secret.describe": "Edited description of",
  "secret.delete": "Deleted secret",
  "secret.schedule": "Changed expiry or rotation of",
  "secret.attach": "Attached",
  "secret.detach": "Detached",
  "profile.capture": "Saved profile",
  "profile.load": "Loaded profile",
  "profile.rename": "Renamed profile",
  "profile.delete": "Deleted profile",
  "profile.delete_version": "Deleted a version of profile",
}

function describe(a: VaultActivity) {
  const name = String(a.metadata.name ?? a.resource_id ?? "")
  const sandbox = a.metadata.sandbox ? String(a.metadata.sandbox) : null
  const label = ACTION_LABELS[a.action] ?? a.action
  if (a.action === "secret.attach") return `${label} ${name} to ${sandbox}`
  if (a.action === "secret.detach") return `${label} ${name} from ${sandbox}`
  if (a.action === "profile.load") return `${label} ${name} into a sandbox`
  return `${label} ${name}`
}

function ActivityCard() {
  const activity = useVaultActivity()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History className="size-4" />
          Activity
        </CardTitle>
        <CardDescription>
          The last 100 changes to your secrets and profiles.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {activity.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {activity.data.map((a) => (
              <div
                key={a.id}
                className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <span className="min-w-0 truncate">{describe(a)}</span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {timeAgo(a.created_at)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nothing yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
