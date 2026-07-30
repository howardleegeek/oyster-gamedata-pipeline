/**
 * POST /api/upload-tarball/finalize
 *
 * Step 3 of the direct-to-Supabase upload protocol (Gap #8).
 *
 * Called by the recorder after it has PUT the tarball binary directly to
 * Supabase Storage via the URL returned by /api/upload-tarball/sign. We
 * verify the object actually landed, that its size matches what the
 * recorder claimed at sign-time, and that its sha256 matches.
 *
 * sha256 verification policy:
 *   - The signed PUT URL is keyed on `<tester_id>/<sha256>.tar.gz`, so a
 *     mismatch between "what the recorder uploaded" and "what it claimed
 *     in sign" would require the recorder to have lied in step 1 about
 *     the hash. Detecting this server-side requires downloading the file,
 *     which defeats the whole point of direct-to-Supabase. We therefore
 *     trust the *path* (sha256 from sign) as the source of truth and
 *     reject any finalize whose `sha256` field doesn't equal the path
 *     hash. Server-side rehash is left as a follow-up task (the worker
 *     that runs D5 grading already streams the file — bolt it on there).
 *   - We DO verify object size via Supabase Storage's HEAD call. A mismatch
 *     means the recorder gave us a fake size_bytes in sign, OR the upload
 *     was truncated. Either way, hard reject.
 *
 * Request body (JSON):
 *   {
 *     "tarball_id": "<uuid returned by sign>",
 *     "sha256":     "<must match the sign-time sha256>"
 *   }
 *
 * Response (200):
 *   { "id":..., "tester_id":..., "uploaded_at":..., "sha256":..., "size_bytes":..., "d5_verdict":..., "accepted": true }
 *
 * Errors:
 *   400 malformed body
 *   401/403 HMAC failures
 *   404 tarball_id not found
 *   409 finalize called but Storage object missing / wrong size / already finalized
 *   422 sha256 in body doesn't match the sha256 on the reserved row
 *   503 Supabase not configured
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

const RL_PER_IP_LIMIT = 30;
const RL_PER_IP_WINDOW_MS = 60_000;

const FinalizeBody = z.object({
  tarball_id: z.string().uuid(),
  sha256: z
    .string()
    .regex(/^[a-f0-9]{64}$/i, 'sha256 must be 64 lowercase hex chars')
    .transform((s) => s.toLowerCase()),
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
      },
    },
  );
}

/**
 * Probe Supabase Storage for the object and return its size in bytes, or
 * null if it doesn't exist. We use `list({ search })` against the parent
 * folder because the SDK doesn't expose a direct HEAD; storage-py users
 * call `info()` but the JS SDK forces us through list.
 *
 * Typed against the public SDK surface; we treat `metadata` as an unknown
 * record because Supabase has shipped two shapes over the SDK's lifetime
 * (object meta vs flat .size).
 */
interface StorageListEntry {
  name: string;
  metadata?: { size?: number } | null;
  size?: number;
}
type StorageLike = {
  from(bucket: string): {
    list(
      dir: string,
      opts: { limit: number; search: string },
    ): Promise<{ data: StorageListEntry[] | null; error: { message: string } | null }>;
  };
};

async function getObjectSizeBytes(
  storage: StorageLike,
  bucket: string,
  storagePath: string,
): Promise<number | null> {
  const slash = storagePath.lastIndexOf('/');
  const dir = slash >= 0 ? storagePath.slice(0, slash) : '';
  const file = slash >= 0 ? storagePath.slice(slash + 1) : storagePath;
  const { data, error } = await storage
    .from(bucket)
    .list(dir, { limit: 1, search: file });
  if (error) {
    log.error('finalize.storage_list_failed', {
      bucket,
      storagePath,
      details: error.message,
    });
    return null;
  }
  if (!Array.isArray(data) || data.length === 0) return null;
  const match = data.find((d) => d.name === file);
  if (!match) return null;
  // Supabase storage objects store size inside `metadata.size` on list output.
  // Some SDK versions surface it directly as a flat `.size`; fall back to null
  // if absent — caller treats null as "size unknown" and rejects.
  if (match.metadata && typeof match.metadata.size === 'number') return match.metadata.size;
  if (typeof match.size === 'number') return match.size;
  return null;
}

