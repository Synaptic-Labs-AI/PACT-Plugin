/**
 * server/routes/teams.ts
 *
 * GET /api/teams — list all teams with member rosters.
 * GET /api/teams/:teamName — detailed team info with tasks, messages, handoffs.
 */

import { Router } from 'express';
import { cached } from '../lib/cache.js';
import { CACHE_TTL } from '../config.js';
import { scanTeams, scanTeam } from '../scanners/team-scanner.js';
import { scanTeamTasks } from '../scanners/task-scanner.js';
import { scanTeamInboxes } from '../scanners/inbox-scanner.js';
import { scanTeamHandoffs } from '../scanners/handoff-scanner.js';

const router = Router();

router.get('/teams', async (req, res) => {
  try {
    let teams = await cached('teams', CACHE_TTL.teams, scanTeams);

    if (req.query.pact === 'true') {
      teams = teams.filter((t) => t.isPact);
    }

    res.json(teams);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SCAN_ERROR', message } });
  }
});

router.get('/teams/:teamName', async (req, res) => {
  const { teamName } = req.params;

  try {
    const team = await cached(`team-${teamName}`, CACHE_TTL.teams, () => scanTeam(teamName));

    if (!team) {
      res.status(404).json({
        error: { code: 'NOT_FOUND', message: `Team ${teamName} not found` },
      });
      return;
    }

    const [tasks, messages, handoffLog] = await Promise.all([
      cached(`tasks-${teamName}`, CACHE_TTL.tasks, () => scanTeamTasks(teamName)),
      cached(`inboxes-${teamName}`, CACHE_TTL.inboxes, () => scanTeamInboxes(teamName)),
      cached(`handoffs-${teamName}`, CACHE_TTL.handoffs, () => scanTeamHandoffs(teamName)),
    ]);

    // Enrich agents with task/inbox data
    for (const agent of team.members) {
      agent.currentTask =
        tasks.find((t) => t.owner === agent.name && t.status === 'in_progress') ?? null;
      agent.completedTaskCount = tasks.filter(
        (t) => t.owner === agent.name && t.status === 'completed',
      ).length;

      const agentMessages = messages.filter((m) => m.toAgent === agent.name);
      if (agentMessages.length > 0) {
        agent.lastMessageAt = agentMessages[0]!.timestamp;
      }
    }

    res.json({ team, tasks, messages, handoffLog });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'SCAN_ERROR', message } });
  }
});

export default router;
