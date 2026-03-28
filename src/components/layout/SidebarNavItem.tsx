import { NavLink } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface SidebarNavItemProps {
  to: string;
  label: string;
  icon: LucideIcon;
  badge?: number;
}

export function SidebarNavItem({
  to,
  label,
  icon: Icon,
  badge,
}: SidebarNavItemProps) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 px-3 py-2 text-[13px] font-medium transition-colors rounded-md",
          isActive
            ? "bg-accent text-foreground"
            : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
        )
      }
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="flex-1 truncate">{label}</span>
      {badge !== undefined && badge > 0 && (
        <span className="text-[10px] font-semibold bg-destructive text-white rounded-full px-1.5 py-0.5 min-w-[18px] text-center">
          {badge}
        </span>
      )}
    </NavLink>
  );
}
