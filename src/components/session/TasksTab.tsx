import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { TaskRow } from "./TaskRow";
import { EmptyState } from "@/components/shared/EmptyState";
import { ListTodo } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DashboardTask, DashboardAgent, TaskStatus } from "@/lib/types";

interface TasksTabProps {
  tasks: DashboardTask[];
  agents: DashboardAgent[];
}

const filters: Array<{ label: string; value: TaskStatus | "all" }> = [
  { label: "All", value: "all" },
  { label: "Pending", value: "pending" },
  { label: "In Progress", value: "in_progress" },
  { label: "Completed", value: "completed" },
];

export function TasksTab({ tasks, agents }: TasksTabProps) {
  const [filter, setFilter] = useState<TaskStatus | "all">("all");

  const filtered =
    filter === "all"
      ? tasks.filter((t) => t.status !== "deleted")
      : tasks.filter((t) => t.status === filter);

  return (
    <div className="space-y-3">
      {/* Status filter */}
      <div className="flex items-center gap-1">
        {filters.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={cn(
              "px-3 py-1 text-xs font-medium rounded-md transition-colors",
              filter === f.value
                ? "bg-accent text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      <Card>
        <CardContent className="px-0 py-0">
          {filtered.length === 0 ? (
            <EmptyState title="No tasks" icon={ListTodo} />
          ) : (
            <div className="divide-y divide-border">
              {filtered.map((task) => (
                <TaskRow key={task.id} task={task} agents={agents} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
