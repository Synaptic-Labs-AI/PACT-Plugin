import { StatusDot } from "@/components/shared/StatusDot";
import { Badge } from "@/components/ui/badge";
import { formatDuration } from "@/lib/utils";
import type { DashboardTeam, DashboardSession } from "@/lib/types";

interface SessionHeaderProps {
  team: DashboardTeam;
  session?: DashboardSession;
}

export function SessionHeader({ team, session }: SessionHeaderProps) {
  const uptime = session
    ? Date.now() - session.startedAt
    : Date.now() - team.createdAt;

  return (
    <div className="flex items-start gap-3 pb-4">
      <StatusDot
        status={session?.isAlive ? "active" : "ended"}
        className="mt-2"
      />
      <div className="flex-1 min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">
          {session?.projectName ?? team.name}
        </h1>
        <div className="flex items-center gap-2 mt-1 flex-wrap">
          <Badge variant="secondary" className="text-xs">
            {team.name}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {team.members.length} agent{team.members.length !== 1 ? "s" : ""}
          </span>
          <span className="text-xs text-muted-foreground">
            {formatDuration(uptime)}
          </span>
          {session && (
            <span className="text-xs text-muted-foreground">
              PID {session.pid}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
