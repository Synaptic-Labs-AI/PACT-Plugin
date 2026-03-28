/**
 * server/lib/activity-buffer.ts
 *
 * In-memory ring buffer for activity events. Stores the last N events
 * and deduplicates by composite key to prevent duplicates from re-scanning.
 */

import type { ActivityEvent } from './types.js';
import { ACTIVITY_BUFFER_SIZE } from '../config.js';

const events: ActivityEvent[] = [];
const seenKeys = new Set<string>();

function makeKey(event: ActivityEvent): string {
  return `${event.type}-${event.teamName}-${event.agentName ?? ''}-${event.timestamp}`;
}

/** Add an event to the buffer if not already seen. */
export function pushEvent(event: ActivityEvent): boolean {
  const key = makeKey(event);
  if (seenKeys.has(key)) {
    return false;
  }

  events.push(event);
  seenKeys.add(key);

  // Trim oldest events if over capacity
  while (events.length > ACTIVITY_BUFFER_SIZE) {
    const removed = events.shift();
    if (removed) {
      seenKeys.delete(makeKey(removed));
    }
  }

  return true;
}

/** Get the most recent N events, newest first. */
export function getRecentEvents(limit: number): ActivityEvent[] {
  const start = Math.max(0, events.length - limit);
  return events.slice(start).reverse();
}

/** Get total event count. */
export function getEventCount(): number {
  return events.length;
}
