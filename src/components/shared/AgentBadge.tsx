import { cn } from "@/lib/utils";

interface AgentBadgeProps {
  name: string;
  color?: string | null;
  type?: string;
  className?: string;
}

export function AgentBadge({ name, color, type, className }: AgentBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium bg-secondary text-secondary-foreground whitespace-nowrap shrink-0",
        className,
      )}
    >
      {color && (
        <span
          className="w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: color }}
        />
      )}
      <span className="truncate max-w-[120px]">{name}</span>
      {type && (
        <span className="text-muted-foreground text-[10px]">{type}</span>
      )}
    </span>
  );
}
