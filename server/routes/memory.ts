/**
 * server/routes/memory.ts
 *
 * GET /api/memory — pact-memory entries from SQLite.
 * GET /api/memory/stats — aggregate stats per project.
 */

import { Router } from 'express';
import { cached } from '../lib/cache.js';
import { CACHE_TTL } from '../config.js';
import { queryMemories, queryMemoryStats } from '../scanners/memory-reader.js';

const router = Router();

router.get('/memory', async (req, res) => {
  try {
    const { project, limit, offset } = req.query;

    const memories = await cached(
      `memory-${project ?? 'all'}-${limit ?? 50}-${offset ?? 0}`,
      CACHE_TTL.memory,
      () =>
        queryMemories({
          project: typeof project === 'string' ? project : undefined,
          limit: typeof limit === 'string' ? parseInt(limit, 10) : undefined,
          offset: typeof offset === 'string' ? parseInt(offset, 10) : undefined,
        }),
    );

    res.json(memories);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SQLITE_ERROR', message } });
  }
});

router.get('/memory/stats', async (_req, res) => {
  try {
    const stats = await cached('memory-stats', CACHE_TTL.memory, queryMemoryStats);
    res.json(stats);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SQLITE_ERROR', message } });
  }
});

export default router;
