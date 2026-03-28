import { Card, CardContent } from "@/components/ui/card";
import { StatusDot } from "@/components/shared/StatusDot";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/shared/EmptyState";
import { Users } from "lucide-react";
import { timeAgo } from "@/lib/utils";
import type { DashboardAgent } from "@/lib/types";

interface AgentsTabProps {
  agents: DashboardAgent[];
}

export function AgentsTab({ agents }: AgentsTabProps) {
  if (agents.length === 0) {
    return <EmptyState title="No agents" icon={Users} />;
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {agents.map((agent) => (
        <Card key={agent.agentId} className="py-4 gap-3">
          <CardContent>
            <div className="flex items-start gap-3">
              <StatusDot
                status={agent.currentTask ? "active" : "idle"}
                className="mt-1"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{agent.name}</span>
                  {agent.color && (
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: agent.color }}
                    />
                  )}
                </div>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  <Badge variant="secondary" className="text-[10px]">
                    {agent.agentType.replace("pact-", "")}
                  </Badge>
                  <Badge variant="outline" className="text-[10px]">
                    {agent.model}
                  </Badge>
                </div>
                <div className="flex items-center gap-3 mt-2 text-xs text-muted-foreground">
                  <span>
                    Joined {timeAgo(new Date(agent.joinedAt).toISOString())}
                  </span>
                  <span>{agent.completedTaskCount} tasks done</span>
                </div>
                {agent.currentTask && (
                  <p className="text-xs text-muted-foreground mt-1.5 truncate">
                    Working on: {agent.currentTask.subject}
                  </p>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
