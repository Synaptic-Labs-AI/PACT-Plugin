import { useOverview } from "@/hooks/useOverview";
import { MetricCardsRow } from "./MetricCardsRow";
import { ActiveAgentsPanel } from "./ActiveAgentsPanel";
import { ActivityFeed } from "./ActivityFeed";
import { SessionList } from "./SessionList";
import { PageSkeleton } from "@/components/shared/PageSkeleton";
import type { DashboardAgent, DashboardSession } from "@/lib/types";

export function DashboardPage() {
  const { data: overview, isLoading, error } = useOverview();

  if (isLoading) return <PageSkeleton />;

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-sm text-destructive font-medium">
          Failed to load dashboard
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          {error instanceof Error ? error.message : "Unknown error"}
        </p>
        <p className="text-xs text-muted-foreground mt-3">
          Make sure the backend server is running on port 3001
        </p>
      </div>
    );
  }

  if (!overview) return null;

  // Collect all agents across all projects
  const allAgents: DashboardAgent[] = overview.projects.flatMap((p) =>
    p.activeSessions.flatMap((s) => s.team?.members ?? []),
  );

  // Collect all sessions
  const allSessions: DashboardSession[] = overview.projects.flatMap((p) => [
    ...p.activeSessions,
    ...p.endedSessions,
  ]);

  return (
    <div className="space-y-6 max-w-7xl">
      <MetricCardsRow overview={overview} />

      <ActiveAgentsPanel agents={allAgents} />

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
        <ActivityFeed events={overview.recentActivity} />
        <SessionList sessions={allSessions} />
      </div>
    </div>
  );
}
