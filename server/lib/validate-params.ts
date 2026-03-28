/**
 * server/lib/validate-params.ts
 *
 * Validates URL parameters against path traversal and injection patterns.
 * Per architecture doc section 10: "Team names and file paths are validated
 * against expected patterns before filesystem access."
 */

/**
 * Valid team name pattern: alphanumeric, hyphens, and underscores only.
 * Matches pact-{hash}, UUIDs, and friendly names like "agile-swinging-shore".
 * Rejects "..", "/", "\", and other path traversal characters.
 */
const SAFE_NAME_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;

export function isValidTeamName(name: string): boolean {
  if (!name || name.length > 128) return false;
  if (name.includes('..') || name.includes('/') || name.includes('\\')) return false;
  return SAFE_NAME_PATTERN.test(name);
}
