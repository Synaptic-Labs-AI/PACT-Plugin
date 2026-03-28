/**
 * server/scanners/task-scanner.ts
 *
 * Reads ~/.claude/tasks/{team_name}/{task_id}.json for task data.
 * Task directories are named by team name (matching team directory names).
 * Each task file is a JSON object with id, subject, status, etc.
 */

import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { PATHS } from '../config.js';
import type { DashboardTask, RawTaskFile, TaskStatus } from '../lib/types.js';

const VALID_STATUSES = new Set<TaskStatus>(['pending', 'in_progress', 'completed', 'deleted']);

export async function scanTeamTasks(teamName: string): Promise<DashboardTask[]> {
  const teamDir = join(PATHS.tasks, teamName);
  let files: string[];
  try {
    files = await readdir(teamDir);
  } catch {
    return [];
  }

  const jsonFiles = files.filter((f) => f.endsWith('.json'));
  const tasks: DashboardTask[] = [];

  for (const file of jsonFiles) {
    try {
      const content = await readFile(join(teamDir, file), 'utf-8');
      const raw: RawTaskFile = JSON.parse(content);
      tasks.push(toTask(raw, teamName));
    } catch {
      // Skip malformed task files
    }
  }

  // Sort by id (numeric)
  tasks.sort((a, b) => parseInt(a.id) - parseInt(b.id));
  return tasks;
}

export async function scanAllTasks(): Promise<Record<string, DashboardTask[]>> {
  let dirs: string[];
  try {
    dirs = await readdir(PATHS.tasks);
  } catch {
    return {};
  }

  const result: Record<string, DashboardTask[]> = {};

  for (const dir of dirs) {
    const tasks = await scanTeamTasks(dir);
    if (tasks.length > 0) {
      result[dir] = tasks;
    }
  }

  return result;
}

function toTask(raw: RawTaskFile, teamName: string): DashboardTask {
  const status = VALID_STATUSES.has(raw.status as TaskStatus)
    ? (raw.status as TaskStatus)
    : 'pending';
  const blockedBy = raw.blockedBy ?? [];

  return {
    id: raw.id,
    teamName,
    subject: raw.subject || '',
    description: raw.description || '',
    activeForm: raw.activeForm ?? null,
    owner: raw.owner ?? null,
    status,
    blocks: raw.blocks ?? [],
    blockedBy,
    isBlocked: blockedBy.length > 0,
    metadata: (raw.metadata ?? {}) as DashboardTask['metadata'],
  };
}
