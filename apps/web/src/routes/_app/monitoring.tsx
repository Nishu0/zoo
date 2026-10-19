import { createFileRoute, Link } from "@tanstack/react-router"
import { Activity, Box, Cpu, HardDrive, MemoryStick } from "lucide-react"
import { useEffect, useState } from "react"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"

import { EmptyState, Page } from "@/components/page"
import { StatCard } from "@/components/stat-card"
import { StatusBadge } from "@/components/status-badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import type { ChartConfig } from "@/components/ui/chart"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useMonitoring } from "@/lib/api_client"
import { formatBytes } from "@/lib/utils"

export const Route = createFileRoute("/_app/monitoring")({
  component: MonitoringPage,
})

const chartConfig = {
  cpu: { label: "CPU %", color: "var(--chart-2)" },
  memory: { label: "Memory %", color: "var(--chart-4)" },
} satisfies ChartConfig

type Point = { time: string; cpu: number; memory: number }

function MonitoringPage() {
  const monitoring = useMonitoring()
  const [history, setHistory] = useState<Array<Point>>([])

  useEffect(() => {
    const data = monitoring.data
    if (!data) return
    setHistory((prev) =>
      [
        ...prev,
        {
          time: new Date(data.collected_at).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }),
          cpu: Math.round((data.cpu_percent / data.host.cpus) * 10) / 10,
          memory:
            Math.round((data.memory_usage / data.host.memory_total) * 1000) /
            10,
        },
      ].slice(-60)
    )
  }, [monitoring.data])

  return (
    <Page
      icon={Activity}
      title="Monitoring"
      description="Live resource usage of your sandboxes and the host running them."
    >
      {monitoring.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-28 rounded-xl" />
          ))}
        </div>
      ) : monitoring.error ? (
        <EmptyState
          icon={Activity}
          title="Monitoring unavailable"
          description={monitoring.error.message}
        />
      ) : (
        <MonitoringContent data={monitoring.data} history={history} />
      )}
    </Page>
  )
}

function MonitoringContent({
  data,
  history,
}: {
  data: NonNullable<ReturnType<typeof useMonitoring>["data"]>
  history: Array<Point>
}) {
  const running = data.sandboxes.running ?? 0

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Sandboxes running"
          value={`${running} / ${data.sandboxes.total ?? 0}`}
          icon={Box}
          hint={
            data.sandboxes.failed
              ? `${data.sandboxes.failed} failed`
              : "all healthy"
          }
        />
        <StatCard
          label="Sandbox CPU"
          value={`${data.cpu_percent.toFixed(1)}%`}
          icon={Cpu}
          hint={`of ${data.host.cpus} host cores`}
        />
        <StatCard
          label="Sandbox memory"
          value={formatBytes(data.memory_usage)}
          icon={MemoryStick}
          hint={`of ${formatBytes(data.host.memory_total)} host memory`}
        />
        <StatCard
          label="Host containers"
          value={String(data.host.containers_running)}
          icon={HardDrive}
          hint={`${data.host.images} images · Docker ${data.host.docker_version}`}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Resource usage</CardTitle>
          <CardDescription>
            Share of host CPU and memory used by your sandboxes, sampled every 5
            seconds while this page is open.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ChartContainer config={chartConfig} className="h-64 w-full">
            <AreaChart data={history} margin={{ left: -20, right: 12 }}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis
                dataKey="time"
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                fontSize={12}
                minTickGap={40}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                fontSize={12}
                domain={[0, "auto"]}
                unit="%"
              />
              <ChartTooltip
                content={<ChartTooltipContent indicator="line" />}
              />
              <Area
                dataKey="cpu"
                type="monotone"
                fill="var(--color-cpu)"
                fillOpacity={0.15}
                stroke="var(--color-cpu)"
                strokeWidth={2}
                isAnimationActive={false}
              />
              <Area
                dataKey="memory"
                type="monotone"
                fill="var(--color-memory)"
                fillOpacity={0.15}
                stroke="var(--color-memory)"
                strokeWidth={2}
                isAnimationActive={false}
              />
            </AreaChart>
          </ChartContainer>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sandboxes</CardTitle>
          <CardDescription>Per-sandbox container stats.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-6">Name</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>Memory</TableHead>
                <TableHead>Network in / out</TableHead>
                <TableHead className="pr-6">Processes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.usage.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="py-6 text-center text-sm text-muted-foreground"
                  >
                    No running sandboxes.
                  </TableCell>
                </TableRow>
              )}
              {data.usage.map((u) => (
                <TableRow key={u.sandbox_id}>
                  <TableCell className="pl-6">
                    <Link
                      to="/sandboxes/$sandboxId"
                      params={{ sandboxId: u.sandbox_id }}
                      className="font-medium hover:underline"
                    >
                      {u.name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={u.status} />
                  </TableCell>
                  <TableCell>{u.cpu_percent.toFixed(1)}%</TableCell>
                  <TableCell>
                    {formatBytes(u.memory_usage)}
                    <span className="text-muted-foreground">
                      {" "}
                      / {formatBytes(u.memory_limit)}
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatBytes(u.network_rx)} / {formatBytes(u.network_tx)}
                  </TableCell>
                  <TableCell className="pr-6">{u.pids}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  )
}
