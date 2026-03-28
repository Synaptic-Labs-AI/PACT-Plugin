import { useState } from "react";
import { useMemory, useMemoryStats } from "@/hooks/useMemory";
import { useAgentMemory } from "@/hooks/useAgentMemory";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/shared/EmptyState";
import { PageSkeleton } from "@/components/shared/PageSkeleton";
import { Brain, BookOpen, Lightbulb, Users } from "lucide-react";
import { formatDateTime } from "@/lib/utils";

export function KnowledgePage() {
  const { data: memories, isLoading: memoriesLoading } = useMemory();
  const { data: stats } = useMemoryStats();
  const { data: agentMemory } = useAgentMemory();
  const [activeTab, setActiveTab] = useState("memories");

  if (memoriesLoading) return <PageSkeleton />;

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Knowledge Base</h1>
        <p className="text-xs text-muted-foreground mt-1">
          {stats ? `${stats.totalMemories} memories across ${stats.byProject.length} projects` : "Loading..."}
        </p>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="memories">Memories</TabsTrigger>
          <TabsTrigger value="decisions">Decisions</TabsTrigger>
          <TabsTrigger value="lessons">Lessons</TabsTrigger>
          <TabsTrigger value="expertise">Agent Expertise</TabsTrigger>
        </TabsList>

        <TabsContent value="memories">
          {!memories || memories.length === 0 ? (
            <EmptyState title="No memories" icon={Brain} description="pact-memory entries will appear here" />
          ) : (
            <div className="space-y-3">
              {memories.map((m) => (
                <Card key={m.id} className="py-4 gap-2">
                  <CardContent>
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-xs text-muted-foreground">{formatDateTime(m.createdAt)}</span>
                      {m.projectId && (
                        <Badge variant="secondary" className="text-[10px]">{m.projectId}</Badge>
                      )}
                    </div>
                    {m.context && (
                      <p className="text-sm text-foreground">{m.context.slice(0, 200)}{m.context.length > 200 ? "..." : ""}</p>
                    )}
                    {m.goal && (
                      <p className="text-xs text-muted-foreground mt-1">Goal: {m.goal}</p>
                    )}
                    {m.entities && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {m.entities.split(",").map((e) => (
                          <Badge key={e.trim()} variant="outline" className="text-[10px]">{e.trim()}</Badge>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="decisions">
          {!memories || memories.filter((m) => m.decisions).length === 0 ? (
            <EmptyState title="No decisions logged" icon={BookOpen} />
          ) : (
            <div className="space-y-2">
              {memories.filter((m) => m.decisions).map((m) => (
                <Card key={m.id} className="py-3 gap-1">
                  <CardContent>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs text-muted-foreground">{formatDateTime(m.createdAt)}</span>
                      {m.projectId && (
                        <Badge variant="secondary" className="text-[10px]">{m.projectId}</Badge>
                      )}
                    </div>
                    <p className="text-sm">{m.decisions}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="lessons">
          {!memories || memories.filter((m) => m.lessonsLearned).length === 0 ? (
            <EmptyState title="No lessons learned" icon={Lightbulb} />
          ) : (
            <div className="space-y-2">
              {memories.filter((m) => m.lessonsLearned).map((m) => (
                <Card key={m.id} className="py-3 gap-1">
                  <CardContent>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs text-muted-foreground">{formatDateTime(m.createdAt)}</span>
                      {m.projectId && (
                        <Badge variant="secondary" className="text-[10px]">{m.projectId}</Badge>
                      )}
                    </div>
                    <p className="text-sm">{m.lessonsLearned}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="expertise">
          {!agentMemory || agentMemory.length === 0 ? (
            <EmptyState title="No agent expertise data" icon={Users} />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {agentMemory.map((agent) => (
                <Card key={agent.specialistName} className="py-4 gap-2">
                  <CardHeader>
                    <CardTitle>{agent.specialistName.replace("pact-", "")}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="text-xs text-muted-foreground space-y-1">
                      <p>{agent.fileCount} memory files</p>
                      <p>Last modified: {formatDateTime(agent.lastModified)}</p>
                    </div>
                    {agent.memoryIndexEntries.length > 0 && (
                      <div className="mt-2 space-y-0.5">
                        {agent.memoryIndexEntries.slice(0, 5).map((entry, i) => (
                          <p key={i} className="text-xs text-muted-foreground truncate">{entry}</p>
                        ))}
                        {agent.memoryIndexEntries.length > 5 && (
                          <p className="text-xs text-muted-foreground">+{agent.memoryIndexEntries.length - 5} more</p>
                        )}
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
