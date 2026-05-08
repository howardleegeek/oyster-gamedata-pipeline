/**
 * POST /api/cart/add
 *
 * Body (JSON or form-data): { tarball_id: uuid }
 *
 * Behaviour:
 *   - signed-in buyer  -> upsert into `cart_items` keyed (buyer_id, tarball_id)
 *   - anonymous buyer  -> append to the `oyster_cart` cookie
 *
 * Idempotent: adding a tarball already in the cart is a no-op.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../../../lib/supabase-server';
import { isSupabaseConfigured } from '../../../../lib/env';
import { readCartCookie, writeCartCookie } from '../../../../lib/cart-cookie';

const Body = z.object({ tarball_id: z.string().uuid() });

async function readPayload(req: NextRequest): Promise<unknown> {
  const ct = req.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) return req.json();
  if (ct.includes('multipart/form-data') || ct.includes('application/x-www-form-urlencoded')) {
    const form = await req.formData();
    return Object.fromEntries(form.entries());
  }
  return {};
}

export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  let raw: unknown;
  try {
    raw = await readPayload(req);
  } catch {
    return NextResponse.json({ error: 'Could not parse body' }, { status: 400 });
  }
  const parsed = Body.safeParse(raw);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid body', details: parsed.error.flatten() },
      { status: 400 },
    );
  }
  const tarballId = parsed.data.tarball_id;

  // Try authenticated path first; fall back to cookie for anonymous.
  if (isSupabaseConfigured()) {
    const supabase = getSupabaseServerClient();
    const { data } = supabase ? await supabase.auth.getUser() : { data: { user: null } };
    if (data?.user) {
      const service = getSupabaseServiceClient();
      if (service) {
        const { error } = await service
          .from('cart_items')
          .upsert(
            { buyer_id: data.user.id, tarball_id: tarballId },
            { onConflict: 'buyer_id,tarball_id', ignoreDuplicates: true },
          );
        if (error) {
          return NextResponse.json(
            { error: 'cart_items upsert failed', details: error.message },
            { status: 500 },
          );
        }
        return NextResponse.json({ added: true, mode: 'live' });
      }
    }
  }

  // Anonymous / DEV: write to cookie.
  const ids = readCartCookie();
  if (!ids.includes(tarballId)) {
    ids.push(tarballId);
    writeCartCookie(ids);
  }
  return NextResponse.json({ added: true, mode: 'cookie' });
}
