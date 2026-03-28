import { Card, CardContent } from "@/components/ui/card";
import { AgentBadge } from "@/components/shared/AgentBadge";
import { EmptyState } from "@/components/shared/EmptyState";
import { FileCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DashboardTask, DashboardAgent } from "@/lib/types";

interface HandoffsTabProps {
  tasks: DashboardTask[];
  agents: DashboardAgent[];
}

const uncertaintyColors: Record<string, string> = {
  HIGH: "text-destructive",
  MEDIUM: "text-[var(--chart-1)]",
  LOW: "text-muted-foreground",
};

export function HandoffsTab({ tasks, agents }: HandoffsTabProps) {
  const tasksWithHandoffs = tasks.filter((t) => t.metadata.handoff);

  if (tasksWithHandoffs.length === 0) {
    return (
      <EmptyState
        title="No handoffs yet"
        description="Handoffs will appear as agents complete their work"
        icon={FileCheck}
      />
    );
  }

  return (
    <div className="space-y-3">
      {tasksWithHandoffs.map((task) => {
        const handoff = task.metadata.handoff!;
        const agent = agents.find((a) => a.name === task.owner);

        return (
          <Card key={task.id} className="py-4 gap-3">
            <CardContent>
              <div className="flex items-center gap-2 mb-3">
                {task.owner && (
                  <AgentBadge name={task.owner} color={agent?.color} />
                )}
                <span className="text-sm font-medium truncate">
                  {task.subject}
                </span>
              </div>

              {handoff.produced.length > 0 && (
                <div className="mb-3">
                  <h4 className="text-xs font-semibold text-muted-foreground mb-1">
                    Produced
                  </h4>
                  <div className="flex flex-wrap gap-1">
                    {handoff.produced.map((file) => (
                      <span
                        key={file}
                        className="text-xs px-2 py-0.5 bg-accent rounded-md font-mono truncate max-w-[200px]"
                      >
                        {file}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {handoff.decisions.length > 0 && (
                <div className="mb-3">
                  <h4 className="text-xs font-semibold text-muted-foreground mb-1">
                    Key Decisions
                  </h4>
                  <ul className="text-xs space-y-1 text-foreground">
                    {handoff.decisions.map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                </div>
              )}

              {handoff.uncertainty.length > 0 && (
                <div className="mb-3">
                  <h4 className="text-xs font-semibold text-muted-foreground mb-1">
                    Uncertainty
                  </h4>
                  <ul className="text-xs space-y-1">
                    {handoff.uncertainty.map((u, i) => {
                      const match = u.match(/^\[(HIGH|MEDIUM|LOW)\]/);
                      const level = match?.[1] ?? "LOW";
                      return (
                        <li
                          key={i}
                          className={cn(
                            uncertaintyColors[level],
                          )}
                        >
                          {u}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {handoff.open_questions.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-muted-foreground mb-1">
                    Open Questions
                  </h4>
                  <ul className="text-xs space-y-1 text-muted-foreground">
                    {handoff.open_questions.map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
