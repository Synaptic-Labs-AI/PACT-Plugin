/**
 * server/routes/messages.ts
 *
 * GET /api/teams/:teamName/messages — inbox messages for all agents in a team.
 * Supports filtering by agent name and timestamp.
 */

import { Router } from 'express';
import { cached } from '../lib/cache.js';
import { CACHE_TTL } from '../config.js';
import { scanTeamInboxes } from '../scanners/inbox-scanner.js';

const router = Router();

router.get('/teams/:teamName/messages', async (req, res) => {
  const { teamName } = req.params;

  try {
    let messages = await cached(`inboxes-${teamName}`, CACHE_TTL.inboxes, () =>
      scanTeamInboxes(teamName),
    );

    const { agent, since } = req.query;
    if (typeof agent === 'string') {
      messages = messages.filter((m) => m.toAgent === agent);
    }

    if (typeof since === 'string') {
      const sinceTime = new Date(since).getTime();
      if (!isNaN(sinceTime)) {
        messages = messages.filter((m) => new Date(m.timestamp).getTime() > sinceTime);
      }
    }

    res.json(messages);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SCAN_ERROR', message } });
  }
});

export default router;
