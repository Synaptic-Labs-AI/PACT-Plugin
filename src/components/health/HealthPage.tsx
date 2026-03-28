import { useHealth } from "@/hooks/useHealth";
import { useSessions } from "@/hooks/useSessions";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { MetricCard } from "@/components/shared/MetricCard";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { PageSkeleton } from "@/components/shared/PageSkeleton";
import {
  Monitor,
  Cpu,
  Database,
  Activity,
} from "lucide-react";
import { formatDateTime, formatDuration } from "@/lib/utils";

export function HealthPage() {
  const { data: health, isLoading } = useHealth();
  const { data: sessions } = useSessions();

  if (isLoading) return <PageSkeleton />;

  if (!health) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-sm text-destructive font-medium">
          Cannot reach backend
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Make sure the server is running on port 3001
        </p>
      </div>
    );
  }

  const aliveSessions = sessions?.filter((s) => s.isAlive) ?? [];
  const okSources = health.dataSources.filter((d) => d.status === "ok").length;

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">System Health</h1>
        <div className="flex items-center gap-2 mt-1">
          <StatusBadge status={health.status} />
          <span className="text-xs text-muted-foreground">
            Uptime: {formatDuration(health.uptime * 1000)}
          </span>
        </div>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="py-0 gap-0 border">
          <MetricCard
            icon={Monitor}
            value={aliveSessions.length}
            label="Active Sessions"
          />
        </Card>
        <Card className="py-0 gap-0 border">
          <MetricCard
            icon={Cpu}
            value={aliveSessions.reduce(
              (acc, s) => acc + (s.team?.members.length ?? 0),
              0,
            )}
            label="Total PIDs"
          />
        </Card>
        <Card className="py-0 gap-0 border">
          <MetricCard
            icon={Database}
            value={`${okSources}/${health.dataSources.length}`}
            label="Data Sources OK"
          />
        </Card>
        <Card className="py-0 gap-0 border">
          <MetricCard
            icon={Activity}
            value={health.claudeDirExists ? "Yes" : "No"}
            label="~/.claude/ exists"
          />
        </Card>
      </div>

      {/* Data Freshness Table */}
      <Card>
        <CardHeader>
          <CardTitle>Data Freshness</CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left">
                  <th className="px-6 py-2 font-medium text-muted-foreground">
                    Source
                  </th>
                  <th className="px-6 py-2 font-medium text-muted-foreground">
                    Status
                  </th>
                  <th className="px-6 py-2 font-medium text-muted-foreground">
                    Last Checked
                  </th>
                  <th className="px-6 py-2 font-medium text-muted-foreground text-right">
                    Items
                  </th>
                </tr>
              </thead>
              <tbody>
                {health.dataSources.map((source) => (
                  <tr
                    key={source.name}
                    className="border-b border-border last:border-0 hover:bg-accent/50 transition-colors"
                  >
                    <td className="px-6 py-2.5 font-medium">
                      {source.name}
                    </td>
                    <td className="px-6 py-2.5">
                      <StatusBadge status={source.status} />
                    </td>
                    <td className="px-6 py-2.5 text-muted-foreground">
                      {formatDateTime(source.lastChecked)}
                    </td>
                    <td className="px-6 py-2.5 text-right tabular-nums">
                      {source.itemCount}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Session Liveness */}
      {sessions && sessions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Session Liveness</CardTitle>
          </CardHeader>
          <CardContent className="px-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left">
                    <th className="px-6 py-2 font-medium text-muted-foreground">
                      PID
                    </th>
                    <th className="px-6 py-2 font-medium text-muted-foreground">
                      Project
                    </th>
                    <th className="px-6 py-2 font-medium text-muted-foreground">
                      Team
                    </th>
                    <th className="px-6 py-2 font-medium text-muted-foreground">
                      Status
                    </th>
                    <th className="px-6 py-2 font-medium text-muted-foreground">
                      Uptime
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr
                      key={s.sessionId}
                      className="border-b border-border last:border-0 hover:bg-accent/50 transition-colors"
                    >
                      <td className="px-6 py-2.5 font-mono text-xs">
                        {s.pid}
                      </td>
                      <td className="px-6 py-2.5 font-medium">
                        {s.projectName}
                      </td>
                      <td className="px-6 py-2.5 text-muted-foreground">
                        {s.team?.name ?? "-"}
                      </td>
                      <td className="px-6 py-2.5">
                        <StatusBadge
                          status={s.isAlive ? "active" : "ended"}
                        />
                      </td>
                      <td className="px-6 py-2.5 text-muted-foreground tabular-nums">
                        {formatDuration(Date.now() - s.startedAt)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
