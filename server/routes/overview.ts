/**
 * server/routes/overview.ts
 *
 * GET /api/overview — aggregated dashboard data for the main page.
 * Combines sessions, teams, tasks into ProjectSummary objects.
 * Includes recent activity events from the ring buffer.
 */

import { Router } from 'express';
import { basename } from 'node:path';
import { cached } from '../lib/cache.js';
import { getRecentEvents } from '../lib/activity-buffer.js';
import { CACHE_TTL, OVERVIEW_ACTIVITY_LIMIT } from '../config.js';
import { scanSessions } from '../scanners/session-scanner.js';
import { scanTeams } from '../scanners/team-scanner.js';
import { scanAllTasks } from '../scanners/task-scanner.js';
import { queryMemoryStats } from '../scanners/memory-reader.js';
import type {
  DashboardOverview,
  DashboardSession,
  DashboardTeam,
  DashboardTask,
  ProjectSummary,
} from '../lib/types.js';

const router = Router();

router.get('/overview', async (_req, res) => {
  try {
    const [sessions, teams, allTasks, memStats] = await Promise.all([
      cached('sessions', CACHE_TTL.sessions, scanSessions),
      cached('teams', CACHE_TTL.teams, scanTeams),
      cached('tasks-all', CACHE_TTL.tasks, scanAllTasks),
      cached('memory-stats', CACHE_TTL.memory, queryMemoryStats),
    ]);

    // Link sessions to teams via leadSessionId
    const teamBySessionId = new Map<string, DashboardTeam>();
    for (const team of teams) {
      if (team.leadSessionId) {
        teamBySessionId.set(team.leadSessionId, team);
      }
    }

    for (const session of sessions) {
      session.team = teamBySessionId.get(session.sessionId) ?? null;
    }

    // Enrich agents with task data
    for (const team of teams) {
      const teamTasks = allTasks[team.name] ?? [];
      for (const agent of team.members) {
        agent.currentTask = teamTasks.find(
          (t) => t.owner === agent.name && t.status === 'in_progress',
        ) ?? null;
        agent.completedTaskCount = teamTasks.filter(
          (t) => t.owner === agent.name && t.status === 'completed',
        ).length;
      }
    }

    // Group sessions by project
    const projectMap = new Map<string, { active: DashboardSession[]; ended: DashboardSession[] }>();
    for (const session of sessions) {
      const key = session.projectPath || 'unknown';
      if (!projectMap.has(key)) {
        projectMap.set(key, { active: [], ended: [] });
      }
      const bucket = projectMap.get(key)!;
      if (session.isAlive) {
        bucket.active.push(session);
      } else {
        bucket.ended.push(session);
      }
    }

    // Build project summaries
    const projects: ProjectSummary[] = [];
    let totalAgents = 0;
    let totalInProgress = 0;
    let totalBlockers = 0;

    for (const [projectPath, { active, ended }] of projectMap) {
      // Collect tasks for teams associated with this project's sessions
      const projectTeamNames = new Set<string>();
      for (const s of [...active, ...ended]) {
        if (s.team) projectTeamNames.add(s.team.name);
      }

      let projectTasks: DashboardTask[] = [];
      let agentCount = 0;
      for (const teamName of projectTeamNames) {
        projectTasks = projectTasks.concat(allTasks[teamName] ?? []);
        const team = teams.find((t) => t.name === teamName);
        if (team) agentCount += team.members.length;
      }

      const inProgress = projectTasks.filter((t) => t.status === 'in_progress').length;
      const blockers = projectTasks.filter((t) => t.isBlocked && t.status !== 'completed').length;

      totalAgents += agentCount;
      totalInProgress += inProgress;
      totalBlockers += blockers;

      // Match memory count by project name (fuzzy)
      const projectName = basename(projectPath);
      const projectMemCount =
        memStats.byProject.find(
          (p) => p.projectId.toLowerCase() === projectName.toLowerCase(),
        )?.count ?? 0;

      projects.push({
        projectPath,
        projectName,
        activeSessions: active,
        endedSessions: ended,
        totalAgents: agentCount,
        totalTasks: projectTasks.length,
        inProgressTasks: inProgress,
        blockerCount: blockers,
        memoryCount: projectMemCount,
        worktrees: [], // Populated on demand (worktree scan is slower)
      });
    }

    // Sort: projects with active sessions first
    projects.sort((a, b) => b.activeSessions.length - a.activeSessions.length);

    const overview: DashboardOverview = {
      activeSessionCount: sessions.filter((s) => s.isAlive).length,
      totalAgentCount: totalAgents,
      inProgressTaskCount: totalInProgress,
      blockerCount: totalBlockers,
      memoryCount: memStats.totalMemories,
      projects,
      recentActivity: getRecentEvents(OVERVIEW_ACTIVITY_LIMIT),
    };

    res.json(overview);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    res.status(500).json({ error: { code: 'INTERNAL', message } });
  }
});

export default router;
