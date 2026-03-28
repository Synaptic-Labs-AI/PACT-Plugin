import type {
  DashboardOverview,
  DashboardSession,
  DashboardTeam,
  TeamDetailResponse,
  DashboardTask,
  DashboardMessage,
  DashboardMemory,
  MemoryStatsResponse,
  AgentMemorySummary,
  DashboardWorktree,
  TelegramSession,
  HealthResponse,
} from "./types";

const BASE = "/api";

class ApiClientError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

async function fetchJson<T>(
  path: string,
  params?: Record<string, string>,
): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }
  const res = await fetch(url.toString());
  if (!res.ok) {
    const err = await res.json().catch(() => ({
      error: { code: "INTERNAL", message: res.statusText },
    }));
    throw new ApiClientError(
      err.error?.code ?? "INTERNAL",
      err.error?.message ?? res.statusText,
    );
  }
  return res.json();
}

export const api = {
  getOverview: () => fetchJson<DashboardOverview>("/overview"),

  getSessions: (filters?: Record<string, string>) =>
    fetchJson<DashboardSession[]>("/sessions", filters),

  getTeams: (filters?: Record<string, string>) =>
    fetchJson<DashboardTeam[]>("/teams", filters),

  getTeamDetail: (teamName: string) =>
    fetchJson<TeamDetailResponse>(`/teams/${encodeURIComponent(teamName)}`),

  getTasks: (teamName: string, filters?: Record<string, string>) =>
    fetchJson<DashboardTask[]>(
      `/teams/${encodeURIComponent(teamName)}/tasks`,
      filters,
    ),

  getMessages: (teamName: string, filters?: Record<string, string>) =>
    fetchJson<DashboardMessage[]>(
      `/teams/${encodeURIComponent(teamName)}/messages`,
      filters,
    ),

  getMemory: (filters?: Record<string, string>) =>
    fetchJson<DashboardMemory[]>("/memory", filters),

  getMemoryStats: () => fetchJson<MemoryStatsResponse>("/memory/stats"),

  getAgentMemory: () => fetchJson<AgentMemorySummary[]>("/agent-memory"),

  getWorktrees: () => fetchJson<DashboardWorktree[]>("/worktrees"),

  getTelegram: () => fetchJson<TelegramSession[]>("/telegram"),

  getHealth: () => fetchJson<HealthResponse>("/health"),
};
