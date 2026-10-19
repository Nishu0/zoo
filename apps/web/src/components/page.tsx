import type { LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export function Page({
  icon: Icon,
  title,
  description,
  action,
  className,
  children,
}: {
  icon?: LucideIcon
  title: React.ReactNode
  description?: React.ReactNode
  action?: React.ReactNode
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-6 py-6">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            {Icon && <Icon className="size-5 shrink-0" />}
            {title}
          </h1>
          {description && (
            <div className="text-sm text-muted-foreground">{description}</div>
          )}
        </div>
        {action && <div className="flex items-center gap-2">{action}</div>}
      </div>
      <div className={cn("flex flex-1 flex-col gap-6 p-6", className)}>
        {children}
      </div>
    </div>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
      <Icon className="size-8 text-muted-foreground" />
      <div className="flex flex-col gap-1">
        <p className="font-medium text-muted-foreground">{title}</p>
        {description && (
          <p className="text-sm text-muted-foreground/70">{description}</p>
        )}
      </div>
      {action}
    </div>
  )
}
