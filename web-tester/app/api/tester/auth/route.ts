/**
 * GET /api/tester/auth
 *
 * Issues an HMAC upload token for the signed-in tester. Used by:
 *   - The dashboard (so the tester can copy/paste a fresh token into a
 *     recorder config if their .exe-embedded token was lost).
 *   - Trusted server-side jobs that present the service-role header
 *     plus a `tester_id` query param.
 *
 * Authn model (mirrors /api/stats/[testerId]):
 *   - Signed-in user → token issued for `user.id` only.
 *   - Service-role header `x-supabase-service-role` matching
 *     SUPABASE_SERVICE_ROLE_KEY → token issued for the `tester_id` query.
 *
 * Iron-law: never echo the HMAC secret. We return ONLY the derived token
 * + a `prefix` (16 chars, what gets embedded in the .exe filename).
 *
 * When `UPLOAD_HMAC_SECRET` is unset, returns 503 so operators can't
 * mistakenly hand out "empty" tokens that wouldn't verify anyway.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { getSupabaseServerClient } from '../../../../lib/supabase-server';
import { isSupabaseConfigured } from '../../../../lib/env';
import {
  computeToken,
  computeTokenPrefix,
  getUploadAuthConfig,
  isHmacConfigured,
} from '../../../../lib/upload-auth';
import { log } from '../../../../lib/log';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const TesterIdSchema = z.string().uuid();

export async function GET(req: NextRequest) {
  if (!isHmacConfigured()) {
    log.warn('tester_auth.unconfigured', {});
    return NextResponse.json(
      {
        error: 'Upload HMAC not configured',
        envVars: ['UPLOAD_HMAC_SECRET'],
        details:
          'Set UPLOAD_HMAC_SECRET (≥32 random bytes hex) on the server. ' +
          'See PRODUCTION_LAUNCH_SOP.md → "Upload HMAC token setup".',
      },
      { status: 503 }
    );
  }

  // Service-role short-circuit (trusted server jobs).
  const adminHeader = req.headers.get('x-supabase-service-role') ?? '';
  if (
    adminHeader &&
    adminHeader === process.env.SUPABASE_SERVICE_ROLE_KEY
  ) {
    const requested = req.nextUrl.searchParams.get('tester_id') ?? '';
    const parsed = TesterIdSchema.safeParse(requested);
    if (!parsed.success) {
      return NextResponse.json(
        { error: 'Invalid or missing tester_id query param' },
        { status: 400 }
      );
    }
    return tokenResponse(parsed.data);
  }

  // Otherwise: must be signed in, can only issue for self.
  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY'],
      },
      { status: 503 }
    );
  }

  const supabase = getSupabaseServerClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase server client unavailable' }, { status: 500 });
  }
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: 'Not signed in' }, { status: 401 });
  }

  const parsed = TesterIdSchema.safeParse(user.id);
  if (!parsed.success) {
    // Auth user IDs in Supabase are UUIDs by construction — this would
    // only fire on a corrupt session.
    return NextResponse.json({ error: 'Session user id is not a UUID' }, { status: 500 });
  }

  return tokenResponse(parsed.data);
}

function tokenResponse(testerId: string) {
  const { secret } = getUploadAuthConfig();
  const token = computeToken(testerId, secret);
  const prefix = computeTokenPrefix(testerId, secret);

  log.info('tester_auth.issued', { tester_id: testerId, prefix });

  return NextResponse.json(
    {
      tester_id: testerId,
      // Full 64-hex-char token. Send as `X-Upload-Token` header.
      token,
      // 16-char prefix, what gets embedded in the .exe filename.
      prefix,
      header_name: 'X-Upload-Token',
    },
    {
      status: 200,
      headers: {
        // Tokens are stable per tester+secret pair — but mark no-store so
        // they don't end up cached on shared proxies.
        'Cache-Control': 'no-store',
      },
    }
  );
}
