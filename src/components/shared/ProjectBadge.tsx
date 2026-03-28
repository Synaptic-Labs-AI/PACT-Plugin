import { cn } from "@/lib/utils";

interface ProjectBadgeProps {
  projectName: string;
  className?: string;
}

export function ProjectBadge({ projectName, className }: ProjectBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-accent text-accent-foreground whitespace-nowrap shrink-0 uppercase tracking-wide",
        className,
      )}
    >
      {projectName}
    </span>
  );
}
