/**
 * Unit tests for server/lib/validate-params.ts
 *
 * Tests the path traversal prevention logic:
 * - Valid team names accepted
 * - Path traversal patterns rejected
 * - Boundary conditions (empty, very long)
 */

import { describe, it, expect } from 'vitest';
import { isValidTeamName } from '../../server/lib/validate-params.js';

describe('isValidTeamName', () => {
  describe('valid team names', () => {
    it.each([
      ['pact-7d44f1d3', 'pact team hash format'],
      ['pact-AABBCCDD', 'pact team hash uppercase'],
      ['agile-swinging-shore', 'friendly team name with hyphens'],
      ['team_alpha', 'team name with underscores'],
      ['a1b2c3d4-e5f6-7890-abcd-ef1234567890', 'UUID format'],
      ['simple', 'simple alphanumeric'],
      ['Test.Name', 'name with dot'],
    ])('should accept %s (%s)', (name) => {
      expect(isValidTeamName(name)).toBe(true);
    });
  });

  describe('path traversal attempts', () => {
    it.each([
      ['..', 'parent directory'],
      ['../etc/passwd', 'relative path traversal'],
      ['..\\windows\\system32', 'Windows path traversal'],
      ['pact-../secret', 'embedded traversal'],
      ['team/../../etc', 'nested path traversal'],
      ['valid..name', 'embedded double dot'],
    ])('should reject %s (%s)', (name) => {
      expect(isValidTeamName(name)).toBe(false);
    });
  });

  describe('injection patterns', () => {
    it.each([
      ['team/name', 'forward slash'],
      ['team\\name', 'backslash'],
      ['/absolute/path', 'absolute path'],
    ])('should reject %s (%s)', (name) => {
      expect(isValidTeamName(name)).toBe(false);
    });
  });

  describe('boundary conditions', () => {
    it('should reject empty string', () => {
      expect(isValidTeamName('')).toBe(false);
    });

    it('should reject names exceeding 128 characters', () => {
      const longName = 'a'.repeat(129);
      expect(isValidTeamName(longName)).toBe(false);
    });

    it('should accept names at exactly 128 characters', () => {
      const maxName = 'a'.repeat(128);
      expect(isValidTeamName(maxName)).toBe(true);
    });

    it('should reject names starting with a dot', () => {
      expect(isValidTeamName('.hidden')).toBe(false);
    });

    it('should reject names starting with a hyphen', () => {
      expect(isValidTeamName('-invalid')).toBe(false);
    });
  });
});
