import { createFileRoute, Link } from "@tanstack/react-router"
import {
  Box,
  Copy,
  ExternalLink,
  MoreHorizontal,
  Search,
  Trash2,
} from "lucide-react"
import { useMemo, useState } from "react"

import {
  CreateSandboxDialog,
  openCreateSandbox,
} from "@/components/create-sandbox-dialog"
import { EmptyState, Page } from "@/components/page"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { displayStatus, useDeleteSandbox, useSandboxes } from "@/lib/api_client"
import type { Sandbox } from "@/lib/api_client"
import { timeAgo } from "@/lib/utils"

export const Route = createFileRoute("/_app/sandboxes/")({
  component: SandboxesPage,
})

const SORTS = {
  newest: {
    label: "Newest first",
    compare: (a: Sandbox, b: Sandbox) =>
      b.created_at.localeCompare(a.created_at),
  },
  oldest: {
    label: "Oldest first",
    compare: (a: Sandbox, b: Sandbox) =>
      a.created_at.localeCompare(b.created_at),
  },
  name: {
    label: "Name A–Z",
    compare: (a: Sandbox, b: Sandbox) => a.name.localeCompare(b.name),
  },
  status: {
    label: "Status",
    compare: (a: Sandbox, b: Sandbox) => a.status.localeCompare(b.status),
  },
}

type SortKey = keyof typeof SORTS

const SORT_ITEMS = Object.entries(SORTS).map(([value, sort]) => ({
  value,
  label: sort.label,
}))

function SandboxesPage() {
  const sandboxes = useSandboxes()
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState<SortKey>("newest")

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (sandboxes.data ?? [])
      .filter(
        (s) =>
          !q ||
          s.name.toLowerCase().includes(q) ||
          s.id.startsWith(q) ||
          s.status.includes(q)
      )
      .sort(SORTS[sort].compare)
  }, [sandboxes.data, query, sort])

  return (
    <Page
      icon={Box}
      title="Sandboxes"
      description="Create and manage your sandbox desktops"
      action={<CreateSandboxDialog />}
    >
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter sandboxes..."
            className="h-9 pr-9"
          />
          <Search className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted-foreground" />
        </div>
        <Select
          items={SORT_ITEMS}
          value={sort}
          onValueChange={(value) => value && setSort(value)}
        >
          <SelectTrigger className="h-9 w-44 data-[size=default]:h-9">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SORT_ITEMS.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {sandboxes.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-32 rounded-xl" />
          ))}
        </div>
      ) : sandboxes.error ? (
        <EmptyState
          icon={Box}
          title="Couldn't load sandboxes"
          description={sandboxes.error.message}
          action={
            <Button variant="outline" onClick={() => sandboxes.refetch()}>
              Try again
            </Button>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Box}
          title={query ? "No sandboxes found" : "No sandboxes yet"}
          description={
            query
              ? "Nothing matches that filter."
              : "Create a sandbox to spin up a desktop in seconds. Press c any time, or ⌘K for everything."
          }
          action={
            query ? undefined : (
              <Button onClick={openCreateSandbox}>Create a sandbox</Button>
            )
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((sandbox) => (
            <SandboxCard key={sandbox.id} sandbox={sandbox} />
          ))}
        </div>
      )}
    </Page>
  )
}

function SandboxCard({ sandbox }: { sandbox: Sandbox }) {
  const remove = useDeleteSandbox()
  const running = sandbox.status === "running"

  return (
    <div className="group relative flex flex-col gap-4 rounded-xl border border-border bg-card p-4 transition-colors hover:border-foreground/20">
      <Link
        to="/sandboxes/$sandboxId"
        params={{ sandboxId: sandbox.id }}
        className="absolute inset-0 rounded-xl"
        aria-label={`Manage ${sandbox.name}`}
      />
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted">
            <Box className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="truncate font-medium">{sandbox.name}</div>
            <div className="font-mono text-xs text-muted-foreground">
              {sandbox.id.slice(0, 8)}
            </div>
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                className="relative z-10"
                aria-label="Sandbox actions"
              />
            }
          >
            <MoreHorizontal />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              disabled={!running || sandbox.kind === "code"}
              render={
                <Link
                  to="/view/$sandboxId"
                  params={{ sandboxId: sandbox.id }}
                  target="_blank"
                />
              }
            >
              <ExternalLink />
              Open desktop
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => navigator.clipboard.writeText(sandbox.id)}
            >
              <Copy />
              Copy ID
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate(sandbox.id)}
            >
              <Trash2 />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {sandbox.error_message && (
        <p className="line-clamp-2 text-xs text-destructive">
          {sandbox.error_message}
        </p>
      )}

      <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground">
        <StatusBadge
          status={remove.isPending ? "deleting" : displayStatus(sandbox)}
        />
        <span>Created {timeAgo(sandbox.created_at)}</span>
      </div>
    </div>
  )
}
