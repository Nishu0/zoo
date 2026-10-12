import { ShieldCheck } from "lucide-react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { usePermissions, useSetPermission } from "@/lib/api_client"

export function PolicyNotice() {
  return (
    <p className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
      <ShieldCheck className="size-4 shrink-0" />
      Applied live to the running sandbox and re-applied on every start. Domains
      are resolved to IPs when the rule is applied.
    </p>
  )
}

export function PermissionsTab({ sandboxId }: { sandboxId: string }) {
  const permissions = usePermissions(sandboxId)
  const setPermission = useSetPermission(sandboxId)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" />
          Agent permissions
        </CardTitle>
        <CardDescription>
          What agents and API clients may do in this sandbox. Enforced by the
          zoo API.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col divide-y divide-border">
        {permissions.isPending && <Skeleton className="h-24" />}
        {permissions.error && (
          <p className="text-sm text-destructive">
            {permissions.error.message}
          </p>
        )}
        {permissions.data?.map((p) => (
          <div
            key={`${p.permission}.${p.action}`}
            className="flex items-center justify-between py-3 first:pt-0 last:pb-0"
          >
            <div>
              <p className="text-sm font-medium">{p.label}</p>
              <p className="font-mono text-xs text-muted-foreground">
                {p.permission}.{p.action}
              </p>
            </div>
            <Switch
              checked={p.effect === "allow"}
              disabled={setPermission.isPending}
              onCheckedChange={(checked) =>
                setPermission.mutate({
                  permission: p.permission,
                  action: p.action,
                  effect: checked ? "allow" : "deny",
                })
              }
            />
          </div>
        ))}
        {setPermission.error && (
          <p className="pt-3 text-sm text-destructive">
            {setPermission.error.message}
          </p>
        )}
      </CardContent>
    </Card>
  )
}
