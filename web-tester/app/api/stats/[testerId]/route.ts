/**
 * GET /api/stats/[testerId]
 *
 * Returns aggregate stats + recent uploads for a tester. Used by the
 * dashboard and (optionally) by the desktop recorder for in-app status.
 *
 * Auth model:
 *   - In LIVE mode the caller must be signed in *and* be the tester
 *     in question (auth.uid() === testerId), OR present a service-role
 *     header (used by trusted server-side jobs).
 *   - In DEV mode (no Supabase), returns sample data for any UUID.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../../../lib/supabase-server';
import { isSupabaseConfigured } from '../../../../lib/env';
import type { TesterStats } from '../../../../types/database';

const TesterIdSchema = z.string().uuid();

export const dynamic = 'force-dynamic';

export async function GET(
  req: NextRequest,
  { params }: { params: { testerId: string } }
) {
  const parsed = TesterIdSchema.safeParse(params.testerId);
  if (!parsed.success) {
    return NextResponse.json({ error: 'Invalid testerId — must be a UUID' }, { status: 400 });
  }
  const testerId = parsed.data;

  // Howard 2026-05-07 IRON-LAW: no fake-data fallback. Endpoint returns
  // 503 if Supabase isn't configured — clear failure mode, never
  // fabricated numbers.
  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        details: 'Set NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY',
      },
      { status: 503 },
    );
  }

  // ---- LIVE MODE ------------------------------------------------------
  // Authn: either the user matches, or the request carries the service role secret.
  const supabase = getSupabaseServerClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase server client unavailable' }, { status: 500 });
  }
  const { data: { user } } = await supabase.auth.getUser();

  const adminHeader = req.headers.get('x-supabase-service-role') ?? '';
  const isAdmin = adminHeader && adminHeader === process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!isAdmin && (!user || user.id !== testerId)) {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
  }

  const service = getSupabaseServiceClient();
  if (!service) {
    return NextResponse.json({ error: 'Service client unavailable' }, { status: 500 });
  }

  const [{ data: tester }, { data: tarballs }, { data: payouts }] = await Promise.all([
    service.from('testers').select('id, email, github_handle, total_hours, total_earnings_cents, created_at').eq('id', testerId).single(),
    service.from('tarballs').select('id, tester_id, uploaded_at, size_bytes, sha256, duration_seconds, d5_verdict, d5_score, paid').eq('tester_id', testerId).order('uploaded_at', { ascending: false }).limit(10),
    service.from('payouts').select('amount_cents, status').eq('tester_id', testerId),
  ]);

  if (!tester) {
    return NextResponse.json({ error: 'Tester not found' }, { status: 404 });
  }

  const pendingCents = (payouts ?? []).filter((p: any) => p.status === 'pending').reduce((a: number, p: any) => a + p.amount_cents, 0);
  const paidCents = (payouts ?? []).filter((p: any) => p.status === 'paid').reduce((a: number, p: any) => a + p.amount_cents, 0);

  const stats: TesterStats = {
    tester_id: testerId,
    email: tester.email,
    github_handle: tester.github_handle ?? null,
    total_hours: Number(tester.total_hours ?? 0),
    total_earnings_cents: Number(tester.total_earnings_cents ?? 0),
    pending_earnings_cents: pendingCents,
    paid_earnings_cents: paidCents,
    upload_count: tarballs?.length ?? 0,
    last_upload_at: tarballs?.[0]?.uploaded_at ?? null,
    joined_at: tester.created_at,
  };

  return NextResponse.json({
    stats,
    recent_uploads: tarballs ?? [],
    mode: 'live',
  });
}
