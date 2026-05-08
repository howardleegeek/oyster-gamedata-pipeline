/**
 * GET /api/stripe/connect/return
 *
 * Stripe redirects the tester here after they finish (or abandon) the
 * Express onboarding flow. Stripe does NOT include the account ID in
 * this redirect by design — we look it up from `testers.stripe_account_id`
 * for the signed-in user, then call `accounts.retrieve()` to read the
 * latest `charges_enabled` / `payouts_enabled` flags and persist them.
 *
 * The return is always a 302 to /payouts so the UI re-renders with the
 * fresh account state. Failure modes (no account, Stripe API down)
 * redirect with a `?stripe_error=...` query string the UI surfaces.
 *
 * Stripe API surface used: GET /v1/accounts/{id}.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../../../../lib/supabase-server';
import { isSupabaseConfigured, isStripeConfigured, env } from '../../../../../lib/env';
import { getStripeClient, StripeApiError } from '../../../../../lib/stripe';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function redirectToPayouts(qs: string = '') {
  const url = `${env.siteUrl}/payouts${qs ? `?${qs}` : ''}`;
  return NextResponse.redirect(url, { status: 302 });
}

export async function GET(req: NextRequest) {
  // Howard 2026-05-07 IRON-LAW: hard-gate.
  if (!isSupabaseConfigured()) {
    return redirectToPayouts('stripe_error=supabase_not_configured');
  }
  if (!isStripeConfigured()) {
    return redirectToPayouts('stripe_error=stripe_not_configured');
  }
  const accountIdFromQuery = req.nextUrl.searchParams.get('account');
  const stripe = getStripeClient();

  const supabase = getSupabaseServerClient();
  if (!supabase) return redirectToPayouts('stripe_error=server_unavailable');

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return redirectToPayouts('stripe_error=not_signed_in');

  const service = getSupabaseServiceClient();
  if (!service) return redirectToPayouts('stripe_error=service_unavailable');

  const { data: tester } = await service
    .from('testers')
    .select('id, stripe_account_id')
    .eq('id', user.id)
    .single();

  const accountId = accountIdFromQuery ?? tester?.stripe_account_id ?? null;
  if (!accountId) return redirectToPayouts('stripe_error=no_account');

  try {
    const account = await stripe.retrieveAccount(accountId);
    const { error: updateErr } = await service
      .from('testers')
      .update({
        stripe_account_id: account.id,
        stripe_charges_enabled: account.charges_enabled,
        stripe_payouts_enabled: account.payouts_enabled,
        stripe_details_submitted: account.details_submitted,
      })
      .eq('id', user.id);

    if (updateErr) {
      return redirectToPayouts(`stripe_error=${encodeURIComponent('persist_failed')}`);
    }

    if (account.charges_enabled && account.payouts_enabled) {
      return redirectToPayouts('stripe_return=ready');
    }
    return redirectToPayouts('stripe_return=incomplete');
  } catch (err) {
    const code =
      err instanceof StripeApiError
        ? err.stripeError.code ?? 'stripe_api_error'
        : 'unexpected_error';
    return redirectToPayouts(`stripe_error=${encodeURIComponent(code)}`);
  }
}
