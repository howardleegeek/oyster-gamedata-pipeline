/**
 * POST /api/upload-tarball
 *
 * Accepts a multipart/form-data upload from the OysterRecorder client:
 *   - field `tester_id`        : UUID of the tester (from the .exe filename)
 *   - field `duration_seconds` : integer, billable duration of the recording
 *   - field `sha256`           : optional, recorder-computed sha256 (we verify)
 *   - field `tarball`          : the file blob (binary)
 *
 * Behaviour:
 *   1. Verify SHA-256 server-side.
 *   2. Stash the tarball in Supabase Storage (bucket: `tarballs`) keyed
 *      by `<tester_id>/<sha256>.tar.gz`.
 *   3. Insert a row into `tarballs` (tester_id, sha256, size_bytes, ...).
 *   4. Return { id, sha256, accepted } so the recorder can mark the file shipped.
 *
 * Howard 2026-05-07 IRON-LAW: returns 503 when Supabase isn't configured.
 * The previous DEV MODE branch wrote tarballs to a local fallback dir and
 * returned a synthetic UUID + dev-mode success response — that fooled
 * the recorder into thinking the upload had landed. No more.
 */

import { NextRequest, NextResponse } from 'next/server';
import crypto from 'node:crypto';
import { z } from 'zod';
import { getSupabaseServiceClient } from '../../../lib/supabase-server';
import { env, isSupabaseConfigured } from '../../../lib/env';
import { checkRateLimit, clientIpFromHeaders } from '../../../lib/rate-limit';
import { log } from '../../../lib/log';
import {
  authenticateUpload,
  getUploadAuthConfig,
} from '../../../lib/upload-auth';

export const runtime = 'nodejs'; // need Buffer / fs / crypto
export const maxDuration = 300; // long uploads OK
export const dynamic = 'force-dynamic';

const FormFields = z.object({
  tester_id: z.string().uuid(),
  duration_seconds: z.coerce.number().int().min(1).max(60 * 60 * 12), // ≤ 12h
  sha256: z.string().regex(/^[a-f0-9]{64}$/i).optional(),
});

const MAX_BYTES = 1024 * 1024 * 1024; // 1 GiB hard ceiling

// Rate limits per the production runbook. A normal recorder uploads
// once per session (~1-3h gameplay), so even 30/hour/tester is generous;
// 12/min/IP catches obvious flooding without blocking dorm/lab IPs that
// host multiple legitimate testers.
const RL_PER_IP_LIMIT = 12;
const RL_PER_IP_WINDOW_MS = 60_000;
const RL_PER_TESTER_LIMIT = 30;
const RL_PER_TESTER_WINDOW_MS = 60 * 60_000;

function rateLimitResponse(label: string, info: ReturnType<typeof checkRateLimit>) {
  return NextResponse.json(
    {
      error: 'Rate limit exceeded',
      scope: label,
      limit: info.limit,
      remaining: info.remaining,
      resetAt: new Date(info.resetAt).toISOString(),
    },
    {
      status: 429,
      headers: {
        'Retry-After': String(Math.max(1, Math.ceil((info.resetAt - Date.now()) / 1000))),
        'X-RateLimit-Limit': String(info.limit),
        'X-RateLimit-Remaining': String(info.remaining),
        'X-RateLimit-Reset': String(Math.floor(info.resetAt / 1000)),
      },
    }
  );
}

