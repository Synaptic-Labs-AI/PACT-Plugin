/**
 * server/config.ts
 *
 * Server configuration: paths, ports, and cache TTLs.
 * All filesystem paths for ~/.claude/ data sources are centralized here.
 */

import { homedir } from 'node:os';
import { join } from 'node:path';

const HOME = homedir();

export const CLAUDE_DIR = join(HOME, '.claude');

export const PATHS = {
  sessions: join(CLAUDE_DIR, 'sessions'),
  teams: join(CLAUDE_DIR, 'teams'),
  tasks: join(CLAUDE_DIR, 'tasks'),
  pactMemoryDb: join(CLAUDE_DIR, 'pact-memory', 'memory.db'),
  agentMemory: join(CLAUDE_DIR, 'agent-memory'),
  telegram: join(CLAUDE_DIR, 'pact-telegram', 'coordinator'),
} as const;

export const SERVER_PORT = parseInt(process.env.PORT || '3001', 10);
export const SERVER_HOST = '127.0.0.1';

/** Cache TTLs in seconds, matched to architecture doc section 4.1. */
export const CACHE_TTL = {
  tasks: 3,
  inboxes: 3,
  teams: 30,
  sessions: 10,
  worktrees: 15,
  handoffs: 10,
  memory: 120,
  agentMemory: 300,
  telegram: 300,
} as const;

/** Maximum events stored in the activity ring buffer. */
export const ACTIVITY_BUFFER_SIZE = 500;

/** Number of recent activity events included in /api/overview. */
export const OVERVIEW_ACTIVITY_LIMIT = 50;

/** PACT team name pattern: pact-{8 hex chars}. Case-insensitive. */
export const PACT_TEAM_PATTERN = /^pact-[a-f0-9]{8}$/i;
