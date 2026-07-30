/**
 * Legacy /api/upload-tarball — DEPRECATED 2026-05-13 (Gap #8).
 *
 * Why this returns 410 Gone:
 *   The legacy route accepted a multipart/form-data tarball POST and
 *   streamed the bytes through Next.js to Supabase Storage. On Vercel
 *   route handlers cap request bodies at 4.5 MB, so anything larger than
 *   a toy recording 413s before our handler runs. Every real Minecraft
 *   session tarball (500 MB–1.5 GB) was guaranteed to fail.
 *
 *   The fix is a three-call protocol that bypasses Vercel for the binary:
 *     1. POST /api/upload-tarball/sign   (small JSON, returns signed PUT URL)
 *     2. PUT  <signed_url>                (recorder -> Supabase directly)
 *     3. POST /api/upload-tarball/finalize (small JSON, server verifies)
 *
 *   We keep this path as a 410 Gone with explicit migration guidance so
 *   any field deploys of recorder <0.27.0 fail loudly and quickly instead
 *   of silently 413'ing.
 *
 * Howard 2026-05-13.
 */

import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const MIGRATION_BODY = {
  error: 'This endpoint is gone — upgrade the recorder to v0.27.0+',
  status: 410,
  reason:
    'Vercel route handlers cap request bodies at 4.5 MB, which silently 413s for any real session tarball. ' +
    'The replacement protocol bypasses Vercel by PUTting the tarball directly to Supabase Storage via a signed URL.',
  migration: {
    step_1: {
      method: 'POST',
      url: '/api/upload-tarball/sign',
      content_type: 'application/json',
      body: {
        tester_id: '<uuid>',
        filename: '<safe>.tar.gz',
        size_bytes: '<int, 1..1 GiB>',
        sha256: '<64 lowercase hex>',
        duration_seconds: '<int>',
      },
      auth: 'X-Tester-Auth: v1 <tester_id> <ts_ms> <hex_sha256_hmac>',
      returns: { signed_url: '...', tarball_id: '...', expires_at: '...' },
    },
    step_2: {
      method: 'PUT',
      url: '<signed_url from step 1>',
      content_type: 'application/gzip',
      headers: { 'x-upsert': 'true' },
      body: '<binary tarball bytes>',
    },
    step_3: {
      method: 'POST',
      url: '/api/upload-tarball/finalize',
      content_type: 'application/json',
      body: { tarball_id: '<from step 1>', sha256: '<must match sign>' },
      auth: 'X-Tester-Auth: v1 <tester_id> <ts_ms> <hex_sha256_hmac>',
    },
  },
  recorder_version_required: '0.27.0',
  docs: '/docs/SUBMISSION_FORMAT.md#direct-to-supabase-upload',
} as const;

export async function POST() {
  return NextResponse.json(MIGRATION_BODY, {
    status: 410,
    headers: { 'X-Deprecated': 'Gap-8-direct-to-supabase' },
  });
}

export async function PUT() {
  return NextResponse.json(MIGRATION_BODY, { status: 410 });
}

export async function GET() {
  return NextResponse.json(MIGRATION_BODY, { status: 410 });
}
