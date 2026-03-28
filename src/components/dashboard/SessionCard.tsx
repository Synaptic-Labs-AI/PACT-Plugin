import { Link } from "react-router-dom";
import { StatusDot } from "@/components/shared/StatusDot";
import { cn } from "@/lib/utils";
import { formatDuration } from "@/lib/utils";
import type { DashboardSession } from "@/lib/types";

interface SessionCardProps {
  session: DashboardSession;
}

export function SessionCard({ session }: SessionCardProps) {
  const teamName = session.team?.name;
  const uptime = Date.now() - session.startedAt;

  return (
    <Link
      to={teamName ? `/sessions/${teamName}` : "#"}
      className={cn(
        "block px-4 py-3 transition-colors hover:bg-accent/50 no-underline text-inherit",
        !session.isAlive && "opacity-60",
      )}
    >
      <div className="flex items-start gap-2">
        <StatusDot
          status={session.isAlive ? "active" : "ended"}
          className="mt-1.5"
        />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">
            {session.projectName}
          </p>
          <div className="flex items-center gap-2 mt-0.5">
            {teamName && (
              <span className="text-xs text-muted-foreground truncate">
                {teamName}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-1.5 text-xs text-muted-foreground">
            {session.team && (
              <span>{session.team.members.length} agents</span>
            )}
            <span>{formatDuration(uptime)}</span>
          </div>
        </div>
      </div>
    </Link>
  );
}
