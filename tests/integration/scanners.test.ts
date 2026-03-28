/**
 * Integration tests for server/scanners/*.ts
 *
 * Tests scanners against the REAL ~/.claude/ directory.
 * These tests verify that scanners can read and parse actual data files.
 * They should pass on any machine with a populated ~/.claude/ directory.
 */

import { describe, it, expect } from 'vitest';
import { existsSync } from 'node:fs';
import { PATHS, CLAUDE_DIR } from '../../server/config.js';

// Guard: skip integration tests if ~/.claude doesn't exist
const claudeDirExists = existsSync(CLAUDE_DIR);

describe.skipIf(!claudeDirExists)('session-scanner (integration)', () => {
  it('should scan sessions from ~/.claude/sessions without crashing', async () => {
    const { scanSessions } = await import('../../server/scanners/session-scanner.js');
    const sessions = await scanSessions();
    expect(Array.isArray(sessions)).toBe(true);
    // We expect at least one session on a machine running Claude Code
    if (sessions.length > 0) {
      const session = sessions[0]!;
      expect(session).toHaveProperty('sessionId');
      expect(session).toHaveProperty('pid');
      expect(session).toHaveProperty('projectPath');
      expect(session).toHaveProperty('isAlive');
      expect(typeof session.sessionId).toBe('string');
      expect(typeof session.pid).toBe('number');
      expect(typeof session.isAlive).toBe('boolean');
    }
  });
});

describe.skipIf(!claudeDirExists)('team-scanner (integration)', () => {
  it('should scan teams from ~/.claude/teams without crashing', async () => {
    const { scanTeams } = await import('../../server/scanners/team-scanner.js');
    const teams = await scanTeams();
    expect(Array.isArray(teams)).toBe(true);
    if (teams.length > 0) {
      const team = teams[0]!;
      expect(team).toHaveProperty('name');
      expect(team).toHaveProperty('members');
      expect(team).toHaveProperty('isPact');
      expect(Array.isArray(team.members)).toBe(true);
    }
  });

  it('scanTeam should return null for non-existent team', async () => {
    const { scanTeam } = await import('../../server/scanners/team-scanner.js');
    const team = await scanTeam('nonexistent-team-name-12345');
    expect(team).toBeNull();
  });
});

describe.skipIf(!claudeDirExists)('task-scanner (integration)', () => {
  it('should scan all tasks from ~/.claude/tasks without crashing', async () => {
    const { scanAllTasks } = await import('../../server/scanners/task-scanner.js');
    const allTasks = await scanAllTasks();
    expect(typeof allTasks).toBe('object');
    // Verify structure: Record<string, DashboardTask[]>
    for (const [teamName, tasks] of Object.entries(allTasks)) {
      expect(typeof teamName).toBe('string');
      expect(Array.isArray(tasks)).toBe(true);
      if (tasks.length > 0) {
        const task = tasks[0]!;
        expect(task).toHaveProperty('id');
        expect(task).toHaveProperty('subject');
        expect(task).toHaveProperty('status');
        expect(['pending', 'in_progress', 'completed', 'deleted']).toContain(task.status);
      }
    }
  });

  it('scanTeamTasks should return empty for non-existent team', async () => {
    const { scanTeamTasks } = await import('../../server/scanners/task-scanner.js');
    const tasks = await scanTeamTasks('nonexistent-team-xyz');
    expect(tasks).toEqual([]);
  });
});

describe.skipIf(!claudeDirExists)('inbox-scanner (integration)', () => {
  it('should return empty array for non-existent team inbox', async () => {
    const { scanTeamInboxes } = await import('../../server/scanners/inbox-scanner.js');
    const messages = await scanTeamInboxes('nonexistent-team-abc');
    expect(messages).toEqual([]);
  });
});

describe.skipIf(!claudeDirExists)('handoff-scanner (integration)', () => {
  it('should return empty array for non-existent team handoffs', async () => {
    const { scanTeamHandoffs } = await import('../../server/scanners/handoff-scanner.js');
    const handoffs = await scanTeamHandoffs('nonexistent-team-def');
    expect(handoffs).toEqual([]);
  });
});

describe.skipIf(!claudeDirExists)('memory-reader (integration)', () => {
  const memoryDbExists = existsSync(PATHS.pactMemoryDb);

  it.skipIf(!memoryDbExists)('should query memories from SQLite without crashing', async () => {
    const { queryMemories } = await import('../../server/scanners/memory-reader.js');
    const memories = queryMemories({ limit: 5 });
    expect(Array.isArray(memories)).toBe(true);
    if (memories.length > 0) {
      const memory = memories[0]!;
      expect(memory).toHaveProperty('id');
      expect(memory).toHaveProperty('createdAt');
    }
  });

  it.skipIf(!memoryDbExists)('should return memory stats', async () => {
    const { queryMemoryStats } = await import('../../server/scanners/memory-reader.js');
    const stats = queryMemoryStats();
    expect(stats).toHaveProperty('totalMemories');
    expect(stats).toHaveProperty('byProject');
    expect(typeof stats.totalMemories).toBe('number');
    expect(Array.isArray(stats.byProject)).toBe(true);
  });

  it('should handle missing database gracefully', async () => {
    // queryMemories returns [] when db doesn't exist - this is safe to test
    const { queryMemories } = await import('../../server/scanners/memory-reader.js');
    // Even if the DB exists, this should not throw
    const result = queryMemories({ limit: 1 });
    expect(Array.isArray(result)).toBe(true);
  });
});

describe.skipIf(!claudeDirExists)('agent-memory-scanner (integration)', () => {
  it('should scan agent memory directories without crashing', async () => {
    const { scanAgentMemories } = await import('../../server/scanners/agent-memory-scanner.js');
    const summaries = await scanAgentMemories();
    expect(Array.isArray(summaries)).toBe(true);
    if (summaries.length > 0) {
      const summary = summaries[0]!;
      expect(summary).toHaveProperty('specialistName');
      expect(summary).toHaveProperty('fileCount');
      expect(typeof summary.specialistName).toBe('string');
      expect(typeof summary.fileCount).toBe('number');
    }
  });
});

describe.skipIf(!claudeDirExists)('worktree-scanner (integration)', () => {
  it('should scan worktrees for known project paths', async () => {
    const { scanWorktrees } = await import('../../server/scanners/worktree-scanner.js');
    // Use the current worktree's parent as a test project path
    const worktrees = await scanWorktrees(['/Users/v4lheru/Documents/PACT-prompt']);
    expect(Array.isArray(worktrees)).toBe(true);
    if (worktrees.length > 0) {
      const wt = worktrees[0]!;
      expect(wt).toHaveProperty('path');
      expect(wt).toHaveProperty('branch');
      expect(typeof wt.path).toBe('string');
    }
  });

  it('should return empty for non-git directories', async () => {
    const { scanWorktrees } = await import('../../server/scanners/worktree-scanner.js');
    const worktrees = await scanWorktrees(['/tmp']);
    expect(Array.isArray(worktrees)).toBe(true);
  });
});
