import { cn } from "@/lib/utils";
import { timeAgo } from "@/lib/utils";
import { ProjectBadge } from "./ProjectBadge";
import type { ActivityEvent } from "@/lib/types";

interface ActivityRowProps {
  event: ActivityEvent;
  isNew?: boolean;
  className?: string;
}

const eventTypeLabels: Record<string, string> = {
  task_created: "created task",
  task_started: "started task",
  task_completed: "completed task",
  task_blocked: "blocked task",
  agent_joined: "joined",
  agent_message: "sent message",
  handoff_completed: "completed handoff",
  session_started: "session started",
  session_ended: "session ended",
  algedonic_signal: "algedonic signal",
};

export function ActivityRow({ event, isNew, className }: ActivityRowProps) {
  return (
    <div
      className={cn(
        "px-4 py-2 text-sm transition-colors hover:bg-accent/50",
        isNew && "activity-row-enter",
        className,
      )}
    >
      <div className="flex gap-3">
        <div className="flex-1 min-w-0 truncate">
          {event.agentColor && (
            <span
              className="inline-block w-2 h-2 rounded-full mr-2 align-middle"
              style={{ backgroundColor: event.agentColor }}
            />
          )}
          {event.agentName && (
            <span className="font-medium">{event.agentName}</span>
          )}
          <span className="text-muted-foreground ml-1">
            {eventTypeLabels[event.type] ?? event.type.replace(/_/g, " ")}
          </span>
          {event.summary && (
            <span className="text-foreground ml-1">{event.summary}</span>
          )}
          {event.projectName && (
            <ProjectBadge
              projectName={event.projectName}
              className="ml-2 align-middle"
            />
          )}
        </div>
        <span className="text-xs text-muted-foreground shrink-0 pt-0.5">
          {timeAgo(event.timestamp)}
        </span>
      </div>
    </div>
  );
}
