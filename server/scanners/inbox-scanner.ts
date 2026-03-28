/**
 * server/scanners/inbox-scanner.ts
 *
 * Reads ~/.claude/teams/{team_name}/inboxes/{agent_name}.json for inbox messages.
 * Each inbox file is a JSON array of message objects with double-encoded text.
 */

import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { PATHS } from '../config.js';
import { toMessage } from '../lib/parse-message.js';
import type { DashboardMessage, RawInboxMessage } from '../lib/types.js';

export async function scanTeamInboxes(teamName: string): Promise<DashboardMessage[]> {
  const inboxDir = join(PATHS.teams, teamName, 'inboxes');
  let files: string[];
  try {
    files = await readdir(inboxDir);
  } catch {
    return [];
  }

  const jsonFiles = files.filter((f) => f.endsWith('.json'));
  const allMessages: DashboardMessage[] = [];

  for (const file of jsonFiles) {
    const agentName = file.replace('.json', '');
    try {
      const content = await readFile(join(inboxDir, file), 'utf-8');
      const rawMessages: RawInboxMessage[] = JSON.parse(content);

      if (!Array.isArray(rawMessages)) continue;

      for (let i = 0; i < rawMessages.length; i++) {
        const raw = rawMessages[i];
        if (!raw) continue;
        allMessages.push(toMessage(raw, teamName, agentName, i));
      }
    } catch {
      // Skip malformed inbox files
    }
  }

  // Sort by timestamp, newest first
  allMessages.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  return allMessages;
}
