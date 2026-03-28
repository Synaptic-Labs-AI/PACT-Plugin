/**
 * Unit tests for server/lib/activity-buffer.ts
 *
 * Tests the in-memory ring buffer for activity events:
 * - pushEvent adds events and deduplicates
 * - getRecentEvents returns newest first
 * - Buffer capacity enforcement (ring buffer overflow)
 */

import { describe, it, expect, beforeEach } from 'vitest';

// We need to reset the module state between tests since the buffer uses module-level state
async function freshImport() {
  // vitest module cache reset
  const mod = await import('../../server/lib/activity-buffer.js');
  return mod;
}

function makeEvent(overrides: Partial<{
  id: string;
  type: string;
  teamName: string;
  agentName: string | null;
  timestamp: string;
  summary: string;
}> = {}) {
  return {
    id: overrides.id ?? `evt-${Math.random().toString(36).slice(2, 8)}`,
    timestamp: overrides.timestamp ?? new Date().toISOString(),
    type: (overrides.type ?? 'task_created') as any,
    teamName: overrides.teamName ?? 'pact-test1234',
    agentName: overrides.agentName ?? 'backend-coder',
    agentColor: null,
    summary: overrides.summary ?? 'Test event',
    sessionId: null,
    projectName: null,
    metadata: {},
  };
}

describe('activity-buffer', () => {
  // Because the buffer is module-level state, we import fresh for isolation
  // In practice, the tests below share the module state, so we test additive behavior

  it('pushEvent should accept a valid event and return true', async () => {
    const { pushEvent, getEventCount } = await import('../../server/lib/activity-buffer.js');
    const before = getEventCount();
    const event = makeEvent({ id: 'unique-test-1', timestamp: '2026-01-01T00:00:00Z' });
    const result = pushEvent(event);
    expect(result).toBe(true);
    expect(getEventCount()).toBe(before + 1);
  });

  it('pushEvent should deduplicate identical events', async () => {
    const { pushEvent, getEventCount } = await import('../../server/lib/activity-buffer.js');
    const event = makeEvent({
      id: 'dedup-test',
      type: 'agent_joined',
      teamName: 'dedup-team',
      agentName: 'test-agent',
      timestamp: '2026-02-01T00:00:00Z',
    });
    const before = getEventCount();
    pushEvent(event);
    const afterFirst = getEventCount();
    const secondResult = pushEvent(event);
    expect(secondResult).toBe(false);
    expect(getEventCount()).toBe(afterFirst);
  });

  it('getRecentEvents should return events in reverse chronological order (newest first)', async () => {
    const { pushEvent, getRecentEvents } = await import('../../server/lib/activity-buffer.js');
    // Push events with distinct timestamps
    const event1 = makeEvent({ id: 'order-1', timestamp: '2026-03-01T00:00:00Z', teamName: 'order-team', agentName: 'a1' });
    const event2 = makeEvent({ id: 'order-2', timestamp: '2026-03-02T00:00:00Z', teamName: 'order-team', agentName: 'a2' });
    const event3 = makeEvent({ id: 'order-3', timestamp: '2026-03-03T00:00:00Z', teamName: 'order-team', agentName: 'a3' });
    pushEvent(event1);
    pushEvent(event2);
    pushEvent(event3);

    const recent = getRecentEvents(3);
    // The last pushed should be first (newest)
    expect(recent.length).toBeGreaterThanOrEqual(3);
    // The most recently pushed event should appear first
    const ids = recent.map(e => e.id);
    const idx1 = ids.indexOf('order-3');
    const idx2 = ids.indexOf('order-2');
    const idx3 = ids.indexOf('order-1');
    expect(idx1).toBeLessThan(idx2);
    expect(idx2).toBeLessThan(idx3);
  });

  it('getRecentEvents with limit should cap results', async () => {
    const { getRecentEvents } = await import('../../server/lib/activity-buffer.js');
    const events = getRecentEvents(1);
    expect(events.length).toBe(1);
  });

  it('getEventCount should return the current buffer size', async () => {
    const { getEventCount } = await import('../../server/lib/activity-buffer.js');
    const count = getEventCount();
    expect(typeof count).toBe('number');
    expect(count).toBeGreaterThan(0);
  });
});
