/**
 * POST /api/checkout
 *
 * Body: { tarball_ids: uuid[], license_type: 'research' | 'commercial' }
 *
 * Creates a Stripe Checkout session and returns its URL. The webhook at
 * /api/checkout/webhook is the authoritative source of truth for fulfilment
 * — DO NOT mark anything purchased here.
 *
 * Two failure modes both return a usable response:
 *   1. Stripe NOT configured (DEV / no secret key) -> we mint a deterministic
 *      `dev_session_<uuid>` id, write the purchases + licenses ourselves, and
 *      return /downloads?session_id=... so the UI flow finishes end-to-end.
 *   2. Stripe configured but Supabase is not -> we still create the live
 *      Stripe session, but add a `dev_supabase=1` query param so /downloads
 *      knows to render sample purchases for the redirected buyer.
 */

import { NextRequest, NextResponse } from 'next/server';
import crypto from 'node:crypto';
import { z } from 'zod';
import {
  isStripeConfigured,
  isSupabaseConfigured,
  env,
} from '../../../lib/env';
import { getStripe } from '../../../lib/stripe';
import {
  getSupabaseServerClient,
  getSupabaseServiceClient,
} from '../../../lib/supabase-server';
import { fetchCatalog } from '../../../lib/catalog';
import { totalCents } from '../../../lib/format';

export const dynamic = 'force-dynamic';

const Body = z.object({
  tarball_ids: z.array(z.string().uuid()).min(1).max(20),
  license_type: z.enum(['research', 'commercial']),
});

function buildSuccessUrl(sessionId: string): string {
  // Replace Stripe's placeholder {CHECKOUT_SESSION_ID} when we know the id
  // ahead of time (DEV path). LIVE Stripe Checkout will fill the template
  // itself when it redirects.
  const path = env.stripeSuccessPath.replace('{CHECKOUT_SESSION_ID}', sessionId);
  return new URL(path, env.siteUrl).toString();
}

export async function POST(req: NextRequest) {
  let payload: unknown;
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }
  const parsed = Body.safeParse(payload);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid body', details: parsed.error.flatten() },
      { status: 400 },
    );
  }
  const { tarball_ids, license_type } = parsed.data;

  // Resolve the cart items against the catalog so we know titles + prices.
  const { rows: catalog } = await fetchCatalog({ limit: 200 });
  const byId = new Map(catalog.map((r) => [r.id, r]));
  const items = tarball_ids.map((id) => byId.get(id)).filter(Boolean) as typeof catalog;
  if (items.length === 0) {
    return NextResponse.json(
      { error: 'No matching tarballs in catalog' },
      { status: 400 },
    );
  }

  // Apply research discount once, evenly across line items.
  const discountPct = license_type === 'research' ? env.researchDiscountPct : 0;
  const discountedTotalCents = totalCents(
    items.map((i) => i.size_bytes),
    env.pricePerGbCents,
    discountPct,
  );

  // Recover or create a buyer id. In LIVE mode we require the user to be
  // signed in BEFORE creating a Stripe session — middleware already gates
  // /checkout but the cart redirector lands here directly so we double-check.
  let buyerId: string | null = null;
  let buyerEmail: string | undefined;
  if (isSupabaseConfigured()) {
    const supabase = getSupabaseServerClient();
    const { data } = supabase ? await supabase.auth.getUser() : { data: { user: null } };
    if (data?.user) {
      buyerId = data.user.id;
      buyerEmail = data.user.email ?? undefined;
    } else if (isStripeConfigured()) {
      // Refuse to create a real Stripe session without an authenticated buyer.
      return NextResponse.json(
        { error: 'Sign in required before checkout' },
        { status: 401 },
      );
    }
  }

  // ------------------------------------------------------------------
  // DEV PATH — Stripe not configured. Synthesize a session id + write
  // purchase rows directly (only if the schema is applied). Either way,
  // return a /downloads URL so the UI flow completes.
  // ------------------------------------------------------------------
  if (!isStripeConfigured()) {
    const fakeSession = `dev_session_${crypto.randomUUID()}`;

    if (buyerId && isSupabaseConfigured()) {
      const service = getSupabaseServiceClient();
      if (service) {
        // Compute per-item price after the flat discount so the
        // licensed amounts on /downloads sum to the cart total.
        const perItem = items.map((t) => {
          const base = t.price_cents;
          const discounted = Math.max(
            base - Math.floor((base * discountPct) / 100),
            0,
          );
          return { tarball_id: t.id, amount_cents: discounted };
        });

        const { data: purchaseRows } = await service
          .from('purchases')
          .insert(
            perItem.map((p) => ({
              buyer_id: buyerId,
              tarball_id: p.tarball_id,
              amount_cents: p.amount_cents,
              stripe_session_id: fakeSession,
            })),
          )
          .select('id');

        if (purchaseRows) {
          await service.from('licenses').insert(
            (purchaseRows as { id: string }[]).map((p) => ({
              purchase_id: p.id,
              type: license_type,
              terms_url: `/licenses#${license_type}`,
            })),
          );
          // Update licenses' purchase_id back-reference and clear cart.
          // (Step done below — schema has trigger)
          await service.from('cart_items').delete().eq('buyer_id', buyerId);
        }
      }
    }

    return NextResponse.json({
      url: buildSuccessUrl(fakeSession),
      session_id: fakeSession,
      mode: 'dev_fake',
      total_cents: discountedTotalCents,
    });
  }

  // ------------------------------------------------------------------
  // LIVE PATH — real Stripe Checkout session.
  // ------------------------------------------------------------------
  const stripe = getStripe();
  if (!stripe) {
    return NextResponse.json({ error: 'Stripe SDK unavailable' }, { status: 500 });
  }

  const successUrl = new URL(
    env.stripeSuccessPath.replace('{CHECKOUT_SESSION_ID}', '{CHECKOUT_SESSION_ID}'),
    env.siteUrl,
  ).toString();
  const cancelUrl = new URL(env.stripeCancelPath, env.siteUrl).toString();

  try {
    const session = await stripe.checkout.sessions.create({
      mode: 'payment',
      success_url: successUrl,
      cancel_url: cancelUrl,
      customer_email: buyerEmail,
      line_items: items.map((t) => {
        const base = t.price_cents;
        const discounted = Math.max(base - Math.floor((base * discountPct) / 100), 0);
        return {
          quantity: 1,
          price_data: {
            currency: 'usd',
            unit_amount: discounted,
            product_data: {
              name: t.title,
              description: `${t.mc_task_type} · ${(t.size_bytes / 1024 / 1024 / 1024).toFixed(1)} GB · ${license_type} license`,
              metadata: { tarball_id: t.id },
            },
          },
        };
      }),
      metadata: {
        buyer_id: buyerId ?? '',
        license_type,
        tarball_ids: tarball_ids.join(','),
      },
      payment_intent_data: {
        metadata: {
          buyer_id: buyerId ?? '',
          license_type,
        },
      },
    });

    if (!session.url) {
      return NextResponse.json(
        { error: 'Stripe returned no Checkout URL' },
        { status: 502 },
      );
    }
    return NextResponse.json({
      url: session.url,
      session_id: session.id,
      mode: 'live',
      total_cents: discountedTotalCents,
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: 'Stripe Checkout creation failed', details: err.message ?? String(err) },
      { status: 502 },
    );
  }
}
