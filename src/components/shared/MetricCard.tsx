import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

interface MetricCardProps {
  icon: LucideIcon;
  value: string | number;
  label: string;
  description?: ReactNode;
  to?: string;
  onClick?: () => void;
  variant?: "default" | "destructive";
}

export function MetricCard({
  icon: Icon,
  value,
  label,
  description,
  to,
  onClick,
  variant = "default",
}: MetricCardProps) {
  const isClickable = !!(to || onClick);

  const inner = (
    <div
      className={cn(
        "h-full px-4 py-4 sm:px-5 sm:py-5 rounded-lg transition-colors",
        isClickable && "hover:bg-accent/50 cursor-pointer",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p
            className={cn(
              "text-2xl sm:text-3xl font-semibold tracking-tight tabular-nums",
              variant === "destructive" &&
                Number(value) > 0 &&
                "text-destructive",
            )}
          >
            {value}
          </p>
          <p className="text-xs sm:text-sm font-medium text-muted-foreground mt-1">
            {label}
          </p>
          {description && (
            <div className="text-xs text-muted-foreground/70 mt-1.5 hidden sm:block">
              {description}
            </div>
          )}
        </div>
        <Icon
          className={cn(
            "h-4 w-4 shrink-0 mt-1.5",
            variant === "destructive" && Number(value) > 0
              ? "text-destructive/50"
              : "text-muted-foreground/50",
          )}
        />
      </div>
    </div>
  );

  if (to) {
    return (
      <Link to={to} className="no-underline text-inherit h-full block">
        {inner}
      </Link>
    );
  }

  if (onClick) {
    return (
      <div className="h-full" onClick={onClick}>
        {inner}
      </div>
    );
  }

  return inner;
}
