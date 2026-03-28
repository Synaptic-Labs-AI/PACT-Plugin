/**
 * server/routes/telegram.ts
 *
 * GET /api/telegram — Telegram bridge session state.
 */

import { Router } from 'express';
import { cached } from '../lib/cache.js';
import { CACHE_TTL } from '../config.js';
import { scanTelegramSessions } from '../scanners/telegram-scanner.js';

const router = Router();

router.get('/telegram', async (_req, res) => {
  try {
    const sessions = await cached('telegram', CACHE_TTL.telegram, scanTelegramSessions);
    res.json(sessions);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SCAN_ERROR', message } });
  }
});

export default router;
