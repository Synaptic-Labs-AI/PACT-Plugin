/**
 * server/routes/agent-memory.ts
 *
 * GET /api/agent-memory — summary of each specialist's persistent memory.
 */

import { Router } from 'express';
import { cached } from '../lib/cache.js';
import { CACHE_TTL } from '../config.js';
import { scanAgentMemories } from '../scanners/agent-memory-scanner.js';

const router = Router();

router.get('/agent-memory', async (_req, res) => {
  try {
    const summaries = await cached('agent-memory', CACHE_TTL.agentMemory, scanAgentMemories);
    res.json(summaries);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SCAN_ERROR', message } });
  }
});

export default router;
