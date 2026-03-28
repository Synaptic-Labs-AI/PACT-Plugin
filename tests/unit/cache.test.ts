/**
 * Unit tests for server/lib/cache.ts
 *
 * Tests the TTL-based in-memory cache:
 * - cached() returns loader result on miss
 * - cached() returns stored value on hit
 * - invalidate() clears a specific key
 * - flushAll() clears everything
 * - TTL expiry works
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { cached, invalidate, flushAll } from '../../server/lib/cache.js';

describe('cache', () => {
  beforeEach(() => {
    flushAll();
  });

  it('should call loader on cache miss and return its result', async () => {
    const loader = vi.fn().mockResolvedValue({ data: 'hello' });
    const result = await cached('test-key-1', 60, loader);
    expect(result).toEqual({ data: 'hello' });
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('should return cached value on subsequent calls (cache hit)', async () => {
    const loader = vi.fn().mockResolvedValue({ data: 'cached' });
    await cached('test-key-2', 60, loader);
    const result2 = await cached('test-key-2', 60, loader);
    expect(result2).toEqual({ data: 'cached' });
    // Loader should only be called once (first miss)
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('should handle synchronous loaders', async () => {
    const loader = vi.fn().mockReturnValue(42);
    const result = await cached('test-key-sync', 60, loader);
    expect(result).toBe(42);
  });

  it('invalidate() should clear a specific key', async () => {
    const loader = vi.fn().mockResolvedValue('first');
    await cached('test-key-inv', 60, loader);

    invalidate('test-key-inv');

    loader.mockResolvedValue('second');
    const result = await cached('test-key-inv', 60, loader);
    expect(result).toBe('second');
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('flushAll() should clear all cached values', async () => {
    const loader1 = vi.fn().mockResolvedValue('a');
    const loader2 = vi.fn().mockResolvedValue('b');
    await cached('flush-1', 60, loader1);
    await cached('flush-2', 60, loader2);

    flushAll();

    loader1.mockResolvedValue('a2');
    loader2.mockResolvedValue('b2');
    expect(await cached('flush-1', 60, loader1)).toBe('a2');
    expect(await cached('flush-2', 60, loader2)).toBe('b2');
    expect(loader1).toHaveBeenCalledTimes(2);
    expect(loader2).toHaveBeenCalledTimes(2);
  });

  it('should expire cached values after TTL', async () => {
    const loader = vi.fn().mockResolvedValue('initial');
    await cached('ttl-key', 1, loader); // 1-second TTL

    // Wait for TTL to expire
    await new Promise((r) => setTimeout(r, 1200));

    loader.mockResolvedValue('refreshed');
    const result = await cached('ttl-key', 1, loader);
    expect(result).toBe('refreshed');
    expect(loader).toHaveBeenCalledTimes(2);
  });
});
