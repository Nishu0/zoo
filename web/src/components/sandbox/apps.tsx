import { AppWindow } from "lucide-react"
import { EmptyState } from "@/components/page"
import { PolicyNotice } from "@/components/sandbox/permissions"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { useApps, useSetApp } from "@/lib/api_client"

export function AppsTab({
  sandboxId,
  running,
}: {
  sandboxId: string
  running: boolean
}) {
  const apps = useApps(sandboxId, running)
  const setApp = useSetApp(sandboxId)

  if (!running) {
    return (
      <EmptyState
        icon={AppWindow}
        title="Sandbox isn't running"
        description="Apps are read from the running desktop."
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <PolicyNotice />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AppWindow className="size-4" />
            Installed apps
          </CardTitle>
          <CardDescription>
            Desktop apps found in this sandbox and whether they may be launched.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col divide-y divide-border">
          {apps.isPending && <Skeleton className="h-48" />}
          {apps.error && (
            <p className="text-sm text-destructive">{apps.error.message}</p>
          )}
          {apps.data?.map((app) => (
            <div
              key={app.binary}
              className="flex items-center justify-between py-2.5 first:pt-0 last:pb-0"
            >
              <div>
                <p className="text-sm font-medium">{app.name}</p>
                <p className="font-mono text-xs text-muted-foreground">
                  {app.binary}
                </p>
              </div>
              <Switch
                checked={app.effect === "allow"}
                disabled={setApp.isPending}
                onCheckedChange={(checked) =>
                  setApp.mutate({
                    binary: app.binary,
                    effect: checked ? "allow" : "deny",
                  })
                }
              />
            </div>
          ))}
          {setApp.error && (
            <p className="pt-3 text-sm text-destructive">
              {setApp.error.message}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
