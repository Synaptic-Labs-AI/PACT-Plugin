import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTeamDetail } from "@/hooks/useTeamDetail";
import { useSessions } from "@/hooks/useSessions";
import { SessionHeader } from "./SessionHeader";
import { TasksTab } from "./TasksTab";
import { AgentsTab } from "./AgentsTab";
import { MessagesTab } from "./MessagesTab";
import { HandoffsTab } from "./HandoffsTab";
import { PageSkeleton } from "@/components/shared/PageSkeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

export function SessionDetailPage() {
  const { teamName } = useParams<{ teamName: string }>();
  const { data, isLoading, error } = useTeamDetail(teamName ?? "");
  const { data: sessions } = useSessions();
  const [activeTab, setActiveTab] = useState("tasks");

  if (!teamName) {
    return <p className="text-sm text-muted-foreground">No team selected</p>;
  }

  if (isLoading) return <PageSkeleton />;

  if (error || !data) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-sm text-destructive font-medium">
          Failed to load session
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          {error instanceof Error ? error.message : "Team not found"}
        </p>
      </div>
    );
  }

  const { team, tasks, messages } = data;
  const session = sessions?.find(
    (s) => s.team?.name === teamName,
  );

  return (
    <div className="space-y-6 max-w-5xl">
      <SessionHeader team={team} session={session} />

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="tasks">
            Tasks ({tasks.filter((t) => t.status !== "deleted").length})
          </TabsTrigger>
          <TabsTrigger value="agents">
            Agents ({team.members.length})
          </TabsTrigger>
          <TabsTrigger value="messages">
            Messages ({messages.length})
          </TabsTrigger>
          <TabsTrigger value="handoffs">Handoffs</TabsTrigger>
        </TabsList>

        <TabsContent value="tasks">
          <TasksTab tasks={tasks} agents={team.members} />
        </TabsContent>

        <TabsContent value="agents">
          <AgentsTab agents={team.members} />
        </TabsContent>

        <TabsContent value="messages">
          <MessagesTab messages={messages} agents={team.members} />
        </TabsContent>

        <TabsContent value="handoffs">
          <HandoffsTab tasks={tasks} agents={team.members} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
