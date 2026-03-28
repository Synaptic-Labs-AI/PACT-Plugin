/**
 * server/lib/types.ts
 *
 * Shared TypeScript interfaces for the PACT Dashboard.
 * Consumed by both backend routes and frontend code.
 * Defines the normalized data model for all dashboard entities.
 */

// ============================================================
// Session & Team
// ============================================================

export interface DashboardSession {
  sessionId: string;
  pid: number;
  projectPath: string;
  projectName: string;
  startedAt: number;
  kind: 'interactive' | 'daemon';
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

// ============================================================
// Agent
// ============================================================

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

// ============================================================
// Task
// ============================================================

export type TaskStatus = 'pending' | 'in_progress' | 'completed' | 'deleted';

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
  [key: string]: unknown;
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

// ============================================================
// Message (from team inboxes)
// ============================================================

export type MessageType =
  | 'text'
  | 'task_assignment'
  | 'shutdown_request'
  | 'shutdown_response'
  | 'structured';

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

// ============================================================
// Handoff Log Entry
// ============================================================

export interface HandoffLogEntry {
  taskId: string;
  teammateName: string;
  timestamp: string;
  teamName: string;
}

// ============================================================
// Memory (from pact-memory SQLite)
// ============================================================

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

// ============================================================
// Agent Persistent Memory
// ============================================================

export interface AgentMemorySummary {
  specialistName: string;
  fileCount: number;
  memoryIndexEntries: string[];
  lastModified: string;
}

// ============================================================
// Git Worktree
// ============================================================

export interface DashboardWorktree {
  path: string;
  commitHash: string;
  branch: string;
  projectPath: string;
  projectName: string;
  isMain: boolean;
}

// ============================================================
// Telegram Bridge
// ============================================================

export interface TelegramSession {
  sessionId: string;
  pid: number;
  project: string;
  role: string;
  registeredAt: number;
  lastHeartbeat: number;
  isAlive: boolean;
}

// ============================================================
// Aggregated Views
// ============================================================

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

export interface DashboardOverview {
  activeSessionCount: number;
  totalAgentCount: number;
  inProgressTaskCount: number;
  blockerCount: number;
  memoryCount: number;
  projects: ProjectSummary[];
  recentActivity: ActivityEvent[];
}

export interface ActivityEvent {
  id: string;
  timestamp: string;
  type:
    | 'task_created'
    | 'task_started'
    | 'task_completed'
    | 'task_blocked'
    | 'agent_joined'
    | 'agent_message'
    | 'handoff_completed'
    | 'session_started'
    | 'session_ended'
    | 'algedonic_signal';
  teamName: string;
  agentName: string | null;
  agentColor: string | null;
  summary: string;
  sessionId: string | null;
  projectName: string | null;
  metadata: Record<string, unknown>;
}

// ============================================================
// API Response Types
// ============================================================

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

export interface HealthResponse {
  status: 'ok' | 'degraded';
  uptime: number;
  dataSources: Array<{
    name: string;
    status: 'ok' | 'stale' | 'error';
    lastChecked: string;
    itemCount: number;
    error?: string;
  }>;
  claudeDir: string;
  claudeDirExists: boolean;
}

export interface ApiError {
  error: {
    code: 'NOT_FOUND' | 'SCAN_ERROR' | 'SQLITE_ERROR' | 'INTERNAL';
    message: string;
    path?: string;
  };
}

// ============================================================
// Raw File System Types (used by scanners internally)
// ============================================================

export interface RawSessionFile {
  pid: number;
  sessionId: string;
  cwd: string;
  startedAt: number;
  kind?: string;
  entrypoint?: string;
}

export interface RawTeamConfig {
  name: string;
  description: string;
  createdAt: number;
  leadAgentId: string;
  leadSessionId: string;
  members: RawTeamMember[];
}

export interface RawTeamMember {
  agentId: string;
  name: string;
  agentType: string;
  model: string;
  color?: string;
  joinedAt: number;
  cwd?: string;
  subscriptions?: string[];
  tmuxPaneId?: string;
  backendType?: string;
  prompt?: string;
  planModeRequired?: boolean;
}

export interface RawInboxMessage {
  from: string;
  text: string;
  timestamp: string;
  read: boolean;
  color?: string;
  summary?: string;
}

export interface RawTaskFile {
  id: string;
  subject: string;
  description?: string;
  activeForm?: string;
  status: string;
  owner?: string;
  blocks?: string[];
  blockedBy?: string[];
  metadata?: Record<string, unknown>;
}

export interface RawHandoffEntry {
  task_id: string;
  teammate_name: string;
  timestamp: string;
}
