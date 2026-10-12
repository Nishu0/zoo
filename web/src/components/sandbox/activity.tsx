import { Activity } from "lucide-react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { useExecutions } from "@/lib/api_client"
import { timeAgo } from "@/lib/utils"

export function ActivityTab({ sandboxId }: { sandboxId: string }) {
  const executions = useExecutions(sandboxId)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="size-4" />
          Activity
        </CardTitle>
        <CardDescription>
          Every tool call made through the API or MCP.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {executions.data?.length ? (
          <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {executions.data.map((e) => (
              <div key={e.id} className="flex flex-col gap-1 px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono">{e.tool_name}</span>
                  <span className="text-xs text-muted-foreground">
                    <span
                      className={
                        e.status === "failed"
                          ? "text-red-400"
                          : e.status === "completed"
                            ? "text-emerald-400"
                            : ""
                      }
                    >
                      {e.status}
                    </span>{" "}
                    · {timeAgo(e.created_at)}
                  </span>
                </div>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {e.error_message ?? e.input}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No tool calls yet.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
