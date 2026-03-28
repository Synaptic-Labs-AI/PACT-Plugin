import {
  LayoutDashboard,
  Brain,
  Activity,
  Sun,
  Moon,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { SidebarNavItem } from "./SidebarNavItem";
import { SidebarSection } from "./SidebarSection";
import { StatusDot } from "@/components/shared/StatusDot";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { usePreferences } from "@/hooks/usePreferences";
import { useOverview } from "@/hooks/useOverview";
import { cn } from "@/lib/utils";

export function Sidebar() {
  const { prefs, toggleTheme } = usePreferences();
  const { data: overview } = useOverview();

  const sessions = overview?.projects.flatMap((p) => p.activeSessions) ?? [];
  const projects = overview?.projects ?? [];

  return (
    <aside className="w-60 h-full min-h-0 border-r border-border bg-sidebar flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 h-12 shrink-0 border-b border-border">
        <Activity className="h-4 w-4 text-sidebar-primary shrink-0" />
        <span className="flex-1 text-sm font-bold text-sidebar-foreground truncate">
          PACT Dashboard
        </span>
      </div>

      {/* Navigation */}
      <ScrollArea className="flex-1 min-h-0">
        <nav className="flex flex-col gap-4 px-3 py-3">
          <div className="flex flex-col gap-0.5">
            <SidebarNavItem
              to="/"
              label="Dashboard"
              icon={LayoutDashboard}
            />
            <SidebarNavItem
              to="/knowledge"
              label="Knowledge"
              icon={Brain}
            />
            <SidebarNavItem
              to="/health"
              label="System Health"
              icon={Activity}
              badge={
                overview
                  ? overview.blockerCount
                  : undefined
              }
            />
          </div>

          {projects.length > 0 && (
            <SidebarSection label="Projects">
              {projects.map((project) => (
                <NavLink
                  key={project.projectPath}
                  to={`/sessions/${project.activeSessions[0]?.team?.name ?? ""}`}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-2 px-3 py-1.5 text-[13px] transition-colors rounded-md",
                      isActive
                        ? "bg-accent text-foreground"
                        : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                    )
                  }
                >
                  <span className="flex-1 truncate font-medium">
                    {project.projectName}
                  </span>
                  <span className="text-[10px] tabular-nums text-muted-foreground">
                    {project.totalAgents}
                  </span>
                </NavLink>
              ))}
            </SidebarSection>
          )}

          {sessions.length > 0 && (
            <SidebarSection label="Active Sessions">
              {sessions.map((session) => (
                <NavLink
                  key={session.sessionId}
                  to={`/sessions/${session.team?.name ?? ""}`}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-2 px-3 py-1.5 text-[13px] transition-colors rounded-md",
                      isActive
                        ? "bg-accent text-foreground"
                        : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                    )
                  }
                >
                  <StatusDot
                    status={session.isAlive ? "active" : "ended"}
                  />
                  <span className="flex-1 truncate">
                    {session.team?.name ?? session.sessionId.slice(0, 8)}
                  </span>
                </NavLink>
              ))}
            </SidebarSection>
          )}
        </nav>
      </ScrollArea>

      {/* Footer */}
      <div className="border-t border-border px-3 py-2">
        <div className="flex items-center gap-1">
          <span className="flex-1 text-xs text-muted-foreground px-2">
            {overview
              ? `${overview.activeSessionCount} session${overview.activeSessionCount !== 1 ? "s" : ""}`
              : "Loading..."}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="text-muted-foreground shrink-0"
            onClick={toggleTheme}
            aria-label={`Switch to ${prefs.theme === "dark" ? "light" : "dark"} mode`}
          >
            {prefs.theme === "dark" ? (
              <Sun className="h-4 w-4" />
            ) : (
              <Moon className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>
    </aside>
  );
}
