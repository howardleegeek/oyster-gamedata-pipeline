/**
 * POST /api/stripe/connect/dashboard
 *
 * Generates a one-shot Stripe Express dashboard login link for the
 * signed-in tester. The link expires in ~5 minutes; the tester can use
 * it to update their bank account, view payouts, etc. without ever
 * authenticating with Stripe directly.
 *
 * Returns: { url: string } — the caller redirects to it.
 *
 * Stripe API surface used: POST /v1/accounts/{id}/login_links.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../../../../lib/supabase-server';
import { isSupabaseConfigured, isStripeConfigured } from '../../../../../lib/env';
import { getStripeClient, StripeApiError } from '../../../../../lib/stripe';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(_req: NextRequest) {
  // Howard 2026-05-07 IRON-LAW: hard-gate.
  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      { error: 'Supabase not configured', envVars: ['NEXT_PUBLIC_SUPABASE_URL'] },
      { status: 503 },
    );
  }
  if (!isStripeConfigured()) {
    return NextResponse.json(
      { error: 'Stripe Connect not configured', envVars: ['STRIPE_SECRET_KEY'] },
      { status: 503 },
    );
  }
  const stripe = getStripeClient();

  // ---- LIVE MODE
  const supabase = getSupabaseServerClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase server client unavailable' }, { status: 500 });
  }
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Not signed in' }, { status: 401 });

  const service = getSupabaseServiceClient();
  if (!service) return NextResponse.json({ error: 'Service client unavailable' }, { status: 500 });

  const { data: tester } = await service
    .from('testers')
    .select('stripe_account_id, stripe_charges_enabled')
    .eq('id', user.id)
    .single();

  if (!tester?.stripe_account_id) {
    return NextResponse.json(
      { error: 'No Stripe account — complete onboarding first' },
      { status: 409 }
    );
  }
  if (!tester.stripe_charges_enabled) {
    return NextResponse.json(
      { error: 'Account onboarding incomplete — finish setup first' },
      { status: 409 }
    );
  }

  try {
    const link = await stripe.createLoginLink(tester.stripe_account_id);
    return NextResponse.json({ url: link.url, mode: 'live' });
  } catch (err) {
    if (err instanceof StripeApiError) {
      return NextResponse.json(
        { error: 'Stripe API error', details: err.stripeError },
        { status: 502 }
      );
    }
    return NextResponse.json(
      { error: 'Login link failed', details: err instanceof Error ? err.message : String(err) },
      { status: 500 }
    );
  }
}
