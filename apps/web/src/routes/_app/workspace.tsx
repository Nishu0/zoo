import { createFileRoute } from "@tanstack/react-router"
import { Copy, Mail, Plus, Trash2, Users } from "lucide-react"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  ApiError,
  ROLES,
  roleAtLeast,
  useCreateWorkspace,
  useCurrentWorkspace,
  useInvitations,
  useInvite,
  useMe,
  useMembers,
  useQuota,
  useSetLifecycleDefaults,
  useRemoveMember,
  useRenameWorkspace,
  useRevokeInvitation,
  useSetRole,
  useSwitchWorkspace,
} from "@/lib/api_client"
import type { Role, Workspace } from "@/lib/api_client"
import { timeAgo } from "@/lib/utils"

export const Route = createFileRoute("/_app/workspace")({
  component: WorkspacePage,
})

const ROLE_ITEMS = ROLES.map((r) => ({
  value: r,
  label: r[0].toUpperCase() + r.slice(1),
}))
const INVITE_ROLES = ROLE_ITEMS.filter((r) => r.value !== "owner")

function errorText(error: unknown) {
  return error instanceof ApiError ? error.message : null
}

function WorkspacePage() {
  const workspace = useCurrentWorkspace()

  return (
    <Page
      icon={Users}
      title="Workspace"
      description="Who shares this workspace's sandboxes, servers, secrets and profiles, and what they can do."
    >
      {workspace && <AboutCard workspace={workspace} />}
      {workspace && <QuotaCard workspace={workspace} />}
      {workspace && <AutoStopCard workspace={workspace} />}
      {workspace && <MembersCard workspace={workspace} />}
      {workspace && roleAtLeast(workspace.role, "admin") && (
        <InviteCard workspace={workspace} />
      )}
      <NewWorkspaceCard />
    </Page>
  )
}

function AboutCard({ workspace }: { workspace: Workspace }) {
  const rename = useRenameWorkspace()
  const [name, setName] = useState(workspace.name)
  const admin = roleAtLeast(workspace.role, "admin")

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle>
          {workspace.personal ? "Personal workspace" : workspace.name}
        </CardTitle>
        <CardDescription>
          You are {workspace.role === "admin" ? "an" : "a"} {workspace.role}{" "}
          here. Viewers look, members use sandboxes, admins manage servers,
          secrets, profiles, keys and people, owners manage admins too.
        </CardDescription>
      </CardHeader>
      {admin && !workspace.personal && (
        <CardContent>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (name.trim())
                rename.mutate({ id: workspace.id, name: name.trim() })
            }}
          >
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-label="Workspace name"
            />
            <Button
              type="submit"
              variant="outline"
              disabled={rename.isPending || !name.trim()}
            >
              Rename
            </Button>
          </form>
        </CardContent>
      )}
    </Card>
  )
}

