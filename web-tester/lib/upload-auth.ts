/**
 * HMAC-based upload authentication for /api/upload-tarball.
 *
 * Production gap #6 (PRODUCTION_GAPS.md): prior to this module, /api/upload-tarball
 * accepted any well-formed tester UUID without proof that the caller was that
 * tester. Anyone who learned or guessed a UUID could POST junk attributed to
 * the tester (charged per-hour up to the rate-limit ceiling).
 *
 * Scheme:
 *   1. Server holds `UPLOAD_HMAC_SECRET` (≥32 random bytes hex) — server-only,
 *      never shipped to client, never committed.
 *   2. Per-tester token: `token = HMAC_SHA256(secret, tester_id)` → 64 hex chars.
 *      A 16-hex-char prefix is also accepted (for embedding in filenames).
 *   3. Recorder includes `X-Upload-Token: <token>` on POST /api/upload-tarball.
 *   4. Server recomputes the expected token from `tester_id` and verifies with
 *      `crypto.timingSafeEqual` (constant-time compare, no timing oracle).
 *
 * Gating (`UPLOAD_REQUIRE_TOKEN`):
 *   - `true` (default, 2026-05-13): missing/invalid tokens return 401. This is
 *     the safe-by-default mode — a fresh deploy of web-tester rejects
 *     anonymous uploads. The fix for QA1 finding #2 (BUG-15): previously the
 *     default was `false`, which meant a fresh `vercel deploy` of web-tester
 *     still accepted anonymous uploads and defeated the whole gap-#6 fix
 *     until an operator flipped the flag manually.
 *   - `false`: explicit operator opt-out for legacy v0.26.x deploys that
 *     haven't rolled out HMAC-aware recorder builds yet. Missing/invalid
 *     tokens are logged but the request proceeds. Use only during migration.
 *
 * Iron-law: when `UPLOAD_HMAC_SECRET` is unset, this module hard-fails
 * (`unconfigured` outcome) — operators must set both `UPLOAD_HMAC_SECRET` and
 * `UPLOAD_REQUIRE_TOKEN` (or explicitly opt out). The route handler maps the
 * `unconfigured` outcome to 503 (matching the iron-law pattern used by
 * `isSupabaseConfigured`).
 */

import crypto from 'node:crypto';

/** Length of the truncated token embedded in the .exe filename. */
export const TOKEN_PREFIX_LEN = 16;

/** Length of the full token (sha256 hex digest = 64). */
export const TOKEN_FULL_LEN = 64;

/** HTTP header carrying the upload token. */
export const UPLOAD_TOKEN_HEADER = 'x-upload-token';

export interface UploadAuthConfig {
  /** Server-side HMAC secret. Empty string = unconfigured (warn-only). */
  secret: string;
  /** If true, missing/invalid tokens reject with 401. */
  requireToken: boolean;
}

/**
 * Read the upload-auth config from process.env. Pulled out as a function (not
 * a top-level const) so test code can mutate env between cases.
 *
 * QA1 finding #2 fix (BUG-15): `UPLOAD_REQUIRE_TOKEN` defaults to `true`. A
 * fresh deploy with `UPLOAD_HMAC_SECRET` set but no explicit `UPLOAD_REQUIRE_TOKEN`
 * now enforces the HMAC gate. Operators must explicitly set
 * `UPLOAD_REQUIRE_TOKEN=false` to opt OUT during the v0.26.x migration window.
 */
export function getUploadAuthConfig(): UploadAuthConfig {
  const raw = (process.env.UPLOAD_REQUIRE_TOKEN ?? '').toLowerCase().trim();
  // Default to TRUE (fail-closed). Explicit opt-out: 'false' / '0' / 'no'.
  const requireToken = !(raw === 'false' || raw === '0' || raw === 'no');
  return {
    secret: process.env.UPLOAD_HMAC_SECRET ?? '',
    requireToken,
  };
}

/**
 * Startup-time consistency check. Returns a list of operator-facing warnings.
 * Intended to be called once during route bootstrap (or in a `next dev`/`next build`
 * hook). The route handler also calls `assertConfigCoherent()` per-request to fail
 * fast on a misconfigured deploy.
 *
 * The fail-fast cases:
 *   - `UPLOAD_REQUIRE_TOKEN=true` (the default) AND `UPLOAD_HMAC_SECRET` unset:
 *     this is a misconfiguration — the gate is on but no secret exists to
 *     verify against. We MUST refuse uploads (503) rather than silently
 *     accepting them as "unauthorized but log-only".
 */
export function describeConfigIssues(cfg: UploadAuthConfig = getUploadAuthConfig()): string[] {
  const issues: string[] = [];
  if (cfg.requireToken && !cfg.secret) {
    issues.push(
      'UPLOAD_REQUIRE_TOKEN=true but UPLOAD_HMAC_SECRET is unset. ' +
        'Generate a 32-byte hex secret (e.g. `openssl rand -hex 32`) and set ' +
        '`UPLOAD_HMAC_SECRET` in the web-tester deploy env. To opt OUT of the ' +
        'gate during migration, set `UPLOAD_REQUIRE_TOKEN=false` explicitly.',
    );
  }
  return issues;
}

