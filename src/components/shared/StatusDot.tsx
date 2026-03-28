import { cn } from "@/lib/utils";

interface StatusDotProps {
  status: "active" | "idle" | "ended";
  className?: string;
}

const statusStyles: Record<StatusDotProps["status"], string> = {
  active: "bg-[var(--agent-active)] animate-pulse-dot",
  idle: "bg-[var(--agent-idle)]",
  ended: "bg-muted-foreground/40",
};

export function StatusDot({ status, className }: StatusDotProps) {
  return (
    <span
      className={cn("inline-block w-2 h-2 rounded-full shrink-0", statusStyles[status], className)}
      aria-label={`Status: ${status}`}
    />
  );
}