export async function POST(req: NextRequest) {
  const startedAt = Date.now();
  const ip = clientIpFromHeaders(req.headers);

  // ---- IP-level rate limit (cheapest gate first, before form parsing) ---
  const ipRl = checkRateLimit(`ip:${ip}`, RL_PER_IP_LIMIT, RL_PER_IP_WINDOW_MS);
  if (!ipRl.allowed) {
    log.warn('upload.rate_limited', { ip, scope: 'ip', limit: ipRl.limit });
    return rateLimitResponse('ip', ipRl);
  }

  // Howard 2026-05-07 IRON-LAW: hard-gate before any storage work.
  if (!isSupabaseConfigured()) {
    log.error('upload.not_configured', { ip });
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY'],
        details:
          'Tarball ingestion writes to Supabase Storage + the tarballs table. ' +
          'No local fallback — configure Supabase before pointing the recorder at this endpoint.',
      },
      { status: 503 },
    );
  }

  let form: FormData;
  try {
    form = await req.formData();
  } catch (err) {
    return NextResponse.json({ error: 'Could not parse multipart body' }, { status: 400 });
  }

  // ---- validate fields --------------------------------------------------
  const parsed = FormFields.safeParse({
    tester_id: form.get('tester_id'),
    duration_seconds: form.get('duration_seconds'),
    sha256: form.get('sha256') ?? undefined,
  });
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid form fields', details: parsed.error.flatten() },
      { status: 400 }
    );
  }
  const { tester_id, duration_seconds } = parsed.data;
  let { sha256: clientSha } = parsed.data;

  // ---- per-tester rate limit (after we know the tester_id is well-formed)
  const testerRl = checkRateLimit(
    `tester:${tester_id}`,
    RL_PER_TESTER_LIMIT,
    RL_PER_TESTER_WINDOW_MS
  );
  if (!testerRl.allowed) {
    log.warn('upload.rate_limited', { ip, tester_id, scope: 'tester', limit: testerRl.limit });
    return rateLimitResponse('tester', testerRl);
  }

  // ---- HMAC token gate (production gap #6) -----------------------------
  // Verifies that the caller possesses the per-tester HMAC token issued at
  // download time.
  //
  // QA1 finding #2 fix (BUG-15): the gate is now ENFORCED BY DEFAULT.
  //   - `UPLOAD_REQUIRE_TOKEN` defaults to `true`.
  //   - When `requireToken=true` AND `UPLOAD_HMAC_SECRET` is unset, the route
  //     returns 503 (iron-law: a misconfigured deploy MUST NOT silently
  //     accept anonymous uploads). Operators must either set the secret or
  //     explicitly opt-out with `UPLOAD_REQUIRE_TOKEN=false` (legacy mode).
  const authCfg = getUploadAuthConfig();
  const auth = authenticateUpload(tester_id, req.headers, authCfg);
  if (auth.kind === 'unconfigured') {
    if (auth.requireToken) {
      // Fail-fast: default deploy is enforcing the gate but no secret to
      // verify against. Surface a 503 with a clear operator-facing message.
      log.warn('upload.auth_misconfigured', {
        ip,
        tester_id,
        detail: 'UPLOAD_REQUIRE_TOKEN=true but UPLOAD_HMAC_SECRET unset',
      });
      return NextResponse.json(
        {
          error: 'Service Unavailable',
          details:
            'Upload authentication is enabled but the server is not configured. ' +
            'Operator: set UPLOAD_HMAC_SECRET (32 random hex bytes) in the web-tester ' +
            'deploy env, or set UPLOAD_REQUIRE_TOKEN=false to opt out during the ' +
            'v0.26.x migration window.',
          envVars: ['UPLOAD_HMAC_SECRET'],
        },
        { status: 503 },
      );
    }
    // Explicit legacy opt-out — log loudly but proceed.
    log.warn('upload.auth_unconfigured', { ip, tester_id, legacyOptOut: true });
  } else if (auth.kind === 'unauthorized') {
    log.warn('upload.auth_failed', {
      ip,
      tester_id,
      reason: auth.reason,
      enforced: authCfg.requireToken,
    });
    if (authCfg.requireToken) {
      return NextResponse.json(
        {
          error: 'Unauthorized',
          details:
            auth.reason === 'missing'
              ? 'Missing X-Upload-Token header. Recorder must include the HMAC token issued at download time.'
              : 'Invalid X-Upload-Token. The token did not match the expected HMAC for this tester_id.',
        },
        { status: 401 }
      );
    }
    // else: log-only fallback — proceed.
  }

  const file = form.get('tarball');
  if (!(file instanceof Blob)) {
    return NextResponse.json({ error: 'Missing field: tarball (file)' }, { status: 400 });
  }
  if (file.size === 0) {
    return NextResponse.json({ error: 'Empty tarball' }, { status: 400 });
  }
  if (file.size > MAX_BYTES) {
    return NextResponse.json(
      { error: `Tarball too large (${file.size} > ${MAX_BYTES} bytes)` },
      { status: 413 }
    );
  }

  // ---- verify SHA-256 ---------------------------------------------------
  const buf = Buffer.from(await file.arrayBuffer());
  const sha = crypto.createHash('sha256').update(buf).digest('hex');
  if (clientSha && clientSha.toLowerCase() !== sha) {
    return NextResponse.json(
      { error: 'sha256 mismatch', expected: clientSha, computed: sha },
      { status: 422 }
    );
  }
  clientSha = sha;

  const storagePath = `${tester_id}/${sha}.tar.gz`;

  // ---- LIVE: Supabase storage + DB row ---------------------------------
  const supabase = getSupabaseServiceClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Service client unavailable' }, { status: 500 });
  }

  // 1. Upload (idempotent — `upsert: false` causes a duplicate to error).
  const { error: storageErr } = await supabase.storage
    .from(env.tarballBucket)
    .upload(storagePath, buf, {
      contentType: 'application/gzip',
      cacheControl: '31536000',
      upsert: false,
    });
  if (storageErr && !storageErr.message.toLowerCase().includes('already exists')) {
    return NextResponse.json(
      { error: 'Storage upload failed', details: storageErr.message },
      { status: 502 }
    );
  }

  // 2. Insert tarball row. Unique index on sha256 makes this idempotent.
  const { data: row, error: insertErr } = await supabase
    .from('tarballs')
    .insert({
      tester_id,
      size_bytes: file.size,
      sha256: sha,
      duration_seconds,
      d5_verdict: 'pending',
      storage_path: storagePath,
    })
    .select('id, tester_id, uploaded_at, sha256, size_bytes, d5_verdict')
    .single();

  if (insertErr) {
    // Duplicate sha256 → return the existing row so the recorder treats it as success.
    if (insertErr.code === '23505') {
      const { data: existing } = await supabase
        .from('tarballs')
        .select('id, tester_id, uploaded_at, sha256, size_bytes, d5_verdict')
        .eq('sha256', sha)
        .single();
      log.info('upload.duplicate', {
        ip,
        tester_id,
        sha256: sha,
        bytes: file.size,
        latency_ms: Date.now() - startedAt,
      });
      return NextResponse.json({ ...(existing ?? {}), duplicate: true });
    }
    log.error('upload.db_insert_failed', { ip, tester_id, sha256: sha, code: insertErr.code });
    return NextResponse.json(
      { error: 'DB insert failed', details: insertErr.message },
      { status: 500 }
    );
  }

  log.info('upload.accepted', {
    ip,
    tester_id,
    sha256: sha,
    bytes: file.size,
    duration_seconds,
    latency_ms: Date.now() - startedAt,
  });
  return NextResponse.json({ ...row, accepted: true });
}

export async function GET() {
  const cfg = getUploadAuthConfig();
  return NextResponse.json(
    {
      endpoint: '/api/upload-tarball',
      method: 'POST',
      content_type: 'multipart/form-data',
      fields: ['tester_id', 'duration_seconds', 'sha256?', 'tarball'],
      headers: ['X-Upload-Token'],
      max_bytes: MAX_BYTES,
      auth: {
        hmac_configured: Boolean(cfg.secret),
        // True when the server will 401 on missing/invalid tokens.
        // Recorders should call /api/tester/auth (or read the token embedded
        // in the .exe filename) to populate X-Upload-Token before POST.
        token_required: cfg.requireToken && Boolean(cfg.secret),
      },
    },
    { status: 200 }
  );
}
