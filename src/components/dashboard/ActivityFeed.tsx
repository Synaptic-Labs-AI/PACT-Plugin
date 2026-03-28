import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ActivityRow } from "@/components/shared/ActivityRow";
import { EmptyState } from "@/components/shared/EmptyState";
import { Activity } from "lucide-react";
import type { ActivityEvent } from "@/lib/types";

interface ActivityFeedProps {
  events: ActivityEvent[];
  limit?: number;
}

export function ActivityFeed({ events, limit = 50 }: ActivityFeedProps) {
  const displayEvents = events.slice(0, limit);

  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle>Recent Activity</CardTitle>
      </CardHeader>
      <CardContent className="flex-1 min-h-0 px-0">
        {displayEvents.length === 0 ? (
          <EmptyState
            title="No recent activity"
            description="Activity from PACT sessions will appear here"
            icon={Activity}
          />
        ) : (
          <ScrollArea className="max-h-[400px]">
            <div className="divide-y divide-border">
              {displayEvents.map((event, i) => (
                <ActivityRow
                  key={event.id}
                  event={event}
                  isNew={i === 0}
                />
              ))}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}