export async function POST(req: NextRequest) {
  const startedAt = Date.now();
  const ip = clientIpFromHeaders(req.headers);

  const ipRl = checkRateLimit(`finalize:ip:${ip}`, RL_PER_IP_LIMIT, RL_PER_IP_WINDOW_MS);
  if (!ipRl.allowed) {
    log.warn('finalize.rate_limited', { ip, scope: 'ip', limit: ipRl.limit });
    return rateLimitResponse('ip', ipRl);
  }

  if (!isSupabaseConfigured()) {
    log.error('finalize.not_configured', { ip });
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: [
          'NEXT_PUBLIC_SUPABASE_URL',
          'NEXT_PUBLIC_SUPABASE_ANON_KEY',
          'SUPABASE_SERVICE_ROLE_KEY',
        ],
      },
      { status: 503 },
    );
  }

  const rawBody = await req.text();
  let parsedBody: unknown;
  try {
    parsedBody = JSON.parse(rawBody);
  } catch {
    return NextResponse.json({ error: 'Body must be JSON' }, { status: 400 });
  }
  const parsed = FinalizeBody.safeParse(parsedBody);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid body', details: parsed.error.flatten() },
      { status: 400 },
    );
  }
  const { tarball_id, sha256 } = parsed.data;

  const supabase = getSupabaseServiceClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Service client unavailable' }, { status: 500 });
  }

  // ---- 1. Look up the reserved row ------------------------------------
  const { data: row, error: lookupErr } = await supabase
    .from('tarballs')
    .select(
      'id, tester_id, sha256, size_bytes, duration_seconds, storage_bucket, storage_path, upload_status, signed_url_expires_at, uploaded_at, d5_verdict',
    )
    .eq('id', tarball_id)
    .single();
  if (lookupErr || !row) {
    return NextResponse.json({ error: 'tarball_id not found' }, { status: 404 });
  }

  // ---- 2. HMAC auth — body sha bound to tester_id from the row --------
  const bodyHash = sha256Hex(rawBody);
  const auth = await verifyTesterAuth(req, bodyHash, row.tester_id);
  if (!auth.ok) {
    log.warn('finalize.auth_rejected', {
      ip,
      tester_id: row.tester_id,
      tarball_id,
      reason: auth.reason,
    });
    return NextResponse.json({ error: auth.reason }, { status: auth.status });
  }

  // ---- 3. sha256 in body must match the path / sign-time claim --------
  if (row.sha256.toLowerCase() !== sha256) {
    log.warn('finalize.sha256_mismatch', {
      ip,
      tarball_id,
      tester_id: row.tester_id,
      claimed_at_sign: row.sha256,
      claimed_at_finalize: sha256,
    });
    return NextResponse.json(
      {
        error: 'sha256 mismatch between sign and finalize',
        expected: row.sha256,
        received: sha256,
      },
      { status: 422 },
    );
  }

  // ---- 4. Idempotency: already finalized -------------------------------
  if (row.upload_status === 'uploaded') {
    log.info('finalize.already_finalized', { ip, tester_id: row.tester_id, tarball_id });
    return NextResponse.json({
      id: row.id,
      tester_id: row.tester_id,
      uploaded_at: row.uploaded_at,
      sha256: row.sha256,
      size_bytes: row.size_bytes,
      d5_verdict: row.d5_verdict,
      duplicate: true,
      accepted: true,
    });
  }
  if (row.upload_status === 'failed') {
    return NextResponse.json(
      { error: 'This upload was previously marked failed — request a new signed URL' },
      { status: 409 },
    );
  }

  // ---- 5. Verify the object actually landed in Storage -----------------
  const objSize = await getObjectSizeBytes(
    supabase.storage,
    row.storage_bucket,
    row.storage_path,
  );
  if (objSize === null) {
    log.warn('finalize.object_missing', {
      ip,
      tester_id: row.tester_id,
      tarball_id,
      bucket: row.storage_bucket,
      path: row.storage_path,
    });
    return NextResponse.json(
      {
        error: 'No object found at storage path — did the PUT complete?',
        bucket: row.storage_bucket,
        path: row.storage_path,
      },
      { status: 409 },
    );
  }
  if (objSize !== row.size_bytes) {
    log.warn('finalize.size_mismatch', {
      ip,
      tester_id: row.tester_id,
      tarball_id,
      expected: row.size_bytes,
      actual: objSize,
    });
    return NextResponse.json(
      {
        error: 'Storage object size does not match sign-time size_bytes',
        expected: row.size_bytes,
        actual: objSize,
      },
      { status: 409 },
    );
  }

  // ---- 6. Flip the row to 'uploaded' (single round-trip) ---------------
  const { data: updated, error: updateErr } = await supabase
    .from('tarballs')
    .update({
      upload_status: 'uploaded',
      uploaded_at: new Date().toISOString(),
      signed_url_expires_at: null,
    })
    .eq('id', tarball_id)
    .eq('upload_status', 'pending_upload') // concurrency guard
    .select('id, tester_id, uploaded_at, sha256, size_bytes, d5_verdict')
    .single();

  if (updateErr || !updated) {
    log.error('finalize.db_update_failed', {
      ip,
      tester_id: row.tester_id,
      tarball_id,
      details: updateErr?.message,
    });
    return NextResponse.json(
      { error: 'Failed to mark upload as finalized', details: updateErr?.message },
      { status: 500 },
    );
  }

  log.info('finalize.accepted', {
    ip,
    tester_id: updated.tester_id,
    tarball_id,
    sha256,
    bytes: updated.size_bytes,
    stub_mode_auth: auth.stub_mode,
    latency_ms: Date.now() - startedAt,
  });

  return NextResponse.json({ ...updated, accepted: true });
}

export async function GET() {
  return NextResponse.json(
    {
      endpoint: '/api/upload-tarball/finalize',
      method: 'POST',
      content_type: 'application/json',
      fields: ['tarball_id', 'sha256'],
      auth: 'X-Tester-Auth: v1 <tester_id> <ts_ms> <hex_sha256_hmac> (gap #6)',
      prereq: 'Must have POSTed /api/upload-tarball/sign and then PUT the binary to signed_url',
    },
    { status: 200 },
  );
}
