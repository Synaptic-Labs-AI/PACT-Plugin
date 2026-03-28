import { TaskStatusIcon } from "@/components/shared/TaskStatusIcon";
import { AgentBadge } from "@/components/shared/AgentBadge";
import { cn } from "@/lib/utils";
import type { DashboardTask, DashboardAgent } from "@/lib/types";

interface TaskRowProps {
  task: DashboardTask;
  agents: DashboardAgent[];
}

export function TaskRow({ task, agents }: TaskRowProps) {
  const ownerAgent = task.owner
    ? agents.find((a) => a.name === task.owner)
    : null;

  return (
    <div
      className={cn(
        "flex items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-accent/50",
        task.status === "completed" && "opacity-60",
      )}
    >
      <TaskStatusIcon
        status={task.status}
        isBlocked={task.isBlocked}
      />
      <span className="text-xs text-muted-foreground tabular-nums w-6 shrink-0">
        #{task.id}
      </span>
      <span className="flex-1 min-w-0 truncate">{task.subject}</span>
      {task.activeForm && task.status === "in_progress" && (
        <span className="text-xs text-muted-foreground truncate max-w-[200px] hidden md:block">
          {task.activeForm}
        </span>
      )}
      {task.owner && (
        <AgentBadge
          name={task.owner}
          color={ownerAgent?.color}
        />
      )}
      {task.isBlocked && task.blockedBy.length > 0 && (
        <span className="text-[10px] text-destructive font-medium shrink-0">
          blocked by #{task.blockedBy.join(", #")}
        </span>
      )}
    </div>
  );
}
