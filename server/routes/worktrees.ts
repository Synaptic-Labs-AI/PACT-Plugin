/**
 * server/routes/worktrees.ts
 *
 * GET /api/worktrees — git worktrees across known project directories.
 * Derives project directories from active session cwd values.
 */

import { Router } from 'express';
import { cached } from '../lib/cache.js';
import { CACHE_TTL } from '../config.js';
import { scanSessions } from '../scanners/session-scanner.js';
import { scanWorktrees } from '../scanners/worktree-scanner.js';

const router = Router();

router.get('/worktrees', async (_req, res) => {
  try {
    const sessions = await cached('sessions', CACHE_TTL.sessions, scanSessions);
    const projectPaths = [...new Set(sessions.map((s) => s.projectPath).filter(Boolean))];

    const worktrees = await cached('worktrees', CACHE_TTL.worktrees, () =>
      scanWorktrees(projectPaths),
    );

    res.json(worktrees);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SCAN_ERROR', message } });
  }
});

export default router;
