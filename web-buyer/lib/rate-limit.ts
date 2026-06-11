/**
 * In-memory leaky-bucket rate limiter for production endpoints.
 *
 * Howard 2026-05-08: Stage-1 production rate limiting. State lives in a
 * module-level Map, so each Vercel serverless instance has its own bucket.
 * That's an acceptable soft limit — the worst case is N×instance-count
 * effective requests/min, which is still bounded.
 *
 * For tighter enforcement (cross-instance global cap, sliding window with
 * sub-second precision), upgrade to Upstash / Redis later. The route
 * handler interface stays the same.
 *
 * Iron-law: no fakes here. checkRateLimit() returns honest counters that
 * the route handler surfaces in the 429 response so the client (recorder)
 * can back off intelligently.
 */

interface BucketState {
  count: number;
  resetAt: number; // unix ms when the window rolls over
}

interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  resetAt: number;
  limit: number;
}

// Module-level state — survives across requests within one serverless instance.
const buckets: Map<string, BucketState> = new Map();

// Soft GC: when the map crosses this size, evict expired entries.
const GC_THRESHOLD = 10_000;

function maybeGc(now: number): void {
  if (buckets.size < GC_THRESHOLD) return;
  for (const [k, v] of buckets) {
    if (v.resetAt <= now) buckets.delete(k);
  }
}

/**
 * Check whether `key` is allowed to make a request right now.
 *
 * @param key      — opaque bucket identifier (e.g. `ip:1.2.3.4` or `tester:UUID`)
 * @param limit    — max requests permitted in the window
 * @param windowMs — window length in milliseconds
 */
export function checkRateLimit(
  key: string,
  limit: number,
  windowMs: number
): RateLimitResult {
  const now = Date.now();
  maybeGc(now);

  const cur = buckets.get(key);
  if (!cur || cur.resetAt <= now) {
    buckets.set(key, { count: 1, resetAt: now + windowMs });
    return { allowed: true, remaining: limit - 1, resetAt: now + windowMs, limit };
  }

  if (cur.count >= limit) {
    return { allowed: false, remaining: 0, resetAt: cur.resetAt, limit };
  }

  cur.count += 1;
  return { allowed: true, remaining: limit - cur.count, resetAt: cur.resetAt, limit };
}

/**
 * Best-effort client IP extraction. Vercel sets x-forwarded-for as a chain;
 * the leftmost entry is the original client.
 */
export function clientIpFromHeaders(headers: Headers): string {
  const xff = headers.get('x-forwarded-for');
  if (xff) return xff.split(',')[0]!.trim();
  const real = headers.get('x-real-ip');
  if (real) return real.trim();
  return 'unknown';
}
