/**
 * Constant-time string comparison.
 *
 * SECURITY (fix #02, 2026-05-13): admin / service-role / token comparisons
 * must NOT use JavaScript `===` because that operator short-circuits at the
 * first byte mismatch, leaking the matching-prefix length through wall-clock
 * timing. Repeated probing across HTTP RTT can recover a 64-char secret in
 * ~4096 measurements (Crosby & Wallach 2009; Cloudflare edge timing research).
 *
 * `crypto.timingSafeEqual` enforces data-independent runtime, but it throws
 * when the two Buffers differ in length. We pre-check length (NOT secret —
 * leaking length of `expected` is acceptable; the secret content is what we
 * protect) and only then call the constant-time primitive.
 *
 * Mirrors the pattern used by lib/upload-auth.ts (Gap #6, PR #4). Reference
 * test: tests/security/test_finding_02_service_role_timing_oracle.py.
 */

import crypto from 'node:crypto';

/**
 * Compare two strings in constant time.
 *
 * Returns false (without timing leak) when either argument is falsy or
 * lengths differ. Returns the constant-time equality otherwise.
 *
 * @param a — value presented by the caller (untrusted)
 * @param b — value loaded from server-side env (the secret)
 */
export function safeEqual(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const ab = Buffer.from(a, 'utf8');
  const bb = Buffer.from(b, 'utf8');
  if (ab.length !== bb.length) return false;
  return crypto.timingSafeEqual(ab, bb);
}
