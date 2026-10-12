import {
  createFileRoute,
  Link,
  Outlet,
  useNavigate,
} from "@tanstack/react-router"
import {
  Box,
  Download,
  ExternalLink,
  Play,
  RotateCcw,
  Square,
  Trash2,
} from "lucide-react"

import { ConnectDialog } from "@/components/connect"
import { EmptyState, Page } from "@/components/page"
import { SandboxContext } from "@/components/sandbox/context"
import { SandboxProgress } from "@/components/sandbox/progress"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  displayStatus,
  downloadBackup,
  useDeleteSandbox,
  useSandbox,
  useSandboxAction,
} from "@/lib/api_client"
import type { Sandbox } from "@/lib/api_client"

export const Route = createFileRoute("/_app/sandboxes/$sandboxId")({
  component: SandboxLayout,
})

type Tab = {
  to: string
  label: string
  shown?: (sandbox: Sandbox) => boolean
}

const desktop = (s: Sandbox) => s.kind !== "code"
const linux = (s: Sandbox) => s.kind !== "macos" && s.kind !== "windows"

// each tab is a route of its own under /sandboxes/$sandboxId
export const TABS: Tab[] = [
  { to: "/sandboxes/$sandboxId", label: "Overview" },
  { to: "/sandboxes/$sandboxId/agent", label: "Agent", shown: desktop },
  { to: "/sandboxes/$sandboxId/terminal", label: "Terminal", shown: linux },
  { to: "/sandboxes/$sandboxId/permissions", label: "Permissions" },
  { to: "/sandboxes/$sandboxId/network", label: "Network" },
  { to: "/sandboxes/$sandboxId/apps", label: "Apps" },
  { to: "/sandboxes/$sandboxId/secrets", label: "Secrets" },
  { to: "/sandboxes/$sandboxId/activity", label: "Activity" },
  { to: "/sandboxes/$sandboxId/profiles", label: "Profiles", shown: desktop },
  { to: "/sandboxes/$sandboxId/snapshots", label: "Snapshots" },
  { to: "/sandboxes/$sandboxId/share", label: "Share", shown: desktop },
  { to: "/sandboxes/$sandboxId/server", label: "Server" },
]

function SandboxLayout() {
  const { sandboxId } = Route.useParams()
  const navigate = useNavigate()
  const sandbox = useSandbox(sandboxId)
  const remove = useDeleteSandbox()
  const start = useSandboxAction("start")
  const stop = useSandboxAction("stop")

  if (sandbox.isPending) {
    return (
      <Page icon={Box} title={<Skeleton className="h-6 w-40" />}>
        <Skeleton className="h-64 rounded-xl" />
      </Page>
    )
  }

  if (sandbox.error) {
    return (
      <Page icon={Box} title="Sandbox">
        <EmptyState
          icon={Box}
          title="Sandbox not found"
          description={sandbox.error.message}
          action={
            <Button variant="outline" render={<Link to="/sandboxes" />}>
              Back to sandboxes
            </Button>
          }
        />
      </Page>
    )
  }

  const data = sandbox.data
  const running = data.status === "running"
  const failed = data.status === "failed"
  const startable = data.status === "stopped" || failed
  const busy = data.job !== null

  return (
    <Page
      icon={Box}
      title={
        <>
          {data.name}
          <StatusBadge
            status={remove.isPending ? "deleting" : displayStatus(data)}
          />
        </>
      }
      description={<span className="font-mono">{data.id}</span>}
      action={
        <>
          <ConnectDialog sandbox={data} />
          {running ? (
            <Button
              variant="outline"
              disabled={stop.isPending || busy}
              onClick={() => stop.mutate(data.id)}
            >
              <Square />
              Stop
            </Button>
          ) : (
            <Button
              variant="outline"
              disabled={!startable || start.isPending || busy}
              onClick={() => start.mutate(data.id)}
            >
              {failed ? <RotateCcw /> : <Play />}
              {failed ? "Retry" : "Start"}
            </Button>
          )}
          <Button
            variant="outline"
            disabled={!running}
            onClick={() => void downloadBackup(data.id, data.name)}
          >
            <Download />
            Export .tar
          </Button>
          <Button
            variant="outline"
            disabled={remove.isPending}
            onClick={() =>
              remove.mutate(data.id, {
                onSuccess: () => navigate({ to: "/sandboxes" }),
              })
            }
          >
            <Trash2 />
            Delete
          </Button>
          <Button
            disabled={!running || data.kind === "code"}
            render={
              <Link
                to="/view/$sandboxId"
                params={{ sandboxId: data.id }}
                target="_blank"
              />
            }
          >
            <ExternalLink />
            Open desktop
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <SandboxProgress sandbox={data} />
        <nav
          aria-label="Sandbox sections"
          className="flex w-fit max-w-full gap-1 overflow-x-auto rounded-lg bg-muted p-[3px]"
        >
          {TABS.filter((t) => !t.shown || t.shown(data)).map((t) => (
            <Link
              key={t.to}
              to={t.to}
              params={{ sandboxId: data.id }}
              activeOptions={{ exact: true }}
              className="rounded-md px-2.5 py-1 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground"
              activeProps={{
                className: "bg-background text-foreground shadow-sm",
              }}
            >
              {t.label}
            </Link>
          ))}
        </nav>
        <SandboxContext.Provider value={data}>
          <Outlet />
        </SandboxContext.Provider>
      </div>
    </Page>
  )
}
