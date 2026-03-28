/**
 * server/routes/sessions.ts
 *
 * GET /api/sessions — list all sessions with liveness status.
 * Supports filtering by alive status and project name.
 */

import { Router } from 'express';
import { cached } from '../lib/cache.js';
import { CACHE_TTL } from '../config.js';
import { scanSessions } from '../scanners/session-scanner.js';
import { scanTeams } from '../scanners/team-scanner.js';
import type { DashboardTeam } from '../lib/types.js';

const router = Router();

router.get('/sessions', async (req, res) => {
  try {
    const [sessions, teams] = await Promise.all([
      cached('sessions', CACHE_TTL.sessions, scanSessions),
      cached('teams', CACHE_TTL.teams, scanTeams),
    ]);

    // Link sessions to teams
    const teamBySessionId = new Map<string, DashboardTeam>();
    for (const team of teams) {
      if (team.leadSessionId) {
        teamBySessionId.set(team.leadSessionId, team);
      }
    }

    let result = sessions.map((s) => ({
      ...s,
      team: teamBySessionId.get(s.sessionId) ?? null,
    }));

    // Apply filters
    const { alive, project } = req.query;
    if (alive === 'true') {
      result = result.filter((s) => s.isAlive);
    } else if (alive === 'false') {
      result = result.filter((s) => !s.isAlive);
    }

    if (typeof project === 'string') {
      const projectLower = project.toLowerCase();
      result = result.filter((s) => s.projectName.toLowerCase().includes(projectLower));
    }

    res.json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SCAN_ERROR', message } });
  }
});

export default router;
