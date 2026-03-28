/**
 * server/scanners/worktree-scanner.ts
 *
 * Runs `git worktree list --porcelain` in known project directories
 * to discover active worktrees. Project directories are derived from
 * active session cwd values.
 */

import { exec } from 'node:child_process';
import { basename, dirname } from 'node:path';
import { promisify } from 'node:util';
import type { DashboardWorktree } from '../lib/types.js';

const execAsync = promisify(exec);

/**
 * Parse `git worktree list --porcelain` output into structured worktree entries.
 * Each worktree block looks like:
 *   worktree /path/to/worktree
 *   HEAD abc123...
 *   branch refs/heads/branch-name
 *   (blank line)
 */
function parsePorcelain(output: string, projectPath: string): DashboardWorktree[] {
  const worktrees: DashboardWorktree[] = [];
  const blocks = output.trim().split('\n\n');

  for (const block of blocks) {
    if (!block.trim()) continue;

    const lines = block.trim().split('\n');
    let path = '';
    let commitHash = '';
    let branch = '';

    for (const line of lines) {
      if (line.startsWith('worktree ')) {
        path = line.slice('worktree '.length);
      } else if (line.startsWith('HEAD ')) {
        commitHash = line.slice('HEAD '.length);
      } else if (line.startsWith('branch ')) {
        branch = line.slice('branch refs/heads/'.length);
      }
    }

    if (path) {
      const isMain = branch === 'main' || branch === 'master';
      worktrees.push({
        path,
        commitHash,
        branch: branch || 'detached',
        projectPath,
        projectName: basename(projectPath),
        isMain,
      });
    }
  }

  return worktrees;
}

/**
 * Scan worktrees for a set of project directories.
 * Deduplicates by resolving to the git root first.
 */
export async function scanWorktrees(projectPaths: string[]): Promise<DashboardWorktree[]> {
  // Deduplicate project paths (multiple sessions may share a project)
  const unique = [...new Set(projectPaths)];
  const gitRoots = new Set<string>();
  const allWorktrees: DashboardWorktree[] = [];

  for (const projectPath of unique) {
    try {
      // Find git root to avoid duplicate scans
      const { stdout: root } = await execAsync('git rev-parse --show-toplevel', {
        cwd: projectPath,
        timeout: 5000,
      });
      const gitRoot = root.trim();

      if (gitRoots.has(gitRoot)) continue;
      gitRoots.add(gitRoot);

      const { stdout } = await execAsync('git worktree list --porcelain', {
        cwd: gitRoot,
        timeout: 5000,
      });

      const worktrees = parsePorcelain(stdout, gitRoot);
      allWorktrees.push(...worktrees);
    } catch {
      // Not a git repo or git not available -- skip
    }
  }

  return allWorktrees;
}
