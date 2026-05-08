/**
 * GET /api/catalog
 *
 * Returns the buyer-side catalog. Supports query-string filters:
 *   - verdict        : 'accepted' | 'pending' | 'rejected'  (default 'accepted')
 *   - task_type      : one of survival|creative|redstone|pvp|mining|...
 *   - min_size_bytes : integer
 *   - max_size_bytes : integer
 *   - min_price_cents: integer
 *   - max_price_cents: integer
 *   - limit          : 1..200 (default 100)
 *
 * Responses:
 *   200 { mode: 'live'|'dev_sample', count, rows }
 *   400 { error: '...', details? }
 */

import { NextRequest, NextResponse } from 'next/server';
import { fetchCatalog, FilterSchema } from '../../../lib/catalog';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const raw = Object.fromEntries(url.searchParams.entries());
  const parsed = FilterSchema.safeParse(raw);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid filters', details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  const { rows, liveMode } = await fetchCatalog(parsed.data);

  return NextResponse.json({
    mode: liveMode ? 'live' : 'dev_sample',
    count: rows.length,
    rows,
  });
}
