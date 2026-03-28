/**
 * server/routes/health.ts
 *
 * GET /api/health — system health and data freshness.
 * Checks each data source (sessions, teams, tasks, memory, etc.)
 * and reports status along with server uptime.
 */

import { Router } from 'express';
import { existsSync } from 'node:fs';
import { readdir } from 'node:fs/promises';
import { PATHS, CLAUDE_DIR } from '../config.js';
import { scanSessions } from '../scanners/session-scanner.js';
import { scanTeams } from '../scanners/team-scanner.js';
import { scanAllTasks } from '../scanners/task-scanner.js';
import { queryMemoryStats } from '../scanners/memory-reader.js';
import { scanAgentMemories } from '../scanners/agent-memory-scanner.js';
import { scanTelegramSessions } from '../scanners/telegram-scanner.js';
import type { HealthResponse } from '../lib/types.js';

const router = Router();
const startTime = Date.now();

interface DataSourceCheck {
  name: string;
  check: () => Promise<{ itemCount: number }>;
}

const checks: DataSourceCheck[] = [
  {
    name: 'sessions',
    check: async () => ({ itemCount: (await scanSessions()).length }),
  },
  {
    name: 'teams',
    check: async () => ({ itemCount: (await scanTeams()).length }),
  },
  {
    name: 'tasks',
    check: async () => {
      const all = await scanAllTasks();
      const count = Object.values(all).reduce((sum, tasks) => sum + tasks.length, 0);
      return { itemCount: count };
    },
  },
  {
    name: 'pact-memory',
    check: async () => {
      const stats = queryMemoryStats();
      return { itemCount: stats.totalMemories };
    },
  },
  {
    name: 'agent-memory',
    check: async () => ({ itemCount: (await scanAgentMemories()).length }),
  },
  {
    name: 'telegram',
    check: async () => ({ itemCount: (await scanTelegramSessions()).length }),
  },
];

router.get('/health', async (_req, res) => {
  const dataSources: HealthResponse['dataSources'] = [];
  let hasError = false;

  for (const { name, check } of checks) {
    try {
      const result = await check();
      dataSources.push({
        name,
        status: 'ok',
        lastChecked: new Date().toISOString(),
        itemCount: result.itemCount,
      });
    } catch (error) {
      hasError = true;
      dataSources.push({
        name,
        status: 'error',
        lastChecked: new Date().toISOString(),
        itemCount: 0,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  const response: HealthResponse = {
    status: hasError ? 'degraded' : 'ok',
    uptime: Math.floor((Date.now() - startTime) / 1000),
    dataSources,
    claudeDir: CLAUDE_DIR,
    claudeDirExists: existsSync(CLAUDE_DIR),
  };

  res.json(response);
});

export default router;
