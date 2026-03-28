/**
 * server/lib/cache.ts
 *
 * TTL-based in-memory cache wrapper around node-cache.
 * Scanners are stateless; caching is done at the route level.
 */

import NodeCache from 'node-cache';

const cache = new NodeCache({ checkperiod: 5 });

/**
 * Get a cached value or compute it via the loader function.
 * The loader is called when the cache key has expired or is missing.
 */
export async function cached<T>(
  key: string,
  ttlSeconds: number,
  loader: () => T | Promise<T>,
): Promise<T> {
  const existing = cache.get<T>(key);
  if (existing !== undefined) {
    return existing;
  }
  const value = await loader();
  cache.set(key, value, ttlSeconds);
  return value;
}

/** Manually invalidate a cache key. */
export function invalidate(key: string): void {
  cache.del(key);
}

/** Flush all cached data. */
export function flushAll(): void {
  cache.flushAll();
}
