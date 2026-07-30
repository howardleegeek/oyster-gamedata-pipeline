/**
 * Tester HMAC authentication helper.
 *
 * **STUB_MODE FOR GAP #6 (Engineer B's branch — upstream)**
 *
 * Gap #6 ships the real HMAC token verification: tester_id + per-tester
 * shared secret + timestamp + signature in `X-Tester-Auth`. The recorder
 * embeds the secret at install time; the server verifies the HMAC against
 * `testers.auth_secret`. Until Engineer B's branch merges, this module
 * exposes the same surface so Gap #8 (signed-URL upload split) can call
 * `verifyTesterAuth(req)` from both routes without coupling.
 *
 * Contract Engineer B must keep:
 *   verifyTesterAuth(req: NextRequest, requiredTesterId?: string)
 *     -> Promise<{ ok: true; tester_id: string } | { ok: false; reason: string; status: number }>
 *
 * Behaviour of this stub_mode:
 *   - When `TESTER_AUTH_HMAC_SECRET` is unset, we ALLOW the call but stamp
 *     `stub_mode: true` on the result and warn-log so the stub_mode is
 *     visible in Vercel logs. This unblocks Gap #8 dev/preview without
 *     blocking on the parallel Gap #6 branch.
 *   - When the secret IS set, we perform a real timing-safe HMAC verify
 *     (`sha256(timestamp + tester_id + body_sha)` keyed by the per-tester
 *     secret) using the format that Gap #6's recorder branch is shipping.
 *     If Engineer B lands a different scheme, replace `verifyHmac()` below
 *     and update the recorder client in `bin/upload_tarball_signed.py` to
 *     match.
 *
 * Iron-law: this file MUST NOT be a no-op when the secret is configured.
 * If the secret exists, an invalid header is a hard 401. Howard 2026-05-13.
 */

import type { NextRequest } from 'next/server';
import crypto from 'node:crypto';
import { log } from './log';

export interface TesterAuthOk {
  ok: true;
  tester_id: string;
  /** True when the HMAC check was skipped because no shared secret is configured.
   *  Routes can refuse to proceed (e.g. in prod) by checking this field. */
  stub_mode: boolean;
}

export interface TesterAuthErr {
  ok: false;
  reason: string;
  status: number;
}

export type TesterAuthResult = TesterAuthOk | TesterAuthErr;

const HEADER_NAME = 'x-tester-auth';
/** Reject HMAC timestamps older than 5 minutes — prevents replay. */
const MAX_SKEW_MS = 5 * 60_000;

function constantTimeEqHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  try {
    return crypto.timingSafeEqual(Buffer.from(a, 'hex'), Buffer.from(b, 'hex'));
  } catch {
    return false;
  }
}

function verifyHmac(
  headerValue: string,
  secret: string,
  bodySha: string,
): { ok: true; tester_id: string } | { ok: false; reason: string } {
  // Header format (Gap #6 contract): v1 <tester_id> <unix_ts_ms> <hex_signature>
  const parts = headerValue.trim().split(/\s+/);
  if (parts.length !== 4 || parts[0] !== 'v1') {
    return { ok: false, reason: 'X-Tester-Auth must be "v1 <tester_id> <ts_ms> <hex_sig>"' };
  }
  const [, tester_id, tsRaw, sig] = parts;
  const ts = Number.parseInt(tsRaw!, 10);
  if (!Number.isFinite(ts)) return { ok: false, reason: 'timestamp not integer' };
  if (Math.abs(Date.now() - ts) > MAX_SKEW_MS) return { ok: false, reason: 'timestamp skew >5min' };

  const expected = crypto
    .createHmac('sha256', secret)
    .update(`${tester_id}\n${ts}\n${bodySha}`)
    .digest('hex');
  if (!constantTimeEqHex(sig!, expected)) return { ok: false, reason: 'signature mismatch' };
  return { ok: true, tester_id: tester_id! };
}

/**
 * Verify the X-Tester-Auth header for a request.
 *
 * @param req           NextRequest
 * @param bodySha       sha256 hex of the canonical body bytes (sign/finalize each
 *                      have their own canonical body — see callers)
 * @param requiredTesterId  if set, additionally enforce that the HMAC's tester_id matches
 */
export async function verifyTesterAuth(
  req: NextRequest,
  bodySha: string,
  requiredTesterId?: string,
): Promise<TesterAuthResult> {
  const secret = process.env.TESTER_AUTH_HMAC_SECRET ?? '';
  const header = req.headers.get(HEADER_NAME);

  // StubMode branch — Gap #6 not yet deployed.
  if (!secret) {
    if (!header) {
      // No header AND no secret: accept but log loudly. requiredTesterId must
      // come from somewhere; the caller's zod-validated body is the source.
      log.warn('tester_auth.stub_mode.no_header', {
        path: req.nextUrl.pathname,
        hint: 'set TESTER_AUTH_HMAC_SECRET to enable real HMAC verification (gap #6)',
      });
      return {
        ok: true,
        tester_id: requiredTesterId ?? 'unknown',
        stub_mode: true,
      };
    }
    // Recorder already speaks the new protocol — best-effort parse, still stub_mode.
    const m = header.trim().split(/\s+/);
    const guessed = m.length >= 2 && m[0] === 'v1' ? m[1]! : (requiredTesterId ?? 'unknown');
    log.warn('tester_auth.stub_mode.header_present', {
      path: req.nextUrl.pathname,
      tester_id: guessed,
    });
    return { ok: true, tester_id: guessed, stub_mode: true };
  }

  // Real verification — secret is configured.
  if (!header) {
    return { ok: false, reason: 'missing X-Tester-Auth header', status: 401 };
  }
  const result = verifyHmac(header, secret, bodySha);
  if (!result.ok) {
    return { ok: false, reason: result.reason, status: 401 };
  }
  if (requiredTesterId && result.tester_id !== requiredTesterId) {
    return {
      ok: false,
      reason: `HMAC tester_id (${result.tester_id}) does not match body tester_id (${requiredTesterId})`,
      status: 403,
    };
  }
  return { ok: true, tester_id: result.tester_id, stub_mode: false };
}

/** Helper exposed so callers don't have to import node:crypto themselves. */
export function sha256Hex(bytes: Buffer | string): string {
  return crypto.createHash('sha256').update(bytes).digest('hex');
}
