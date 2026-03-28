/**
 * server/scanners/memory-reader.ts
 *
 * Reads from ~/.claude/pact-memory/memory.db using better-sqlite3.
 * Database is opened in read-only mode with query_only pragma.
 */

import Database from 'better-sqlite3';
import { existsSync } from 'node:fs';
import { PATHS } from '../config.js';
import type { DashboardMemory } from '../lib/types.js';

let db: Database.Database | null = null;

function getDb(): Database.Database | null {
  if (db) return db;

  if (!existsSync(PATHS.pactMemoryDb)) {
    return null;
  }

  try {
    db = new Database(PATHS.pactMemoryDb, { readonly: true });
    db.pragma('query_only = ON');
    return db;
  } catch {
    return null;
  }
}

interface MemoryRow {
  id: string;
  context: string | null;
  goal: string | null;
  active_tasks: string | null;
  lessons_learned: string | null;
  decisions: string | null;
  entities: string | null;
  project_id: string | null;
  session_id: string | null;
  created_at: string;
  updated_at: string;
  reasoning_chains: string | null;
  agreements_reached: string | null;
  disagreements_resolved: string | null;
}

function rowToMemory(row: MemoryRow): DashboardMemory {
  return {
    id: row.id,
    context: row.context,
    goal: row.goal,
    activeTasks: row.active_tasks,
    lessonsLearned: row.lessons_learned,
    decisions: row.decisions,
    entities: row.entities,
    projectId: row.project_id,
    sessionId: row.session_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    reasoningChains: row.reasoning_chains,
    agreementsReached: row.agreements_reached,
    disagreementsResolved: row.disagreements_resolved,
  };
}

export interface MemoryQueryOptions {
  project?: string;
  limit?: number;
  offset?: number;
}

export function queryMemories(options: MemoryQueryOptions = {}): DashboardMemory[] {
  const database = getDb();
  if (!database) return [];

  const limit = options.limit ?? 50;
  const offset = options.offset ?? 0;

  try {
    if (options.project) {
      const stmt = database.prepare(
        'SELECT * FROM memories WHERE project_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
      );
      return (stmt.all(options.project, limit, offset) as MemoryRow[]).map(rowToMemory);
    }

    const stmt = database.prepare(
      'SELECT * FROM memories ORDER BY created_at DESC LIMIT ? OFFSET ?',
    );
    return (stmt.all(limit, offset) as MemoryRow[]).map(rowToMemory);
  } catch {
    return [];
  }
}

export interface MemoryProjectStat {
  projectId: string;
  count: number;
  latestAt: string;
}

export function queryMemoryStats(): { totalMemories: number; byProject: MemoryProjectStat[] } {
  const database = getDb();
  if (!database) return { totalMemories: 0, byProject: [] };

  try {
    const countStmt = database.prepare('SELECT COUNT(*) as count FROM memories');
    const countRow = countStmt.get() as { count: number };

    const projectStmt = database.prepare(`
      SELECT
        COALESCE(project_id, 'unknown') as project_id,
        COUNT(*) as count,
        MAX(created_at) as latest_at
      FROM memories
      GROUP BY COALESCE(project_id, 'unknown')
      ORDER BY count DESC
    `);
    const projectRows = projectStmt.all() as Array<{
      project_id: string;
      count: number;
      latest_at: string;
    }>;

    return {
      totalMemories: countRow.count,
      byProject: projectRows.map((r) => ({
        projectId: r.project_id,
        count: r.count,
        latestAt: r.latest_at,
      })),
    };
  } catch {
    return { totalMemories: 0, byProject: [] };
  }
}

/** Close the database connection on shutdown. */
export function closeMemoryDb(): void {
  if (db) {
    db.close();
    db = null;
  }
}
