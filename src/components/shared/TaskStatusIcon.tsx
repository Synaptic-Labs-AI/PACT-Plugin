import {
  Circle,
  Loader2,
  CheckCircle2,
  Ban,
  AlertTriangle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { TaskStatus } from "@/lib/types";

interface TaskStatusIconProps {
  status: TaskStatus;
  isBlocked?: boolean;
  className?: string;
}

export function TaskStatusIcon({
  status,
  isBlocked,
  className,
}: TaskStatusIconProps) {
  if (isBlocked) {
    return (
      <AlertTriangle
        className={cn("h-4 w-4 text-destructive", className)}
        aria-label="Blocked"
      />
    );
  }

  switch (status) {
    case "pending":
      return (
        <Circle
          className={cn("h-4 w-4 text-muted-foreground", className)}
          aria-label="Pending"
        />
      );
    case "in_progress":
      return (
        <Loader2
          className={cn("h-4 w-4 text-[var(--chart-1)] animate-spin", className)}
          aria-label="In progress"
        />
      );
    case "completed":
      return (
        <CheckCircle2
          className={cn("h-4 w-4 text-[var(--chart-5)]", className)}
          aria-label="Completed"
        />
      );
    case "deleted":
      return (
        <Ban
          className={cn("h-4 w-4 text-muted-foreground/50", className)}
          aria-label="Deleted"
        />
      );
  }
}
