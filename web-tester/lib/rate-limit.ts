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
 * Best-effort client IP extraction.
 *
 * SECURITY (fix #03, 2026-05-13): on Vercel we MUST read
 * `x-vercel-forwarded-for` first — it is set by Vercel's edge after stripping
 * any client-supplied value and is therefore not spoofable. Reading the
 * leftmost `x-forwarded-for` entry directly lets an attacker rotate that
 * header per request and fragment the rate-limit bucket arbitrarily
 * (Finding #03 in SECURITY_AUDIT_2026_05_13.md, reproducer at
 * tests/security/test_finding_08_xff_spoofing_ratelimit_bypass.py).
 *
 * Fallback order (intentionally conservative):
 *   1. `x-vercel-forwarded-for` — Vercel signed header, single IP, prod path.
 *   2. `x-forwarded-for` rightmost element — for local-dev / non-Vercel
 *      reverse proxies. Rightmost is the address the LAST trusted proxy
 *      appended; leftmost is whatever the client claimed.
 *   3. `x-real-ip` — common nginx convention for local dev.
 *   4. `'unknown'` — fail closed by sharing a single bucket; this is a
 *      bigger cost to attackers than a unique-per-request bucket.
 *
 * NOTE: `req.ip` (NextRequest) is not used here because `Headers` is all we
 * accept by signature. Callers that have a NextRequest can read `req.ip`
 * as a final fallback before this function returns 'unknown'.
 */
export function clientIpFromHeaders(headers: Headers): string {
  // 1. Vercel's non-spoofable signed header (production path).
  const vercel = headers.get('x-vercel-forwarded-for');
  if (vercel) {
    // Vercel's header is a single client IP, not a chain.
    const v = vercel.split(',')[0]!.trim();
    if (v) return v;
  }
  // 2. Local dev / non-Vercel proxies: take the RIGHTMOST XFF element —
  // that's the IP appended by the last trusted hop, not the
  // client-controlled leading entry.
  const xff = headers.get('x-forwarded-for');
  if (xff) {
    const parts = xff.split(',').map((p) => p.trim()).filter(Boolean);
    if (parts.length) return parts[parts.length - 1]!;
  }
  const real = headers.get('x-real-ip');
  if (real) return real.trim();
  return 'unknown';
}
