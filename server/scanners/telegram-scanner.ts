/**
 * server/scanners/telegram-scanner.ts
 *
 * Reads ~/.claude/pact-telegram/coordinator/ for Telegram bridge session files.
 * Each session file tracks a connected PACT session's Telegram bridge state.
 */

import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { PATHS } from '../config.js';
import type { TelegramSession } from '../lib/types.js';

interface RawTelegramSession {
  session_id: string;
  pid: number;
  project: string;
  role: string;
  registered_at: number;
  last_heartbeat: number;
}

export async function scanTelegramSessions(): Promise<TelegramSession[]> {
  let files: string[];
  try {
    files = await readdir(PATHS.telegram);
  } catch {
    return [];
  }

  const jsonFiles = files.filter((f) => f.endsWith('.json'));
  const sessions: TelegramSession[] = [];

  for (const file of jsonFiles) {
    try {
      const content = await readFile(join(PATHS.telegram, file), 'utf-8');
      const raw: RawTelegramSession = JSON.parse(content);
      const now = Math.floor(Date.now() / 1000);

      sessions.push({
        sessionId: raw.session_id || file.replace('.json', ''),
        pid: raw.pid || 0,
        project: raw.project || 'unknown',
        role: raw.role || 'unknown',
        registeredAt: raw.registered_at || 0,
        lastHeartbeat: raw.last_heartbeat || 0,
        // Consider alive if heartbeat was within last 60 seconds
        isAlive: raw.last_heartbeat > 0 && (now - raw.last_heartbeat) < 60,
      });
    } catch {
      // Skip malformed files
    }
  }

  return sessions;
}
