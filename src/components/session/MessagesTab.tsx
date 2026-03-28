import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AgentBadge } from "@/components/shared/AgentBadge";
import { EmptyState } from "@/components/shared/EmptyState";
import { MessageSquare } from "lucide-react";
import { timeAgo } from "@/lib/utils";
import type { DashboardMessage, DashboardAgent } from "@/lib/types";

interface MessagesTabProps {
  messages: DashboardMessage[];
  agents: DashboardAgent[];
}

export function MessagesTab({ messages, agents }: MessagesTabProps) {
  if (messages.length === 0) {
    return <EmptyState title="No messages" icon={MessageSquare} />;
  }

  const agentMap = new Map(agents.map((a) => [a.name, a]));

  return (
    <Card>
      <CardContent className="px-0 py-0">
        <ScrollArea className="max-h-[500px]">
          <div className="divide-y divide-border">
            {messages.map((msg) => {
              const sender = agentMap.get(msg.from);
              return (
                <div
                  key={msg.id}
                  className="px-4 py-3 text-sm hover:bg-accent/50 transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <AgentBadge
                      name={msg.from}
                      color={sender?.color ?? msg.color}
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-muted-foreground">
                        to {msg.toAgent}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap break-words text-foreground">
                        {msg.parsedContent.slice(0, 300)}
                        {msg.parsedContent.length > 300 ? "..." : ""}
                      </p>
                    </div>
                    <span className="text-xs text-muted-foreground shrink-0">
                      {timeAgo(msg.timestamp)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