/**
 * Compute the full HMAC-SHA256 token for a tester_id under the given secret.
 *
 * @returns 64-char lowercase hex string.
 * @throws Error if secret is empty (caller must check `isHmacConfigured` first).
 */
export function computeToken(testerId: string, secret: string): string {
  if (!secret) {
    throw new Error('UPLOAD_HMAC_SECRET is not configured');
  }
  return crypto.createHmac('sha256', secret).update(testerId).digest('hex');
}

/**
 * Compute the 16-char prefix token suitable for embedding in the .exe
 * filename. Truncating a SHA256 HMAC to 16 hex chars (64 bits) is sufficient
 * for our threat model: an attacker would need ~2^32 online attempts to
 * forge, which the per-IP and per-tester rate limiters block long before.
 */
export function computeTokenPrefix(testerId: string, secret: string): string {
  return computeToken(testerId, secret).slice(0, TOKEN_PREFIX_LEN);
}

/**
 * Verify a presented token against the expected token for `testerId`.
 *
 * Accepts both the 16-char prefix (for tokens embedded in filenames) and
 * the full 64-char digest. Uses `crypto.timingSafeEqual` to avoid timing
 * oracle attacks.
 *
 * @returns true if the token matches, false otherwise.
 */
export function verifyToken(
  testerId: string,
  presentedToken: string,
  secret: string
): boolean {
  if (!secret || !presentedToken) return false;

  // Normalize. The header arrives as a string; lowercase to be lenient about
  // case (callers may upper-case for readability in filenames).
  const presented = presentedToken.trim().toLowerCase();

  // Length must be exactly 16 (prefix) or 64 (full digest). Reject everything
  // else early — keeps the timing-safe compare on a fixed-size buffer.
  if (presented.length !== TOKEN_PREFIX_LEN && presented.length !== TOKEN_FULL_LEN) {
    return false;
  }

  // Hex characters only.
  if (!/^[a-f0-9]+$/.test(presented)) return false;

  const expectedFull = computeToken(testerId, secret);
  const expected =
    presented.length === TOKEN_PREFIX_LEN
      ? expectedFull.slice(0, TOKEN_PREFIX_LEN)
      : expectedFull;

  // Both strings are guaranteed equal length at this point, so the
  // timing-safe compare won't throw.
  const a = Buffer.from(presented, 'utf8');
  const b = Buffer.from(expected, 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

/** Returns true when the server has a non-empty `UPLOAD_HMAC_SECRET`. */
export function isHmacConfigured(): boolean {
  return Boolean(process.env.UPLOAD_HMAC_SECRET);
}

export type AuthOutcome =
  /** Token present and verified. */
  | { kind: 'ok' }
  /**
   * HMAC isn't configured (`UPLOAD_HMAC_SECRET` empty).
   *
   * - When `requireToken=true` (the new default): operator misconfig — the
   *   gate is on but no secret exists. Route MUST return 503 (matches the
   *   iron-law pattern in `isSupabaseConfigured`).
   * - When `requireToken=false` (explicit opt-out): legacy v0.26.x mode.
   *   Route logs and proceeds. This is the only path that preserves the
   *   pre-Gap-#6 "warn-only" behaviour.
   */
  | { kind: 'unconfigured'; requireToken: boolean }
  /**
   * Token missing or wrong. If `requireToken` is true, the route MUST reject
   * with 401. If false, the route logs and proceeds.
   */
  | { kind: 'unauthorized'; reason: 'missing' | 'invalid' };

/**
 * Single entry-point used by route handlers. Pulls the header off `headers`,
 * decides what to do under the current config, and returns a discriminated
 * union. The route handler interprets `unconfigured` and `unauthorized` per
 * its own logging + status conventions.
 *
 * QA1 finding #2 fix (BUG-15): when `cfg.secret` is empty, the outcome now
 * carries `requireToken` so the route handler can fail-fast with a 503 on
 * the default deploy (where `requireToken=true`) rather than silently
 * accepting anonymous uploads.
 */
export function authenticateUpload(
  testerId: string,
  headers: Headers,
  cfg: UploadAuthConfig = getUploadAuthConfig()
): AuthOutcome {
  // HMAC unconfigured. Surface the `requireToken` flag so the caller can
  // decide between "fail-fast 503" (the default) and "warn-only" (explicit
  // legacy opt-out via `UPLOAD_REQUIRE_TOKEN=false`).
  if (!cfg.secret) {
    return { kind: 'unconfigured', requireToken: cfg.requireToken };
  }

  const presented = headers.get(UPLOAD_TOKEN_HEADER);
  if (!presented) {
    return { kind: 'unauthorized', reason: 'missing' };
  }

  if (!verifyToken(testerId, presented, cfg.secret)) {
    return { kind: 'unauthorized', reason: 'invalid' };
  }

  return { kind: 'ok' };
}
