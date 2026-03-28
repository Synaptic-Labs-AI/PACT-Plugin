import type { ReactNode } from "react";

interface SidebarSectionProps {
  label: string;
  children: ReactNode;
}

export function SidebarSection({ label, children }: SidebarSectionProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <h3 className="px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/60">
        {label}
      </h3>
      {children}
    </div>
  );
}
