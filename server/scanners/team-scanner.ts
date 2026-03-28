/**
 * server/scanners/team-scanner.ts
 *
 * Reads ~/.claude/teams/{team_name}/config.json for each team directory.
 * Team directories may be named as pact-{hash}, UUIDs, or friendly names.
 * Some team directories lack a config.json (only inboxes/).
 */

import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { PATHS, PACT_TEAM_PATTERN } from '../config.js';
import type { DashboardTeam, DashboardAgent, RawTeamConfig, RawTeamMember } from '../lib/types.js';

export async function scanTeams(): Promise<DashboardTeam[]> {
  let dirs: string[];
  try {
    dirs = await readdir(PATHS.teams);
  } catch {
    return [];
  }

  const teams: DashboardTeam[] = [];

  for (const dir of dirs) {
    try {
      const configPath = join(PATHS.teams, dir, 'config.json');
      const content = await readFile(configPath, 'utf-8');
      const raw: RawTeamConfig = JSON.parse(content);

      teams.push({
        name: raw.name || dir,
        description: raw.description || '',
        createdAt: raw.createdAt || 0,
        leadAgentId: raw.leadAgentId || '',
        leadSessionId: raw.leadSessionId || '',
        members: (raw.members || []).map((m) => toAgent(m, raw.name || dir)),
        isPact: PACT_TEAM_PATTERN.test(raw.name || dir),
      });
    } catch {
      // Directory exists but no config.json or malformed -- create minimal entry
      // Only include if it has an inboxes directory (indicates an active team)
      try {
        await readdir(join(PATHS.teams, dir, 'inboxes'));
        teams.push({
          name: dir,
          description: '',
          createdAt: 0,
          leadAgentId: '',
          leadSessionId: '',
          members: [],
          isPact: PACT_TEAM_PATTERN.test(dir),
        });
      } catch {
        // No inboxes either -- skip
      }
    }
  }

  return teams;
}

export async function scanTeam(teamName: string): Promise<DashboardTeam | null> {
  try {
    const configPath = join(PATHS.teams, teamName, 'config.json');
    const content = await readFile(configPath, 'utf-8');
    const raw: RawTeamConfig = JSON.parse(content);

    return {
      name: raw.name || teamName,
      description: raw.description || '',
      createdAt: raw.createdAt || 0,
      leadAgentId: raw.leadAgentId || '',
      leadSessionId: raw.leadSessionId || '',
      members: (raw.members || []).map((m) => toAgent(m, raw.name || teamName)),
      isPact: PACT_TEAM_PATTERN.test(raw.name || teamName),
    };
  } catch {
    return null;
  }
}

function toAgent(raw: RawTeamMember, teamName: string): DashboardAgent {
  return {
    agentId: raw.agentId || `${raw.name}@${teamName}`,
    name: raw.name,
    agentType: raw.agentType || 'unknown',
    model: raw.model || 'unknown',
    color: raw.color ?? null,
    joinedAt: raw.joinedAt || 0,
    teamName,
    currentTask: null,       // Computed later by joining with tasks
    completedTaskCount: 0,   // Computed later
    lastMessageAt: null,     // Computed later from inbox scan
  };
}
