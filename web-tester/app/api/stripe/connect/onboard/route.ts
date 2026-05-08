/**
 * POST /api/stripe/connect/onboard
 *
 * Creates a Stripe Connect Express account for the signed-in tester
 * (or reuses an existing one), then returns a one-shot account_link URL
 * the client redirects to. Persists `testers.stripe_account_id` on first
 * call so the same account is reused on subsequent visits.
 *
 * Auth: tester must be signed in. In DEV MODE (no Supabase) this returns
 * a synthetic onboarding URL so the UI can still be exercised end-to-end.
 *
 * Stripe API surface used: POST /v1/accounts, POST /v1/account_links.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../../../../lib/supabase-server';
import { isSupabaseConfigured, isStripeConfigured, env } from '../../../../../lib/env';
import { getStripeClient, StripeApiError } from '../../../../../lib/stripe';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface OnboardResponse {
  url: string;
  account_id: string;
}

export async function POST(_req: NextRequest) {
  // Howard 2026-05-07 IRON-LAW: hard-gate. No DEV MODE fallback to
  // mock Stripe — the previous synthetic `acct_mock_*` accounts +
  // sample-tester@example.com email shipped fake data through the API.
  // 503 with clear remediation when not configured.
  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: ['NEXT_PUBLIC_SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY'],
      },
      { status: 503 },
    );
  }
  if (!isStripeConfigured()) {
    return NextResponse.json(
      {
        error: 'Stripe Connect not configured',
        envVars: ['STRIPE_SECRET_KEY', 'NEXT_PUBLIC_SITE_URL'],
      },
      { status: 503 },
    );
  }
  const stripe = getStripeClient();
  const refreshUrl = `${env.siteUrl}/payouts?stripe_refresh=1`;
  const returnUrl = `${env.siteUrl}/api/stripe/connect/return`;

  // ---- LIVE MODE
  const supabase = getSupabaseServerClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase server client unavailable' }, { status: 500 });
  }
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: 'Not signed in' }, { status: 401 });
  }

  const service = getSupabaseServiceClient();
  if (!service) {
    return NextResponse.json({ error: 'Service client unavailable' }, { status: 500 });
  }

  const { data: tester, error: testerErr } = await service
    .from('testers')
    .select('id, email, stripe_account_id')
    .eq('id', user.id)
    .single();

  if (testerErr || !tester) {
    return NextResponse.json(
      { error: 'Tester profile missing', details: testerErr?.message },
      { status: 404 }
    );
  }

  let accountId = tester.stripe_account_id as string | null;

  try {
    if (!accountId) {
      const account = await stripe.createExpressAccount({
        email: tester.email,
        metadata: { tester_id: tester.id, source: 'oyster-gamedata' },
      });
      accountId = account.id;

      const { error: updateErr } = await service
        .from('testers')
        .update({ stripe_account_id: accountId })
        .eq('id', tester.id);
      if (updateErr) {
        return NextResponse.json(
          { error: 'Failed to persist stripe_account_id', details: updateErr.message },
          { status: 500 }
        );
      }
    }

    const link = await stripe.createAccountLink({
      accountId,
      refreshUrl,
      returnUrl,
    });

    const body: OnboardResponse = { url: link.url, account_id: accountId, mode: 'live' };
    return NextResponse.json(body);
  } catch (err) {
    if (err instanceof StripeApiError) {
      return NextResponse.json(
        { error: 'Stripe API error', details: err.stripeError },
        { status: 502 }
      );
    }
    return NextResponse.json(
      { error: 'Onboarding failed', details: err instanceof Error ? err.message : String(err) },
      { status: 500 }
    );
  }
}
