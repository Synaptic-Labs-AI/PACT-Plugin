/**
 * server/scanners/handoff-scanner.ts
 *
 * Reads ~/.claude/teams/{team_name}/completed_handoffs.jsonl for handoff log entries.
 * Each line is a JSON object with task_id, teammate_name, and timestamp.
 */

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { PATHS } from '../config.js';
import type { HandoffLogEntry, RawHandoffEntry } from '../lib/types.js';

export async function scanTeamHandoffs(teamName: string): Promise<HandoffLogEntry[]> {
  const filePath = join(PATHS.teams, teamName, 'completed_handoffs.jsonl');
  let content: string;
  try {
    content = await readFile(filePath, 'utf-8');
  } catch {
    return [];
  }

  const entries: HandoffLogEntry[] = [];
  const lines = content.trim().split('\n');

  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const raw: RawHandoffEntry = JSON.parse(line);
      entries.push({
        taskId: raw.task_id,
        teammateName: raw.teammate_name,
        timestamp: raw.timestamp,
        teamName,
      });
    } catch {
      // Skip malformed JSONL lines
    }
  }

  return entries;
}
