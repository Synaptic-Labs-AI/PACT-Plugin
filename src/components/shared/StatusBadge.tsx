import { cn } from "@/lib/utils";
import type { TaskStatus } from "@/lib/types";

const statusColors: Record<string, string> = {
  pending: "bg-muted text-muted-foreground",
  in_progress: "bg-[var(--chart-1)]/15 text-[var(--chart-1)]",
  completed: "bg-[var(--chart-5)]/15 text-[var(--chart-5)]",
  blocked: "bg-destructive/15 text-destructive",
  deleted: "bg-muted text-muted-foreground/50",
  active: "bg-[var(--agent-active)]/15 text-[var(--agent-active)]",
  idle: "bg-muted text-muted-foreground",
  ended: "bg-muted text-muted-foreground/50",
  ok: "bg-[var(--chart-5)]/15 text-[var(--chart-5)]",
  stale: "bg-[var(--chart-1)]/15 text-[var(--chart-1)]",
  error: "bg-destructive/15 text-destructive",
  degraded: "bg-[var(--chart-1)]/15 text-[var(--chart-1)]",
};

const defaultStyle = "bg-muted text-muted-foreground";

interface StatusBadgeProps {
  status: TaskStatus | string;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap shrink-0",
        statusColors[status] ?? defaultStyle,
        className,
      )}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}
