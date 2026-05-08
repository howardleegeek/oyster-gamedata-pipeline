/**
 * POST /api/checkout/webhook
 *
 * Stripe webhook. Authoritative source of truth for fulfilment.
 *
 * On `checkout.session.completed` (status=paid) we:
 *   1. Insert one `purchases` row per tarball in the session metadata.
 *   2. Insert one `licenses` row per purchase.
 *   3. Clear the buyer's cart.
 *
 * The handler is idempotent: re-running on the same session_id is a no-op
 * because (buyer_id, tarball_id, stripe_session_id) is a unique tuple.
 *
 * NOTE: Stripe webhooks REQUIRE the raw request body to verify the
 * signature, so we MUST NOT touch req.json() before constructing the event.
 */

import { NextRequest, NextResponse } from 'next/server';
import type Stripe from 'stripe';
import { env, isStripeConfigured } from '../../../../lib/env';
import { getStripe } from '../../../../lib/stripe';
import { getSupabaseServiceClient } from '../../../../lib/supabase-server';

export const runtime = 'nodejs'; // need raw body
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  if (!isStripeConfigured()) {
    return NextResponse.json(
      {
        error:
          'Stripe is not configured — webhook rejected. Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET.',
      },
      { status: 503 },
    );
  }

  const stripe = getStripe();
  if (!stripe) {
    return NextResponse.json({ error: 'Stripe SDK unavailable' }, { status: 500 });
  }

  const sig = req.headers.get('stripe-signature');
  if (!sig) {
    return NextResponse.json({ error: 'Missing stripe-signature header' }, { status: 400 });
  }

  // Read the raw body. Next 14's NextRequest.text() returns the unmodified body
  // and is what Stripe's constructEvent expects.
  const rawBody = await req.text();

  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(rawBody, sig, env.stripeWebhookSecret);
  } catch (err: any) {
    return NextResponse.json(
      { error: 'Webhook signature verification failed', details: err.message ?? String(err) },
      { status: 400 },
    );
  }

  // We only act on completed checkout sessions. Stripe sends a few other
  // related events (checkout.session.async_payment_succeeded, etc.) — we
  // 200 them so Stripe stops retrying, but don't need to act.
  if (event.type !== 'checkout.session.completed') {
    return NextResponse.json({ ignored: true, type: event.type });
  }

  const session = event.data.object as Stripe.Checkout.Session;
  if (session.payment_status !== 'paid') {
    return NextResponse.json({ ignored: true, reason: 'not paid yet' });
  }

  const buyerId = (session.metadata?.buyer_id ?? '').trim();
  const licenseType =
    session.metadata?.license_type === 'research' ? 'research' : 'commercial';
  const idsStr = session.metadata?.tarball_ids ?? '';
  const tarballIds = idsStr.split(',').map((s) => s.trim()).filter(Boolean);

  if (!buyerId || tarballIds.length === 0) {
    return NextResponse.json(
      { error: 'Missing buyer_id or tarball_ids on session metadata' },
      { status: 400 },
    );
  }

  const service = getSupabaseServiceClient();
  if (!service) {
    return NextResponse.json(
      { error: 'Supabase service client unavailable' },
      { status: 500 },
    );
  }

  // Stripe's session.amount_total is the sum we charged (cents). We store
  // it per-tarball by dividing equally — line-item granularity is not
  // exposed unless we expand the session, which costs an extra API call.
  // Instead we re-derive per-tarball from session line items.
  let lineItems: Stripe.LineItem[] = [];
  try {
    const list = await stripe.checkout.sessions.listLineItems(session.id, {
      limit: tarballIds.length,
      expand: ['data.price.product'],
    });
    lineItems = list.data;
  } catch {
    /* fall through — we'll split evenly */
  }

  const evenSplit = Math.floor((session.amount_total ?? 0) / Math.max(tarballIds.length, 1));
  const purchasesToInsert = tarballIds.map((tid, i) => {
    const li = lineItems[i];
    const productMeta = li?.price?.product as Stripe.Product | undefined;
    const tidFromMeta = productMeta?.metadata?.tarball_id;
    return {
      buyer_id: buyerId,
      tarball_id: tidFromMeta ?? tid,
      amount_cents: li?.amount_total ?? evenSplit,
      stripe_session_id: session.id,
    };
  });

  // Insert purchases — ON CONFLICT DO NOTHING so retries are no-ops.
  const { data: purchaseRows, error: purchaseErr } = await service
    .from('purchases')
    .insert(purchasesToInsert)
    .select('id, tarball_id');

  if (purchaseErr) {
    // Duplicate (re-delivered webhook) — short-circuit success.
    if (purchaseErr.code === '23505') {
      return NextResponse.json({ ok: true, deduped: true });
    }
    return NextResponse.json(
      { error: 'purchases insert failed', details: purchaseErr.message },
      { status: 500 },
    );
  }

  if (purchaseRows && purchaseRows.length > 0) {
    const licenseRows = (purchaseRows as { id: string }[]).map((p) => ({
      purchase_id: p.id,
      type: licenseType,
      terms_url: `/licenses#${licenseType}`,
    }));
    const { error: licErr } = await service.from('licenses').insert(licenseRows);
    if (licErr && licErr.code !== '23505') {
      return NextResponse.json(
        { error: 'licenses insert failed', details: licErr.message },
        { status: 500 },
      );
    }
  }

  // Clear cart for this buyer.
  await service.from('cart_items').delete().eq('buyer_id', buyerId);

  return NextResponse.json({ ok: true, purchased: tarballIds.length });
}

export async function GET() {
  // Useful smoke-test surface — Stripe will only POST.
  return NextResponse.json({
    endpoint: '/api/checkout/webhook',
    method: 'POST',
    notes:
      'Stripe webhook. Acts on checkout.session.completed. Requires STRIPE_WEBHOOK_SECRET. Use `stripe listen --forward-to http://localhost:3001/api/checkout/webhook` for local dev.',
  });
}
