import { createFileRoute, useNavigate } from "@tanstack/react-router"
import {
  CheckCircle2,
  Copy,
  ExternalLink,
  Globe,
  KeyRound,
  LogOut,
  Plus,
  Trash2,
  TriangleAlert,
  Send,
  UserRound,
  Webhook,
} from "lucide-react"
import { useState } from "react"

import { SecretOnce } from "@/components/connect"
import { Page } from "@/components/page"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  ApiError,
  MCP_URL,
  WEBHOOK_EVENT_TYPES,
  roleAtLeast,
  useCreateWebhook,
  useCurrentWorkspace,
  useRemoveWebhook,
  useTestWebhook,
  useUpdateWebhook,
  useWebhookDeliveries,
  useWebhooks,
  domainInputSchema,
  useAddDomain,
  useDomains,
  useRemoveDomain,
  useVerifyDomain,
  useApiKeys,
  useCreateApiKey,
  useLogout,
  useMe,
  useRevokeApiKey,
  useSandboxes,
} from "@/lib/api_client"
import type { Webhook as WebhookRow } from "@/lib/api_client"
import { dateOf, timeAgo } from "@/lib/utils"

export const Route = createFileRoute("/_app/settings")({
  component: ProfilePage,
})

function ProfilePage() {
  const { data: user } = useMe()
  const logout = useLogout()
  const navigate = useNavigate()

  const fields = [
    { label: "Name", value: user?.name || "—" },
    { label: "Email", value: user?.email },
    { label: "User ID", value: user?.id, mono: true },
  ]

  return (
    <Page icon={UserRound} title="Profile" description="Your zoo account.">
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>Details you signed up with.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {fields.map((field) => (
            <div key={field.label} className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">
                {field.label}
              </span>
              <span className={field.mono ? "font-mono text-sm" : "text-sm"}>
                {field.value}
              </span>
            </div>
          ))}
        </CardContent>
        <CardFooter className="justify-end border-t border-border pt-4">
          <Button
            variant="outline"
            onClick={() => {
              logout()
              navigate({ to: "/login" })
            }}
          >
            <LogOut />
            Log out
          </Button>
        </CardFooter>
      </Card>
      <ApiKeysCard />
      <WebhooksCard />
      <DomainsCard />
    </Page>
  )
}

const ALL_SANDBOXES = "all"
const LABELLED = "labels"

/** "end_user=alice, team=support" as {end_user: "alice", team: "support"}; null when a part isn't key=value. */
function parseLabels(text: string): Record<string, string> | null {
  const labels: Record<string, string> = {}
  for (const part of text.split(",")) {
    const [key, ...rest] = part.split("=")
    const value = rest.join("=").trim()
    if (!key.trim() || !value) return null
    labels[key.trim()] = value
  }
  return labels
}

const showLabels = (labels: Record<string, string>) =>
  Object.entries(labels)
    .map(([key, value]) => `${key}=${value}`)
    .join(", ")

