/**
 * POST /api/upload-tarball/sign
 *
 * Step 1 of the direct-to-Supabase upload protocol (Gap #8).
 *
 * Why this exists:
 *   Vercel route handlers cap request bodies at 4.5 MB. A real Minecraft
 *   session tarball is 500 MB–1.5 GB. The legacy /api/upload-tarball
 *   POST-the-whole-file flow trips this cap and 413s before the handler
 *   even runs. We split the protocol into three calls:
 *
 *     1. sign       — small JSON, returns a signed PUT URL
 *     2. (PUT)      — recorder uploads the tarball directly to Supabase
 *     3. finalize   — small JSON, server verifies + commits to DB
 *
 *   Vercel never sees the tarball bytes.
 *
 * Request body (JSON):
 *   {
 *     "tester_id":        "<uuid>",
 *     "filename":         "<basename>.tar.gz",
 *     "size_bytes":       <int, 1..1 GiB>,
 *     "sha256":           "<64 lowercase hex>",
 *     "duration_seconds": <int, 1..43200>
 *   }
 *
 * Response (200):
 *   {
 *     "tarball_id":          "<uuid>",
 *     "signed_url":          "https://<supabase>/storage/v1/.../sign?token=...",
 *     "signed_token":        "<jwt-ish token>",   // also returned for clients
 *                                                 //   that use it via the SDK
 *     "storage_bucket":      "tarball-uploads",
 *     "storage_path":        "<tester_id>/<sha256>.tar.gz",
 *     "expires_at":          "<ISO ts>",
 *     "max_bytes":           1073741824,
 *     "ttl_seconds":         900,
 *     "next_step": "PUT the binary to signed_url, then POST /api/upload-tarball/finalize"
 *   }
 *
 * Errors:
 *   400  malformed body
 *   401  missing/invalid X-Tester-Auth (Gap #6)
 *   403  HMAC tester_id mismatch
 *   409  this sha256 has already been uploaded by someone (replay attack)
 *   413  size_bytes > 1 GiB
 *   429  rate limited
 *   503  Supabase not configured (iron-law)
 *
 * Howard 2026-05-13.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { getSupabaseServiceClient } from '../../../../lib/supabase-server';
import { env, isSupabaseConfigured } from '../../../../lib/env';
import { checkRateLimit, clientIpFromHeaders } from '../../../../lib/rate-limit';
import { log } from '../../../../lib/log';
import { sha256Hex, verifyTesterAuth } from '../../../../lib/tester-auth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const MAX_BYTES = 1024 * 1024 * 1024; // 1 GiB hard ceiling

// Rate limits — same as the legacy route, but tighter on the per-IP knob
// because signing is cheap and easy to spam (no upload bandwidth required).
const RL_PER_IP_LIMIT = 30;
const RL_PER_IP_WINDOW_MS = 60_000;
const RL_PER_TESTER_LIMIT = 60;
const RL_PER_TESTER_WINDOW_MS = 60 * 60_000;

const SignBody = z.object({
  tester_id: z.string().uuid(),
  filename: z
    .string()
    .min(1)
    .max(200)
    .regex(/^[A-Za-z0-9._-]+\.tar\.gz$/, 'filename must be <safe>.tar.gz'),
  size_bytes: z.coerce.number().int().min(1).max(MAX_BYTES),
  sha256: z
    .string()
    .regex(/^[a-f0-9]{64}$/i, 'sha256 must be 64 lowercase hex chars')
    .transform((s) => s.toLowerCase()),
  duration_seconds: z.coerce.number().int().min(1).max(60 * 60 * 12),
});

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
    },
  );
}

export async function POST(req: NextRequest) {
  const startedAt = Date.now();
  const ip = clientIpFromHeaders(req.headers);

  // ---- IP-level rate limit (cheapest gate first) -----------------------
  const ipRl = checkRateLimit(`sign:ip:${ip}`, RL_PER_IP_LIMIT, RL_PER_IP_WINDOW_MS);
  if (!ipRl.allowed) {
    log.warn('sign.rate_limited', { ip, scope: 'ip', limit: ipRl.limit });
    return rateLimitResponse('ip', ipRl);
  }

  if (!isSupabaseConfigured()) {
    log.error('sign.not_configured', { ip });
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: [
          'NEXT_PUBLIC_SUPABASE_URL',
          'NEXT_PUBLIC_SUPABASE_ANON_KEY',
          'SUPABASE_SERVICE_ROLE_KEY',
        ],
        details:
          'Signed-URL minting requires service-role credentials. Configure Supabase before ' +
          'pointing the recorder at this endpoint.',
      },
      { status: 503 },
    );
  }

  // ---- parse + validate body ------------------------------------------
  const rawBody = await req.text();
  let parsedBody: unknown;
  try {
    parsedBody = JSON.parse(rawBody);
  } catch {
    return NextResponse.json({ error: 'Body must be JSON' }, { status: 400 });
  }
  const parsed = SignBody.safeParse(parsedBody);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid body', details: parsed.error.flatten() },
      { status: 400 },
    );
  }
  const { tester_id, filename, size_bytes, sha256, duration_seconds } = parsed.data;

  // ---- HMAC auth (Gap #6 — stub_mode until Engineer B's branch lands) -
  // The HMAC is computed over sha256(raw body bytes) so a single header
  // protects every field the client claims.
  const bodyHash = sha256Hex(rawBody);
  const auth = await verifyTesterAuth(req, bodyHash, tester_id);
  if (!auth.ok) {
    log.warn('sign.auth_rejected', { ip, tester_id, reason: auth.reason });
    return NextResponse.json({ error: auth.reason }, { status: auth.status });
  }

  // ---- per-tester rate limit (after we know the tester_id is valid) ----
  const testerRl = checkRateLimit(
    `sign:tester:${tester_id}`,
    RL_PER_TESTER_LIMIT,
    RL_PER_TESTER_WINDOW_MS,
  );
  if (!testerRl.allowed) {
    log.warn('sign.rate_limited', { ip, tester_id, scope: 'tester' });
    return rateLimitResponse('tester', testerRl);
  }

  const supabase = getSupabaseServiceClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Service client unavailable' }, { status: 500 });
  }

  // Storage path keys on sha256 so re-uploads of the same content are
  // deterministic — finalize can detect the duplicate without a race.
  const bucket = env.tarballUploadBucket;
  const storagePath = `${tester_id}/${sha256}.tar.gz`;
  const ttlSec = env.signedUploadUrlTtlSeconds;
  const expiresAt = new Date(Date.now() + ttlSec * 1000);

  // ---- 1. Reserve a tarballs row (idempotent on sha256) ----------------
  // We INSERT before minting the URL so a crash mid-flight leaves a
  // pending_upload row that the reaper can clean. createSignedUploadUrl is
  // idempotent at the storage layer; the DB row is the source of truth.
  const insertPayload = {
    tester_id,
    size_bytes,
    sha256,
    duration_seconds,
    storage_bucket: bucket,
    storage_path: storagePath,
    upload_status: 'pending_upload' as const,
    signed_url_expires_at: expiresAt.toISOString(),
    d5_verdict: 'pending' as const,
  };
  const { data: row, error: insertErr } = await supabase
    .from('tarballs')
    .insert(insertPayload)
    .select('id, tester_id, sha256, size_bytes, upload_status')
    .single();

  let tarball_id: string;
  if (insertErr) {
    if (insertErr.code === '23505') {
      // Duplicate sha256 — fetch the existing row.
      const { data: existing, error: lookupErr } = await supabase
        .from('tarballs')
        .select('id, tester_id, upload_status, sha256, size_bytes')
        .eq('sha256', sha256)
        .single();
      if (lookupErr || !existing) {
        log.error('sign.duplicate_lookup_failed', { ip, tester_id, sha256, code: lookupErr?.code });
        return NextResponse.json(
          { error: 'Duplicate sha256 but row lookup failed', details: lookupErr?.message },
          { status: 500 },
        );
      }
      // If it belongs to a different tester, this is a replay / collision attempt.
      if (existing.tester_id !== tester_id) {
        log.warn('sign.sha256_collision_other_tester', {
          ip,
          claimed_tester_id: tester_id,
          owner_tester_id: existing.tester_id,
          sha256,
        });
        return NextResponse.json(
          { error: 'sha256 already uploaded by a different tester' },
          { status: 409 },
        );
      }
      // Already uploaded? Tell the recorder to skip step 2.
      if (existing.upload_status === 'uploaded') {
        log.info('sign.already_uploaded', { ip, tester_id, sha256 });
        return NextResponse.json(
          {
            tarball_id: existing.id,
            already_uploaded: true,
            sha256,
            size_bytes: existing.size_bytes,
            next_step: 'POST /api/upload-tarball/finalize to confirm (no PUT needed)',
          },
          { status: 200 },
        );
      }
      // Otherwise re-issue the signed URL against the existing pending row.
      tarball_id = existing.id;
    } else {
      log.error('sign.db_insert_failed', { ip, tester_id, sha256, code: insertErr.code });
      return NextResponse.json(
        { error: 'DB insert failed', details: insertErr.message },
        { status: 500 },
      );
    }
  } else {
    tarball_id = row!.id;
  }

  // ---- 2. Mint the signed PUT URL --------------------------------------
  // Supabase's createSignedUploadUrl returns { token, path, signedUrl }.
  // Note: the SDK doesn't expose an explicit TTL parameter — Supabase Storage
  // pins the upload-token TTL at the bucket setting on the dashboard. We
  // still record `expires_at` in the DB so the reaper has a deadline; this
  // is correct as long as the bucket TTL >= ttlSec. The Supabase-side cap is
  // checked by the finalize endpoint regardless.
  const { data: signed, error: signErr } = await supabase.storage
    .from(bucket)
    .createSignedUploadUrl(storagePath);

  if (signErr || !signed) {
    log.error('sign.create_signed_url_failed', {
      ip,
      tester_id,
      sha256,
      details: signErr?.message,
    });
    // Roll the row back to 'failed' so a retry doesn't 409 us forever.
    await supabase
      .from('tarballs')
      .update({ upload_status: 'failed' })
      .eq('id', tarball_id);
    return NextResponse.json(
      { error: 'Could not mint signed upload URL', details: signErr?.message },
      { status: 502 },
    );
  }

  log.info('sign.issued', {
    ip,
    tester_id,
    tarball_id,
    sha256,
    size_bytes,
    bucket,
    ttl_seconds: ttlSec,
    stub_mode_auth: auth.stub_mode,
    latency_ms: Date.now() - startedAt,
  });

  return NextResponse.json(
    {
      tarball_id,
      signed_url: signed.signedUrl,
      signed_token: signed.token,
      storage_bucket: bucket,
      storage_path: signed.path,
      expires_at: expiresAt.toISOString(),
      max_bytes: MAX_BYTES,
      ttl_seconds: ttlSec,
      next_step:
        'PUT the tarball binary to signed_url with header "x-upsert: true" then POST /api/upload-tarball/finalize',
    },
    { status: 200 },
  );
}

export async function GET() {
  return NextResponse.json(
    {
      endpoint: '/api/upload-tarball/sign',
      method: 'POST',
      content_type: 'application/json',
      fields: ['tester_id', 'filename', 'size_bytes', 'sha256', 'duration_seconds'],
      auth: 'X-Tester-Auth: v1 <tester_id> <ts_ms> <hex_sha256_hmac> (gap #6)',
      next_step:
        'After signing, PUT the binary directly to signed_url, then POST /api/upload-tarball/finalize',
      max_bytes: MAX_BYTES,
    },
    { status: 200 },
  );
}
