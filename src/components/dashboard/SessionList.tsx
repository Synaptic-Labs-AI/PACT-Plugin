import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { SessionCard } from "./SessionCard";
import { EmptyState } from "@/components/shared/EmptyState";
import { Monitor } from "lucide-react";
import type { DashboardSession } from "@/lib/types";

interface SessionListProps {
  sessions: DashboardSession[];
}

export function SessionList({ sessions }: SessionListProps) {
  const alive = sessions.filter((s) => s.isAlive);
  const ended = sessions.filter((s) => !s.isAlive);

  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle>Sessions ({sessions.length})</CardTitle>
      </CardHeader>
      <CardContent className="flex-1 min-h-0 px-0">
        {sessions.length === 0 ? (
          <EmptyState
            title="No sessions"
            description="Start a PACT session to see it here"
            icon={Monitor}
          />
        ) : (
          <ScrollArea className="max-h-[400px]">
            <div className="divide-y divide-border">
              {alive.map((s) => (
                <SessionCard key={s.sessionId} session={s} />
              ))}
              {ended.map((s) => (
                <SessionCard key={s.sessionId} session={s} />
              ))}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}
