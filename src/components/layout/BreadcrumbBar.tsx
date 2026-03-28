import { useLocation, Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

function getBreadcrumbs(pathname: string) {
  const segments = pathname.split("/").filter(Boolean);
  const crumbs: Array<{ label: string; path: string }> = [
    { label: "Dashboard", path: "/" },
  ];

  if (segments[0] === "sessions" && segments[1]) {
    crumbs.push({ label: segments[1], path: `/sessions/${segments[1]}` });
  } else if (segments[0] === "knowledge") {
    crumbs.push({ label: "Knowledge", path: "/knowledge" });
  } else if (segments[0] === "health") {
    crumbs.push({ label: "System Health", path: "/health" });
  }

  return crumbs;
}

export function BreadcrumbBar() {
  const { pathname } = useLocation();
  const crumbs = getBreadcrumbs(pathname);

  if (crumbs.length <= 1) return null;

  return (
    <nav className="flex items-center gap-1 px-4 md:px-6 h-10 text-sm border-b border-border">
      {crumbs.map((crumb, i) => (
        <span key={crumb.path} className="flex items-center gap-1">
          {i > 0 && (
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />
          )}
          {i < crumbs.length - 1 ? (
            <Link
              to={crumb.path}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              {crumb.label}
            </Link>
          ) : (
            <span className="text-foreground font-medium">{crumb.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
