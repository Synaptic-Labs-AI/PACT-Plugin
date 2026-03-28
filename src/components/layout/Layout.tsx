import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { BreadcrumbBar } from "./BreadcrumbBar";

export function Layout() {
  return (
    <div className="flex h-dvh bg-background text-foreground overflow-hidden">
      <Sidebar />
      <div className="flex min-w-0 flex-col flex-1 h-full">
        <BreadcrumbBar />
        <main
          id="main-content"
          tabIndex={-1}
          className="flex-1 overflow-auto p-4 md:p-6"
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}
