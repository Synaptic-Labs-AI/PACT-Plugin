import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { StatusDot } from "@/components/shared/StatusDot";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/shared/EmptyState";
import { Users } from "lucide-react";
import type { DashboardAgent } from "@/lib/types";

interface ActiveAgentsPanelProps {
  agents: DashboardAgent[];
}

export function ActiveAgentsPanel({ agents }: ActiveAgentsPanelProps) {
  if (agents.length === 0) {
    return (
      <Card>
        <EmptyState
          title="No active agents"
          description="Agents will appear here when PACT sessions are running"
          icon={Users}
        />
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Active Agents ({agents.length})</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {agents.map((agent) => (
            <div
              key={agent.agentId}
              className="flex items-start gap-3 px-3 py-3 rounded-lg border border-border hover:bg-accent/50 transition-colors"
            >
              <StatusDot
                status={agent.currentTask ? "active" : "idle"}
                className="mt-1"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium truncate">
                    {agent.name}
                  </span>
                  {agent.color && (
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: agent.color }}
                    />
                  )}
                </div>
                <Badge variant="secondary" className="mt-1 text-[10px]">
                  {agent.agentType.replace("pact-", "")}
                </Badge>
                {agent.currentTask && (
                  <p className="text-xs text-muted-foreground mt-1.5 truncate">
                    {agent.currentTask.activeForm ?? agent.currentTask.subject}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
