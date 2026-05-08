/**
 * GET /api/tarball/[id]/preview
 *
 * Returns the first 100 records of action_camera.json for the given tarball
 * plus the URL of the poster frame. This endpoint is intentionally
 * low-friction — no auth required, so prospective buyers can inspect
 * sample data before paying.
 *
 * Howard 2026-05-07 IRON-LAW: returns 503 when Supabase is unconfigured,
 * 404 when the tarball has no real action_camera_preview blob. The
 * previous implementation returned `sampleActionCameraRecords()` (a
 * deterministic fabricated mouse-trail) for BOTH dev and live mode —
 * meaning every preview shipped fake data. No more.
 */

import { NextRequest, NextResponse } from 'next/server';
import { fetchCatalogById, CatalogNotConfiguredError } from '../../../../../lib/catalog';
import { getSupabaseServiceClient } from '../../../../../lib/supabase-server';
import { isSupabaseConfigured, env } from '../../../../../lib/env';

export const dynamic = 'force-dynamic';

interface ActionCameraRecord {
  ts_ms: number;
  cam_yaw_deg: number;
  cam_pitch_deg: number;
  player_x: number;
  player_y: number;
  player_z: number;
  mouse_dx: number;
  mouse_dy: number;
  keys_held: string[];
}

export async function GET(
  _req: NextRequest,
  { params }: { params: { id: string } },
) {
  // Howard 2026-05-07 IRON-LAW: hard-gate. No fabricated preview.
  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY'],
      },
      { status: 503 },
    );
  }

  let row: Awaited<ReturnType<typeof fetchCatalogById>>['row'];
  try {
    const result = await fetchCatalogById(params.id);
    row = result.row;
  } catch (err) {
    if (err instanceof CatalogNotConfiguredError) {
      return NextResponse.json(
        { error: err.message, envVars: err.envVars },
        { status: 503 },
      );
    }
    return NextResponse.json(
      { error: 'Catalog read failed', details: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }

  if (!row) {
    return NextResponse.json({ error: 'Tarball not found' }, { status: 404 });
  }

  // Try to load real preview records from the action_camera_preview
  // bucket (materialised at ingest time as the first N records of the
  // tarball's action_camera.jsonl). If it's missing, return 404 with a
  // remediation message — never fabricate.
  const service = getSupabaseServiceClient();
  if (!service) {
    return NextResponse.json(
      { error: 'Service client unavailable' },
      { status: 500 },
    );
  }

  const previewPath = `${row.id}/action_camera_preview.jsonl`;
  const { data: blob, error: dlErr } = await service.storage
    .from(env.tarballBucket)
    .download(previewPath);

  if (dlErr || !blob) {
    return NextResponse.json(
      {
        error: 'Preview not yet generated for this tarball',
        details:
          'The ingest pipeline materialises ' +
          `${previewPath} as the first 100 records of action_camera.jsonl. ` +
          'Re-run the preview job (bin/regenerate_action_camera_preview.py) ' +
          'or wait for the next scheduled batch.',
        tarball_id: row.id,
      },
      { status: 404 },
    );
  }

  const text = await blob.text();
  const records: ActionCameraRecord[] = text
    .split('\n')
    .filter((l) => l.trim().length > 0)
    .slice(0, 100)
    .map((l) => JSON.parse(l));

  return NextResponse.json(
    {
      mode: 'live',
      tarball_id: row.id,
      title: row.title,
      poster_url: row.poster_url,
      video_preview_url: row.video_preview_url,
      action_camera_records: records,
      record_count: records.length,
      total_records_in_tarball: row.duration_seconds * 20,
    },
    {
      headers: {
        // Public preview — cache for an hour at the CDN.
        'Cache-Control': 'public, max-age=60, s-maxage=3600',
      },
    },
  );
}
