/**
 * server/scanners/agent-memory-scanner.ts
 *
 * Reads ~/.claude/agent-memory/{specialist_name}/ directories.
 * Each specialist has a MEMORY.md index and individual .md memory files.
 * Returns summary stats per specialist, not full memory contents.
 */

import { readdir, readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { PATHS } from '../config.js';
import type { AgentMemorySummary } from '../lib/types.js';

export async function scanAgentMemories(): Promise<AgentMemorySummary[]> {
  let dirs: string[];
  try {
    dirs = await readdir(PATHS.agentMemory);
  } catch {
    return [];
  }

  const summaries: AgentMemorySummary[] = [];

  for (const dir of dirs) {
    const dirPath = join(PATHS.agentMemory, dir);

    try {
      const dirStat = await stat(dirPath);
      if (!dirStat.isDirectory()) continue;

      const files = await readdir(dirPath);
      const mdFiles = files.filter((f) => f.endsWith('.md'));

      // Parse MEMORY.md index if it exists
      let indexEntries: string[] = [];
      try {
        const indexContent = await readFile(join(dirPath, 'MEMORY.md'), 'utf-8');
        indexEntries = indexContent
          .split('\n')
          .filter((line) => line.startsWith('- '))
          .map((line) => line.slice(2).trim());
      } catch {
        // No MEMORY.md index
      }

      // Find most recent modification
      let lastModified = new Date(0);
      for (const file of mdFiles) {
        try {
          const fileStat = await stat(join(dirPath, file));
          if (fileStat.mtime > lastModified) {
            lastModified = fileStat.mtime;
          }
        } catch {
          // Skip inaccessible files
        }
      }

      summaries.push({
        specialistName: dir,
        fileCount: mdFiles.length,
        memoryIndexEntries: indexEntries,
        lastModified: lastModified.toISOString(),
      });
    } catch {
      // Skip inaccessible directories
    }
  }

  return summaries;
}
