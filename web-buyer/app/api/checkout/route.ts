/**
 * POST /api/checkout
 *
 * Body: { tarball_ids: uuid[], license_type: 'research' | 'commercial' }
 *
 * Creates a Stripe Checkout session and returns its URL. The webhook at
 * /api/checkout/webhook is the authoritative source of truth for fulfilment
 * — DO NOT mark anything purchased here.
 *
 * Howard 2026-05-07 IRON-LAW: when Stripe or Supabase isn't configured, we
 * return 503 with the required envVars. The previous behaviour minted
 * fabricated dev-prefixed Stripe session ids and wrote a dev-marked
 * purchase mode flag to the DB — that was fake financial data shipping
 * in production source. No more.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import {
  isStripeConfigured,
  isSupabaseConfigured,
  env,
} from '../../../lib/env';
import { getStripe } from '../../../lib/stripe';
import {
  getSupabaseServerClient,
} from '../../../lib/supabase-server';
import { fetchCatalog, CatalogNotConfiguredError } from '../../../lib/catalog';
import { totalCents } from '../../../lib/format';

export const dynamic = 'force-dynamic';

const Body = z.object({
  tarball_ids: z.array(z.string().uuid()).min(1).max(20),
  license_type: z.enum(['research', 'commercial']),
});

export async function POST(req: NextRequest) {
  // Howard 2026-05-07 IRON-LAW: hard-gate. No fake-session fallback.
  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY'],
      },
      { status: 503 },
    );
  }
  if (!isStripeConfigured()) {
    return NextResponse.json(
      {
        error: 'Stripe Checkout not configured',
        envVars: ['STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY'],
      },
      { status: 503 },
    );
  }

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

  // Resolve the cart items against the catalog.
  let catalog: Awaited<ReturnType<typeof fetchCatalog>>['rows'];
  try {
    const result = await fetchCatalog({ limit: 200 });
    catalog = result.rows;
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

  // Require an authenticated buyer.
  const supabase = getSupabaseServerClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase server client unavailable' }, { status: 500 });
  }
  const { data: userData } = await supabase.auth.getUser();
  if (!userData?.user) {
    return NextResponse.json(
      { error: 'Sign in required before checkout' },
      { status: 401 },
    );
  }
  const buyerId = userData.user.id;
  const buyerEmail = userData.user.email ?? undefined;

  // ------------------------------------------------------------------
  // LIVE PATH — real Stripe Checkout session.
  // ------------------------------------------------------------------
  const stripe = getStripe();

  const successUrl = new URL(
    env.stripeSuccessPath.replace('{CHECKOUT_SESSION_ID}', '{CHECKOUT_SESSION_ID}'),
    env.siteUrl,
  ).toString();
  const cancelUrl = new URL(env.stripeCancelPath, env.siteUrl).toString();

  // QA1 finding #3 fix (BUG-10): Stripe metadata values are capped at 500
  // chars. With UUID (36) + comma (1) = 37 chars/id, naively joining all
  // tarball_ids into a single `metadata.tarball_ids` field overflows at 14+
  // items. Stripe API 400s the session create call → buyer can't check out.
  //
  // We chunk the IDs across `tarball_ids_1`, `tarball_ids_2`, ... fields
  // (each ≤ TARBALL_IDS_PER_CHUNK ids → ≤ 481 chars value, well under 500).
  // The webhook iterates over all `tarball_ids_*` keys to reconstruct the
  // full list. Cap of 20 → at most 2 chunks → 3 keys including a count =
  // well under Stripe's 50-key metadata limit.
  //
  // We also keep a single-source `tarball_ids_count` so the webhook can
  // detect a mismatch (e.g. Stripe silently dropped a metadata field).
  const TARBALL_IDS_PER_CHUNK = 13; // 13*37 = 481 ≤ 500
  const idChunks: string[] = [];
  for (let i = 0; i < tarball_ids.length; i += TARBALL_IDS_PER_CHUNK) {
    idChunks.push(tarball_ids.slice(i, i + TARBALL_IDS_PER_CHUNK).join(','));
  }
  const idMetadata: Record<string, string> = {
    tarball_ids_count: String(tarball_ids.length),
    tarball_ids_chunk_count: String(idChunks.length),
  };
  idChunks.forEach((chunk, idx) => {
    idMetadata[`tarball_ids_${idx + 1}`] = chunk;
  });
  // Defence-in-depth: assert no value exceeds Stripe's 500-char cap before
  // we hand off to Stripe. Cheaper to 502 here than to chase a Stripe 400.
  for (const [k, v] of Object.entries(idMetadata)) {
    if (v.length > 500) {
      return NextResponse.json(
        {
          error: 'Internal metadata over-limit',
          details: `metadata.${k} is ${v.length} chars (Stripe cap: 500)`,
        },
        { status: 500 },
      );
    }
  }

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
        buyer_id: buyerId,
        license_type,
        ...idMetadata,
      },
      payment_intent_data: {
        metadata: {
          buyer_id: buyerId,
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
