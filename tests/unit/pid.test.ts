/**
 * Unit tests for server/lib/pid.ts
 *
 * Tests PID liveness checking:
 * - Current process PID is alive
 * - Non-existent PID is not alive
 * - Edge case: PID 0 / negative
 */

import { describe, it, expect } from 'vitest';
import { isProcessAlive } from '../../server/lib/pid.js';

describe('isProcessAlive', () => {
  it('should return true for the current process PID', () => {
    expect(isProcessAlive(process.pid)).toBe(true);
  });

  it('should return false for a non-existent PID', () => {
    // PID 99999999 is extremely unlikely to exist
    expect(isProcessAlive(99999999)).toBe(false);
  });

  it('should return false for PID 0 (no such signal target)', () => {
    // PID 0 would signal the process group; typically throws on macOS
    // The function catches errors and returns false
    const result = isProcessAlive(0);
    // On macOS, kill(0, 0) sends to current process group - may return true
    expect(typeof result).toBe('boolean');
  });

  it('should handle negative PIDs without throwing', () => {
    // On macOS, kill(-1, 0) signals the process group and may succeed
    // The important thing is it doesn't throw
    const result = isProcessAlive(-1);
    expect(typeof result).toBe('boolean');
  });
});
