import {
  createFileRoute,
  Outlet,
  useLocation,
  useNavigate,
} from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { CommandPalette } from "@/components/command-palette"
import { CreateSandboxDialog } from "@/components/create-sandbox-dialog"
import { SiteHeader } from "@/components/site-header"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import type { Connection } from "@/lib/api_client"
import { useEventStream, useMe } from "@/lib/api_client"

export const Route = createFileRoute("/_app")({ component: AppLayout })

function AppLayout() {
  const navigate = useNavigate()
  const { data: user, isPending } = useMe()
  const [connection, setConnection] = useState<Connection>("connecting")
  useEventStream(Boolean(user), setConnection)

  const { pathname } = useLocation()

  useEffect(() => {
    if (!isPending && !user) {
      navigate({ to: "/login", replace: true })
    } else if (user?.onboarded_at === null && pathname !== "/welcome") {
      // first login: the three first-run steps
      navigate({ to: "/welcome", replace: true })
    }
  }, [isPending, user, navigate, pathname])

  if (!user) {
    return (
      <div className="flex min-h-svh items-center justify-center bg-background" />
    )
  }

  return (
    <SidebarProvider>
      <CommandPalette />
      <CreateSandboxDialog global />
      <AppSidebar user={user} />
      <SidebarInset>
        <SiteHeader />
        {connection === "offline" && (
          <div
            role="status"
            className="mx-2 mb-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive md:mx-4"
          >
            Can't reach the Zoo server. Retrying… Live updates resume once it's
            back.
          </div>
        )}
        <main className="flex flex-1 flex-col px-2 pb-2 md:px-4 md:pb-4">
          <div className="flex flex-1 flex-col rounded-xl border border-border bg-sidebar/40 p-1.5">
            <div className="flex flex-1 flex-col rounded-lg border border-border bg-background">
              <Outlet />
            </div>
          </div>
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
