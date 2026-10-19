import type { LucideIcon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  trend,
  className,
}: {
  label: string
  value: string
  hint?: string
  icon: LucideIcon
  trend?: { direction: "up" | "down"; label: string }
  className?: string
}) {
  return (
    <Card className={cn(className)}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
        <Icon className="size-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tracking-tight">{value}</div>
        {(hint || trend) && (
          <p className="mt-1 text-xs text-muted-foreground">
            {trend && (
              <span className={trend.direction === "up" ? "text-emerald-600" : "text-red-600"}>
                {trend.direction === "up" ? "↑" : "↓"} {trend.label}{" "}
              </span>
            )}
            {hint}
          </p>
        )}
      </CardContent>
    </Card>
  )
}
