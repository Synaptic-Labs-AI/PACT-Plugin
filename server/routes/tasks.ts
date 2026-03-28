/**
 * server/routes/tasks.ts
 *
 * GET /api/teams/:teamName/tasks — tasks for a specific team.
 * Supports filtering by status.
 */

import { Router } from 'express';
import { cached } from '../lib/cache.js';
import { CACHE_TTL } from '../config.js';
import { isValidTeamName } from '../lib/validate-params.js';
import { scanTeamTasks } from '../scanners/task-scanner.js';
import type { TaskStatus } from '../lib/types.js';

const router = Router();

const VALID_STATUSES = new Set<string>(['pending', 'in_progress', 'completed', 'deleted']);

router.get('/teams/:teamName/tasks', async (req, res) => {
  const { teamName } = req.params;

  if (!isValidTeamName(teamName)) {
    res.status(400).json({
      error: { code: 'VALIDATION_ERROR', message: 'Invalid team name' },
    });
    return;
  }

  try {
    let tasks = await cached(`tasks-${teamName}`, CACHE_TTL.tasks, () =>
      scanTeamTasks(teamName),
    );

    const { status } = req.query;
    if (typeof status === 'string' && VALID_STATUSES.has(status)) {
      tasks = tasks.filter((t) => t.status === (status as TaskStatus));
    }

    res.json(tasks);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SCAN_ERROR', message } });
  }
});

export default router;
