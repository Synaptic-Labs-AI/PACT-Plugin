/**
 * server/scanners/session-scanner.ts
 *
 * Reads ~/.claude/sessions/*.json files and checks PID liveness.
 * Session files are named by PID (e.g., 19153.json) and contain
 * session metadata including sessionId, cwd, and startedAt.
 */

import { readdir, readFile } from 'node:fs/promises';
import { join, basename } from 'node:path';
import { PATHS } from '../config.js';
import { isProcessAlive } from '../lib/pid.js';
import type { DashboardSession, RawSessionFile } from '../lib/types.js';

export async function scanSessions(): Promise<DashboardSession[]> {
  let files: string[];
  try {
    files = await readdir(PATHS.sessions);
  } catch {
    return [];
  }

  const jsonFiles = files.filter((f) => f.endsWith('.json'));
  const sessions: DashboardSession[] = [];

  for (const file of jsonFiles) {
    try {
      const content = await readFile(join(PATHS.sessions, file), 'utf-8');
      const raw: RawSessionFile = JSON.parse(content);

      const projectPath = raw.cwd || '';
      const projectName = projectPath ? basename(projectPath) : 'unknown';

      sessions.push({
        sessionId: raw.sessionId,
        pid: raw.pid,
        projectPath,
        projectName,
        startedAt: raw.startedAt,
        kind: (raw.kind as DashboardSession['kind']) || 'interactive',
        entrypoint: raw.entrypoint || 'cli',
        isAlive: isProcessAlive(raw.pid),
        team: null, // Linked later by the overview aggregator
      });
    } catch {
      // Skip malformed session files
    }
  }

  return sessions;
}
