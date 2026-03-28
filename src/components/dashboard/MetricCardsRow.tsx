import {
  Monitor,
  Users,
  ListTodo,
  AlertTriangle,
  Brain,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { MetricCard } from "@/components/shared/MetricCard";
import type { DashboardOverview } from "@/lib/types";

interface MetricCardsRowProps {
  overview: DashboardOverview;
}

export function MetricCardsRow({ overview }: MetricCardsRowProps) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
      <Card className="py-0 gap-0 border">
        <MetricCard
          icon={Monitor}
          value={overview.activeSessionCount}
          label="Active Sessions"
        />
      </Card>
      <Card className="py-0 gap-0 border">
        <MetricCard
          icon={Users}
          value={overview.totalAgentCount}
          label="Total Agents"
        />
      </Card>
      <Card className="py-0 gap-0 border">
        <MetricCard
          icon={ListTodo}
          value={overview.inProgressTaskCount}
          label="In-Progress Tasks"
        />
      </Card>
      <Card className="py-0 gap-0 border">
        <MetricCard
          icon={AlertTriangle}
          value={overview.blockerCount}
          label="Blockers"
          variant={overview.blockerCount > 0 ? "destructive" : "default"}
        />
      </Card>
      <Card className="py-0 gap-0 border hidden xl:block">
        <MetricCard
          icon={Brain}
          value={overview.memoryCount}
          label="Memories"
          to="/knowledge"
        />
      </Card>
    </div>
  );
}
