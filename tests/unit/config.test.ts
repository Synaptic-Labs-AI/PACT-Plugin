/**
 * Unit tests for server/config.ts
 *
 * Tests configuration values are correct and paths are well-formed.
 */

import { describe, it, expect } from 'vitest';
import { homedir } from 'node:os';
import { join } from 'node:path';
import {
  CLAUDE_DIR,
  PATHS,
  SERVER_PORT,
  SERVER_HOST,
  CACHE_TTL,
  ACTIVITY_BUFFER_SIZE,
  PACT_TEAM_PATTERN,
} from '../../server/config.js';

describe('config', () => {
  it('CLAUDE_DIR should point to ~/.claude', () => {
    expect(CLAUDE_DIR).toBe(join(homedir(), '.claude'));
  });

  it('PATHS should include all expected data source paths', () => {
    expect(PATHS.sessions).toContain('.claude/sessions');
    expect(PATHS.teams).toContain('.claude/teams');
    expect(PATHS.tasks).toContain('.claude/tasks');
    expect(PATHS.pactMemoryDb).toContain('.claude/pact-memory/memory.db');
    expect(PATHS.agentMemory).toContain('.claude/agent-memory');
    expect(PATHS.telegram).toContain('.claude/pact-telegram/coordinator');
  });

  it('SERVER_PORT should default to 3001', () => {
    expect(SERVER_PORT).toBe(3001);
  });

  it('SERVER_HOST should be localhost only', () => {
    expect(SERVER_HOST).toBe('127.0.0.1');
  });

  it('CACHE_TTL values should all be positive numbers', () => {
    for (const [key, value] of Object.entries(CACHE_TTL)) {
      expect(value, `CACHE_TTL.${key}`).toBeGreaterThan(0);
    }
  });

  it('ACTIVITY_BUFFER_SIZE should be a reasonable number', () => {
    expect(ACTIVITY_BUFFER_SIZE).toBeGreaterThanOrEqual(100);
    expect(ACTIVITY_BUFFER_SIZE).toBeLessThanOrEqual(10000);
  });

  describe('PACT_TEAM_PATTERN', () => {
    it('should match pact-{8 hex chars}', () => {
      expect(PACT_TEAM_PATTERN.test('pact-7d44f1d3')).toBe(true);
      expect(PACT_TEAM_PATTERN.test('pact-AABBCCDD')).toBe(true);
      expect(PACT_TEAM_PATTERN.test('pact-00000000')).toBe(true);
    });

    it('should not match non-pact team names', () => {
      expect(PACT_TEAM_PATTERN.test('team-alpha')).toBe(false);
      expect(PACT_TEAM_PATTERN.test('pact-short')).toBe(false);
      expect(PACT_TEAM_PATTERN.test('pact-toolonghash1')).toBe(false);
      expect(PACT_TEAM_PATTERN.test('')).toBe(false);
    });
  });
});
