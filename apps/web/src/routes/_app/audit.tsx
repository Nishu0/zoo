import { createFileRoute } from "@tanstack/react-router"
import { Download, ScrollText } from "lucide-react"
import { useState } from "react"

import { Page } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  ApiError,
  downloadAuditLog,
  roleAtLeast,
  useAuditLog,
  useCurrentWorkspace,
} from "@/lib/api_client"
import type { AuditEntry, AuditFilters } from "@/lib/api_client"
import { timeAgo } from "@/lib/utils"

export const Route = createFileRoute("/_app/audit")({
  component: AuditPage,
})

const FIELDS: Array<{ key: keyof AuditFilters; label: string; type?: string }> =
  [
    { key: "action", label: "Action, e.g. POST /sandboxes" },
    { key: "resource_type", label: "Resource, e.g. sandboxes" },
    { key: "sandbox_id", label: "Sandbox id" },
    { key: "actor", label: "User id" },
    { key: "since", label: "From", type: "date" },
    { key: "until", label: "To", type: "date" },
  ]

function AuditPage() {
  const workspace = useCurrentWorkspace()
  const [draft, setDraft] = useState<AuditFilters>({})
  const [filters, setFilters] = useState<AuditFilters>({})
  const log = useAuditLog(filters)
  const [exportError, setExportError] = useState<string | null>(null)
  const entries = log.data?.pages.flatMap((p) => p.data) ?? []

  if (workspace && !roleAtLeast(workspace.role, "admin")) {
    return (
      <Page icon={ScrollText} title="Audit log">
        <p className="text-sm text-muted-foreground">
          The audit log is for the workspace's admins and owners.
        </p>
      </Page>
    )
  }

  return (
    <Page
      icon={ScrollText}
      title="Audit log"
      description="Every change anyone made in this workspace, newest first. Request bodies are never kept."
      action={
        <Button
          variant="outline"
          onClick={() => {
            setExportError(null)
            downloadAuditLog(filters).catch((e) =>
              setExportError(
                e instanceof ApiError ? e.message : "Export failed"
              )
            )
          }}
        >
          <Download />
          Export CSV
        </Button>
      }
    >
      <form
        className="grid gap-2 sm:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault()
          setFilters({ ...draft })
        }}
      >
        {FIELDS.map((f) => (
          <Input
            key={f.key}
            type={f.type ?? "text"}
            aria-label={f.label}
            placeholder={f.label}
            value={draft[f.key] ?? ""}
            onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
          />
        ))}
        <div className="flex gap-2 sm:col-span-3">
          <Button type="submit">Filter</Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setDraft({})
              setFilters({})
            }}
          >
            Clear
          </Button>
        </div>
      </form>
      {exportError && <p className="text-sm text-destructive">{exportError}</p>}
      {log.error && (
        <p className="text-sm text-destructive">
          {log.error instanceof ApiError
            ? log.error.message
            : "Couldn't load the audit log."}
        </p>
      )}
      <Card>
        <CardContent className="flex flex-col divide-y divide-border p-0">
          {entries.map((entry) => (
            <AuditRow key={entry.id} entry={entry} />
          ))}
          {!log.isPending && entries.length === 0 && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Nothing recorded yet.
            </p>
          )}
        </CardContent>
      </Card>
      {log.hasNextPage && (
        <Button
          variant="outline"
          className="self-center"
          disabled={log.isFetchingNextPage}
          onClick={() => log.fetchNextPage()}
        >
          Load more
        </Button>
      )}
    </Page>
  )
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const system = entry.metadata.source === "system"
  return (
    <div className="flex flex-wrap items-start justify-between gap-2 px-4 py-3 text-sm">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="font-mono text-xs">{entry.action}</span>
        <span className="truncate text-xs text-muted-foreground">
          {system ? "Zoo" : (entry.actor_email ?? "a removed user")}
          {entry.metadata.api_key_id ? " (API key)" : ""}
          {entry.resource_id
            ? ` · ${entry.resource_type} ${entry.resource_id}`
            : ""}
          {typeof entry.metadata.reason === "string"
            ? ` · ${entry.metadata.reason}`
            : ""}
        </span>
      </div>
      <span className="text-xs text-muted-foreground">
        {timeAgo(entry.created_at)}
      </span>
    </div>
  )
}