function ApiKeysCard() {
  const keys = useApiKeys()
  const create = useCreateApiKey()
  const revoke = useRevokeApiKey()
  const sandboxes = useSandboxes()
  const [name, setName] = useState("")
  const [sandboxId, setSandboxId] = useState(ALL_SANDBOXES)
  const [readOnly, setReadOnly] = useState(false)
  const [expires, setExpires] = useState("")
  const [selector, setSelector] = useState("")
  const [created, setCreated] = useState<string | null>(null)
  const scopes = [
    { value: ALL_SANDBOXES, label: "Every sandbox" },
    { value: LABELLED, label: "Labelled sandboxes" },
    ...(sandboxes.data ?? []).map((s) => ({ value: s.id, label: s.name })),
  ]
  const labels = sandboxId === LABELLED ? parseLabels(selector) : null
  const sandboxName = (id: string) =>
    sandboxes.data?.find((s) => s.id === id)?.name ?? id.slice(0, 8)

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4" />
          API keys
        </CardTitle>
        <CardDescription>
          Use a key as <span className="font-mono">Authorization: Bearer</span>{" "}
          for the REST API, the Python SDK, or MCP at{" "}
          <span className="font-mono">{MCP_URL}</span>
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (!name.trim() || (sandboxId === LABELLED && !labels)) return
            create.mutate(
              {
                name: name.trim(),
                sandbox_id:
                  sandboxId === ALL_SANDBOXES || sandboxId === LABELLED
                    ? null
                    : sandboxId,
                label_selector: labels,
                read_only: readOnly,
                // the end of the chosen day
                expires_at: expires
                  ? new Date(`${expires}T23:59:59`).toISOString()
                  : null,
              },
              {
                onSuccess: (data) => {
                  setCreated(data.key)
                  setName("")
                },
              }
            )
          }}
        >
          <div className="flex gap-2">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Key name, e.g. claude-desktop"
            />
            <Button type="submit" disabled={create.isPending || !name.trim()}>
              <Plus />
              Create key
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Select
              items={scopes}
              value={sandboxId}
              onValueChange={(next) => next && setSandboxId(next)}
            >
              <SelectTrigger className="w-48" aria-label="Sandboxes it reaches">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {scopes.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {sandboxId === LABELLED && (
              <Input
                className="w-56"
                value={selector}
                onChange={(e) => setSelector(e.target.value)}
                placeholder="end_user=alice"
                aria-label="Labels the sandboxes must have"
              />
            )}
            <label className="flex items-center gap-2">
              <Switch checked={readOnly} onCheckedChange={setReadOnly} />
              Read-only
            </label>
            <label className="flex items-center gap-2 text-muted-foreground">
              Expires
              <Input
                type="date"
                className="w-40"
                value={expires}
                onChange={(e) => setExpires(e.target.value)}
              />
            </label>
          </div>
          {create.error instanceof ApiError && (
            <p className="text-sm text-destructive">{create.error.message}</p>
          )}
        </form>
        {created && <SecretOnce value={created} />}
        {keys.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {keys.data.map((k) => (
              <div
                key={k.id}
                className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <div className="flex min-w-0 flex-col">
                  <span>{k.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {k.key_prefix}…
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {k.sandbox_id
                      ? sandboxName(k.sandbox_id)
                      : k.label_selector
                        ? `Sandboxes labelled ${showLabels(k.label_selector)}`
                        : "Every sandbox"}
                    {k.read_only ? ", read-only" : ""}
                    {k.expires_at ? `, expires ${dateOf(k.expires_at)}` : ""}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  {k.revoked_at
                    ? "Revoked"
                    : k.last_used_at
                      ? `Used ${timeAgo(k.last_used_at)}`
                      : "Never used"}
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Revoke key"
                    disabled={!!k.revoked_at || revoke.isPending}
                    onClick={() => revoke.mutate(k.id)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-muted-foreground">
            No API keys yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function WebhooksCard() {
  const workspace = useCurrentWorkspace()
  const admin = roleAtLeast(workspace?.role, "admin")
  const webhooks = useWebhooks(admin)
  const create = useCreateWebhook()
  const [url, setUrl] = useState("")
  const [types, setTypes] = useState<string[]>([])
  const [selector, setSelector] = useState("")
  const [secret, setSecret] = useState<string | null>(null)
  const labels = selector.trim() ? parseLabels(selector) : null

  if (!admin) return null

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Webhook className="size-4" />
          Webhooks
        </CardTitle>
        <CardDescription>
          Zoo POSTs each event as JSON, signed with{" "}
          <span className="font-mono">Zoo-Signature: v1=</span>HMAC-SHA256 of{" "}
          <span className="font-mono">{"{Zoo-Timestamp}.{body}"}</span>, and
          retries failures for about 8 hours. The same events stream from{" "}
          <span className="font-mono">GET /events/stream</span>.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (!url.trim() || (selector.trim() && !labels)) return
            create.mutate(
              { url: url.trim(), types, label_selector: labels, enabled: true },
              {
                onSuccess: (data) => {
                  setSecret(data.secret)
                  setUrl("")
                },
              }
            )
          }}
        >
          <div className="flex gap-2">
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/zoo-events"
            />
            <Button type="submit" disabled={create.isPending || !url.trim()}>
              <Plus />
              Add webhook
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {WEBHOOK_EVENT_TYPES.map((type) => (
              <Button
                key={type}
                type="button"
                size="sm"
                variant={types.includes(type) ? "default" : "outline"}
                onClick={() =>
                  setTypes((now) =>
                    now.includes(type)
                      ? now.filter((t) => t !== type)
                      : [...now, type]
                  )
                }
              >
                {type}
              </Button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {types.length
              ? "Only these events."
              : "Every event, unless you pick some."}
          </p>
          <Input
            value={selector}
            onChange={(e) => setSelector(e.target.value)}
            placeholder="Only sandboxes labelled… (optional), e.g. end_user=alice"
          />
          {create.error && (
            <p className="text-sm text-destructive">{create.error.message}</p>
          )}
        </form>
        {secret && (
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
              <span className="flex-1 truncate font-mono text-xs">
                {secret}
              </span>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Copy signing secret"
                onClick={() => navigator.clipboard.writeText(secret)}
              >
                <Copy />
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              The signing secret. Copy it now; you won't see it again.
            </p>
          </div>
        )}
        {webhooks.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {webhooks.data.map((hook) => (
              <WebhookRowView key={hook.id} hook={hook} />
            ))}
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-muted-foreground">
            No webhooks yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function WebhookRowView({ hook }: { hook: WebhookRow }) {
  const update = useUpdateWebhook()
  const remove = useRemoveWebhook()
  const test = useTestWebhook()
  const [open, setOpen] = useState(false)
  const deliveries = useWebhookDeliveries(hook.id, open)
  const error = update.error ?? remove.error ?? test.error

  return (
    <div className="flex flex-col gap-2 px-3 py-2 text-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-mono text-xs">{hook.url}</span>
          <span className="text-xs text-muted-foreground">
            {hook.types.length ? hook.types.join(", ") : "Every event"}
            {hook.label_selector
              ? `, sandboxes labelled ${showLabels(hook.label_selector)}`
              : ""}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Switch
            checked={hook.enabled}
            aria-label="Enabled"
            onCheckedChange={(enabled) =>
              update.mutate({
                id: hook.id,
                url: hook.url,
                types: hook.types,
                label_selector: hook.label_selector,
                enabled,
              })
            }
          />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Send a test event"
            disabled={test.isPending}
            onClick={() =>
              test.mutate(hook.id, { onSuccess: () => setOpen(true) })
            }
          >
            <Send />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setOpen((o) => !o)}>
            {open ? "Hide deliveries" : "Deliveries"}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Remove webhook"
            disabled={remove.isPending}
            onClick={() => remove.mutate(hook.id)}
          >
            <Trash2 />
          </Button>
        </div>
      </div>
      {error && <p className="text-xs text-destructive">{error.message}</p>}
      {open &&
        (deliveries.data?.length ? (
          <div className="flex flex-col gap-1 rounded-md bg-muted/40 p-2 text-xs">
            {deliveries.data.map((d) => (
              <div key={d.id} className="flex justify-between gap-2">
                <span className="font-mono">{d.event_type}</span>
                <span
                  className={
                    d.status === "failed"
                      ? "text-destructive"
                      : "text-muted-foreground"
                  }
                  title={d.last_error ?? undefined}
                >
                  {d.status === "delivered"
                    ? `Delivered ${timeAgo(d.delivered_at ?? d.created_at)}`
                    : d.status === "failed"
                      ? `Failed after ${d.attempts} tries${d.response_status ? ` (HTTP ${d.response_status})` : ""}`
                      : d.attempts
                        ? `Retrying, ${d.attempts} tries so far`
                        : "Sending"}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            {deliveries.isPending ? "Loading…" : "No deliveries yet."}
          </p>
        ))}
    </div>
  )
}

function DomainsCard() {
  const domains = useDomains()
  const add = useAddDomain()
  const remove = useRemoveDomain()
  const verify = useVerifyDomain()
  const [hostname, setHostname] = useState("")
  const [error, setError] = useState<string>()
  const forbidden =
    domains.error instanceof ApiError && domains.error.status === 403

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const parsed = domainInputSchema.safeParse(hostname)
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message)
      return
    }
    setError(undefined)
    add.mutate(parsed.data, { onSuccess: () => setHostname("") })
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe className="size-4" />
          Domains
        </CardTitle>
        <CardDescription>
          Serve this dashboard on your own domain. Point an A record at this
          server, add the domain here, and HTTPS is issued automatically on the
          first visit. Needs the stack running with{" "}
          <span className="font-mono">--profile domain</span>.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {forbidden ? (
          <p className="text-sm text-muted-foreground">
            Only admins can manage domains. Add your email to{" "}
            <span className="font-mono">ADMIN_EMAILS</span> on the API.
          </p>
        ) : (
          <>
            <form onSubmit={onSubmit} className="flex gap-2">
              <Input
                value={hostname}
                onChange={(event) => setHostname(event.target.value)}
                placeholder="zoo.example.com"
                className="flex-1"
              />
              <Button type="submit" disabled={add.isPending}>
                <Plus />
                Add domain
              </Button>
            </form>
            {(error ?? add.error ?? domains.error) && (
              <p className="text-sm text-destructive">
                {error ?? add.error?.message ?? domains.error?.message}
              </p>
            )}
            {domains.data?.length ? (
              <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
                {domains.data.map((d) => (
                  <div
                    key={d.id}
                    className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
                  >
                    <div className="min-w-0">
                      <a
                        href={d.url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1 font-medium hover:underline"
                      >
                        {d.hostname}
                        <ExternalLink className="size-3" />
                      </a>
                      <div className="flex items-center gap-1 text-xs text-muted-foreground">
                        {d.points_here ? (
                          <CheckCircle2 className="size-3 text-emerald-500" />
                        ) : (
                          <TriangleAlert className="size-3 text-amber-500" />
                        )}
                        {d.addresses.length === 0
                          ? "No DNS record yet"
                          : d.points_here === false
                            ? `Points to ${d.addresses.join(", ")}, expected ${d.public_ip}`
                            : `Resolves to ${d.addresses.join(", ")}`}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      {!d.points_here && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={verify.isPending}
                          onClick={() => verify.mutate(d.id)}
                        >
                          {verify.isPending && verify.variables === d.id
                            ? "Checking…"
                            : "Check DNS again"}
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Remove domain"
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(d.id)}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              !domains.isPending && (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  No domains yet.
                </p>
              )
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
