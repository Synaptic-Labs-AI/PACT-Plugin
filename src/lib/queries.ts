export const queryKeys = {
  overview: ["overview"] as const,
  sessions: (filters?: Record<string, string>) =>
    ["sessions", filters] as const,
  teams: (filters?: Record<string, string>) => ["teams", filters] as const,
  teamDetail: (teamName: string) => ["teams", teamName] as const,
  tasks: (teamName: string) => ["tasks", teamName] as const,
  messages: (teamName: string, filters?: Record<string, string>) =>
    ["messages", teamName, filters] as const,
  memory: (filters?: Record<string, string>) => ["memory", filters] as const,
  memoryStats: ["memory", "stats"] as const,
  agentMemory: ["agent-memory"] as const,
  worktrees: ["worktrees"] as const,
  telegram: ["telegram"] as const,
  health: ["health"] as const,
} as const;
