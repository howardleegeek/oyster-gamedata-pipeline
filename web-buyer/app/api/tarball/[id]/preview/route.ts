/**
 * GET /api/tarball/[id]/preview
 *
 * Returns the first 100 records of action_camera.json for the given tarball
 * plus the URL of the poster frame. This endpoint is intentionally
 * low-friction — no auth required, so prospective buyers can inspect
 * sample data before paying.
 *
 * Behaviour:
 *   - DEV MODE: uses the deterministic sample in lib/sample-data.
 *   - LIVE   : in a future iteration this will pull `action_camera_preview`
 *              materialised at ingest time. For now we still return the
 *              deterministic sample so the response shape and TTL match.
 */

import { NextRequest, NextResponse } from 'next/server';
import { fetchCatalogById } from '../../../../../lib/catalog';
import { sampleActionCameraRecords } from '../../../../../lib/sample-data';

export const dynamic = 'force-dynamic';

export async function GET(
  _req: NextRequest,
  { params }: { params: { id: string } },
) {
  const { row, liveMode } = await fetchCatalogById(params.id);
  if (!row) {
    return NextResponse.json({ error: 'Tarball not found' }, { status: 404 });
  }

  const records = sampleActionCameraRecords(row.id, 100);

  return NextResponse.json(
    {
      mode: liveMode ? 'live' : 'dev_sample',
      tarball_id: row.id,
      title: row.title,
      poster_url: row.poster_url,
      video_preview_url: row.video_preview_url,
      action_camera_records: records,
      record_count: records.length,
      total_records_in_tarball:
        // 20 Hz capture × duration → estimated total record count
        row.duration_seconds * 20,
    },
    {
      headers: {
        // Public preview — cache for an hour at the CDN.
        'Cache-Control': 'public, max-age=60, s-maxage=3600',
      },
    },
  );
}
