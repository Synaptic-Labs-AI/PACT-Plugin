/**
 * server/lib/pid.ts
 *
 * PID liveness check utility. Uses signal 0 (existence check)
 * which does not actually send a signal to the process.
 */

export function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
