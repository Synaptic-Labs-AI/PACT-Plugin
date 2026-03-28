// Dashboard types — mirrors architecture doc section 5
// When shared/types.ts is available, re-export from there instead

export interface DashboardSession {
  sessionId: string;
  pid: number;
  projectPath: string;
  projectName: string;
  startedAt: number;
  kind: "interactive" | "daemon";
  entrypoint: string;
  isAlive: boolean;
  team: DashboardTeam | null;
}

export interface DashboardTeam {
  name: string;
  description: string;
  createdAt: number;
  leadAgentId: string;
  leadSessionId: string;
  members: DashboardAgent[];
  isPact: boolean;
}

export interface DashboardAgent {
  agentId: string;
  name: string;
  agentType: string;
  model: string;
  color: string | null;
  joinedAt: number;
  teamName: string;
  currentTask: DashboardTask | null;
  completedTaskCount: number;
  lastMessageAt: string | null;
}

export type TaskStatus = "pending" | "in_progress" | "completed" | "deleted";

export interface DashboardTask {
  id: string;
  teamName: string;
  subject: string;
  description: string;
  activeForm: string | null;
  owner: string | null;
  status: TaskStatus;
  blocks: string[];
  blockedBy: string[];
  isBlocked: boolean;
  metadata: TaskMetadata;
}

export interface TaskMetadata {
  type?: string;
  variety?: VarietyScore;
  handoff?: TaskHandoff;
  impact_cycle_count?: number;
  agent_id?: string;
  terminated?: boolean;
  reason?: string;
}

export interface VarietyScore {
  novelty: number;
  scope: number;
  uncertainty: number;
  risk: number;
  total: number;
}

export interface TaskHandoff {
  produced: string[];
  decisions: string[];
  uncertainty: string[];
  integration: string[];
  open_questions: string[];
}

export type MessageType =
  | "text"
  | "task_assignment"
  | "shutdown_request"
  | "shutdown_response"
  | "structured";

export interface DashboardMessage {
  id: string;
  teamName: string;
  toAgent: string;
  from: string;
  timestamp: string;
  read: boolean;
  color: string | null;
  messageType: MessageType;
  rawText: string;
  parsedContent: string;
  structuredData: Record<string, unknown> | null;
}

export interface HandoffLogEntry {
  taskId: string;
  teammateName: string;
  timestamp: string;
  teamName: string;
}

export interface DashboardMemory {
  id: string;
  context: string | null;
  goal: string | null;
  activeTasks: string | null;
  lessonsLearned: string | null;
  decisions: string | null;
  entities: string | null;
  projectId: string | null;
  sessionId: string | null;
  createdAt: string;
  updatedAt: string;
  reasoningChains: string | null;
  agreementsReached: string | null;
  disagreementsResolved: string | null;
}

export interface AgentMemorySummary {
  specialistName: string;
  fileCount: number;
  memoryIndexEntries: string[];
  lastModified: string;
}

export interface DashboardWorktree {
  path: string;
  commitHash: string;
  branch: string;
  projectPath: string;
  projectName: string;
  isMain: boolean;
}

export interface TelegramSession {
  sessionId: string;
  pid: number;
  project: string;
  role: string;
  registeredAt: number;
  lastHeartbeat: number;
  isAlive: boolean;
}

export interface ProjectSummary {
  projectPath: string;
  projectName: string;
  activeSessions: DashboardSession[];
  endedSessions: DashboardSession[];
  totalAgents: number;
  totalTasks: number;
  inProgressTasks: number;
  blockerCount: number;
  memoryCount: number;
  worktrees: DashboardWorktree[];
}

export interface ActivityEvent {
  id: string;
  timestamp: string;
  type:
    | "task_created"
    | "task_started"
    | "task_completed"
    | "task_blocked"
    | "agent_joined"
    | "agent_message"
    | "handoff_completed"
    | "session_started"
    | "session_ended"
    | "algedonic_signal";
  teamName: string;
  agentName: string | null;
  agentColor: string | null;
  summary: string;
  sessionId: string | null;
  projectName: string | null;
  metadata: Record<string, unknown>;
}

export interface DashboardOverview {
  activeSessionCount: number;
  totalAgentCount: number;
  inProgressTaskCount: number;
  blockerCount: number;
  memoryCount: number;
  projects: ProjectSummary[];
  recentActivity: ActivityEvent[];
}

export interface TeamDetailResponse {
  team: DashboardTeam;
  tasks: DashboardTask[];
  messages: DashboardMessage[];
  handoffLog: HandoffLogEntry[];
}

export interface MemoryStatsResponse {
  totalMemories: number;
  byProject: Array<{
    projectId: string;
    count: number;
    latestAt: string;
  }>;
}

export interface DataSourceHealth {
  name: string;
  status: "ok" | "stale" | "error";
  lastChecked: string;
  itemCount: number;
  error?: string;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  uptime: number;
  dataSources: DataSourceHealth[];
  claudeDir: string;
  claudeDirExists: boolean;
}

export interface Preferences {
  theme: "light" | "dark" | "system";
  showEndedSessions: boolean;
  showNonPactTeams: boolean;
  activityFeedLimit: number;
}
