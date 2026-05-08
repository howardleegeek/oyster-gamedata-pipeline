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

export const runtime = 'nodejs'; // need Buffer / fs / crypto
export const maxDuration = 300; // long uploads OK
export const dynamic = 'force-dynamic';

const FormFields = z.object({
  tester_id: z.string().uuid(),
  duration_seconds: z.coerce.number().int().min(1).max(60 * 60 * 12), // ≤ 12h
  sha256: z.string().regex(/^[a-f0-9]{64}$/i).optional(),
});

const MAX_BYTES = 1024 * 1024 * 1024; // 1 GiB hard ceiling

export async function POST(req: NextRequest) {
  // Howard 2026-05-07 IRON-LAW: hard-gate before any storage work.
  if (!isSupabaseConfigured()) {
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
      return NextResponse.json({ ...(existing ?? {}), duplicate: true });
    }
    return NextResponse.json(
      { error: 'DB insert failed', details: insertErr.message },
      { status: 500 }
    );
  }

  return NextResponse.json({ ...row, accepted: true });
}

export async function GET() {
  return NextResponse.json(
    {
      endpoint: '/api/upload-tarball',
      method: 'POST',
      content_type: 'multipart/form-data',
      fields: ['tester_id', 'duration_seconds', 'sha256?', 'tarball'],
      max_bytes: MAX_BYTES,
    },
    { status: 200 }
  );
}