function MembersCard({ workspace }: { workspace: Workspace }) {
  const { data: me } = useMe()
  const members = useMembers(workspace.id)
  const setRole = useSetRole()
  const remove = useRemoveMember()
  const admin = roleAtLeast(workspace.role, "admin")
  const owner = workspace.role === "owner"
  const error = errorText(setRole.error) ?? errorText(remove.error)

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle>Members</CardTitle>
        <CardDescription>
          Admins change members and viewers; only owners change admins and
          owners. There is always at least one owner.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {members.data?.map((m) => {
            const self = m.user_id === me?.id
            // what the API allows: admins manage members and viewers, owners everyone
            const manageable =
              admin && (owner || (m.role !== "owner" && m.role !== "admin"))
            const roles = owner
              ? ROLE_ITEMS
              : ROLE_ITEMS.filter(
                  (r) => r.value === "member" || r.value === "viewer"
                )
            return (
              <div
                key={m.user_id}
                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="truncate">
                    {m.name || m.email}
                    {self && " (you)"}
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {m.email}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {manageable && !self ? (
                    <Select
                      items={roles}
                      value={m.role}
                      onValueChange={(role) =>
                        role &&
                        setRole.mutate({
                          id: workspace.id,
                          userId: m.user_id,
                          role: role,
                        })
                      }
                    >
                      <SelectTrigger className="w-28" aria-label="Role">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {roles.map((r) => (
                          <SelectItem key={r.value} value={r.value}>
                            {r.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      {m.role}
                    </span>
                  )}
                  {(self ? !workspace.personal : manageable) && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={self ? "Leave workspace" : "Remove member"}
                      disabled={remove.isPending}
                      onClick={() =>
                        remove.mutate({ id: workspace.id, userId: m.user_id })
                      }
                    >
                      <Trash2 />
                    </Button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

function InviteCard({ workspace }: { workspace: Workspace }) {
  const invitations = useInvitations(workspace.id, true)
  const invite = useInvite()
  const revoke = useRevokeInvitation()
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<Role>("member")
  const [link, setLink] = useState<string | null>(null)
  const roles =
    workspace.role === "owner"
      ? INVITE_ROLES
      : INVITE_ROLES.filter((r) => r.value !== "admin")
  const error = errorText(invite.error)

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Mail className="size-4" />
          Invite people
        </CardTitle>
        <CardDescription>
          Zoo doesn't send email: copy the link and send it yourself. It works
          once, for 7 days, for whoever signs in with that address.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (!email.trim()) return
            invite.mutate(
              { id: workspace.id, email: email.trim(), role },
              {
                onSuccess: (data) => {
                  setLink(`${window.location.origin}/invite/${data.token}`)
                  setEmail("")
                },
              }
            )
          }}
        >
          <Input
            className="min-w-56 flex-1"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@example.com"
          />
          <Select
            items={roles}
            value={role}
            onValueChange={(next) => next && setRole(next)}
          >
            <SelectTrigger className="w-28" aria-label="Role">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {roles.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="submit" disabled={invite.isPending || !email.trim()}>
            <Plus />
            Invite
          </Button>
        </form>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {link && (
          <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
            <span className="flex-1 truncate font-mono text-xs">{link}</span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Copy invite link"
              onClick={() => navigator.clipboard.writeText(link)}
            >
              <Copy />
            </Button>
          </div>
        )}
        {invitations.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {invitations.data.map((i) => (
              <div
                key={i.id}
                className="flex items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="truncate">{i.email}</span>
                  <span className="text-xs text-muted-foreground">
                    {i.role}, invited {timeAgo(i.created_at)}
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Revoke invitation"
                  disabled={revoke.isPending}
                  onClick={() =>
                    revoke.mutate({ id: workspace.id, invitationId: i.id })
                  }
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

function NewWorkspaceCard() {
  const create = useCreateWorkspace()
  const switchTo = useSwitchWorkspace()
  const [name, setName] = useState("")

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle>New workspace</CardTitle>
        <CardDescription>
          A workspace for a team: you own it and invite the rest.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (!name.trim()) return
            create.mutate(name.trim(), {
              onSuccess: (workspace) => {
                setName("")
                switchTo(workspace)
              },
            })
          }}
        >
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Workspace name"
          />
          <Button type="submit" disabled={create.isPending || !name.trim()}>
            <Plus />
            Create
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}

function QuotaCard({ workspace }: { workspace: Workspace }) {
  const quota = useQuota(workspace.id)
  const q = quota.data
  if (!q) return null
  const rows = [
    ["Running sandboxes", q.running_sandboxes, q.max_running_sandboxes],
    ["CPUs", q.cpus, q.max_cpus],
    ["Memory (MB)", q.memory_mb, q.max_memory_mb],
    ["Storage (GB)", q.storage_gb, q.max_storage_gb],
  ] as const
  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle>Usage and quota</CardTitle>
        <CardDescription>
          What the workspace uses now against its limits. Zoo's admins set the
          limits.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {rows.map(([label, used, limit]) => (
          <div key={label} className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">{label}</span>
            <span className="text-sm tabular-nums">
              {used}
              <span className="text-muted-foreground">
                {" / "}
                {limit ?? "no limit"}
              </span>
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function AutoStopCard({ workspace }: { workspace: Workspace }) {
  const save = useSetLifecycleDefaults()
  const [idle, setIdle] = useState(
    workspace.default_idle_timeout_minutes?.toString() ?? ""
  )
  const [lifetime, setLifetime] = useState(
    workspace.default_max_lifetime_minutes?.toString() ?? ""
  )
  const admin = roleAtLeast(workspace.role, "admin")
  const error = errorText(save.error)

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle>Auto-stop</CardTitle>
        <CardDescription>
          Defaults for sandboxes without settings of their own. Idle means no
          tool call and no open viewer. Leave empty to never stop.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate({
              id: workspace.id,
              default_idle_timeout_minutes: idle ? Number(idle) : null,
              default_max_lifetime_minutes: lifetime ? Number(lifetime) : null,
            })
          }}
        >
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Stop when idle for (minutes)
            <Input
              type="number"
              min={1}
              className="w-40"
              value={idle}
              disabled={!admin}
              onChange={(e) => setIdle(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Stop after running for (minutes)
            <Input
              type="number"
              min={1}
              className="w-40"
              value={lifetime}
              disabled={!admin}
              onChange={(e) => setLifetime(e.target.value)}
            />
          </label>
          {admin && (
            <Button type="submit" variant="outline" disabled={save.isPending}>
              Save
            </Button>
          )}
        </form>
        {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  )
}
