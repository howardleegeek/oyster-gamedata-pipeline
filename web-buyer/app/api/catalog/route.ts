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
 *   200 { mode: 'live', count, rows }
 *   400 { error: '...', details? }
 *   503 { error, envVars[] } — Supabase not configured
 *
 * Howard 2026-05-07 IRON-LAW: no `dev_sample` mode any more. The catalog
 * is real or it is unavailable.
 */

import { NextRequest, NextResponse } from 'next/server';
import { fetchCatalog, FilterSchema, CatalogNotConfiguredError } from '../../../lib/catalog';

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

  try {
    const { rows } = await fetchCatalog(parsed.data);
    return NextResponse.json({
      mode: 'live',
      count: rows.length,
      rows,
    });
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
}
