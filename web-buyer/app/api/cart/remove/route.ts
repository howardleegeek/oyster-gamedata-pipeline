/**
 * POST /api/cart/remove
 *
 * Body (JSON or form-data): { tarball_id: uuid }
 *
 * Behaviour mirrors /api/cart/add — DB row delete for signed-in users,
 * cookie filter for anonymous. Always returns 200 even if the row didn't
 * exist (idempotent).
 *
 * The /cart page submits an HTML <form> here, so we redirect back to /cart
 * for HTML clients and return JSON for fetch() callers.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../../../lib/supabase-server';
import { isSupabaseConfigured } from '../../../../lib/env';
import { readCartCookie, writeCartCookie } from '../../../../lib/cart-cookie';

const Body = z.object({ tarball_id: z.string().uuid() });

async function readPayload(req: NextRequest): Promise<{ payload: unknown; isForm: boolean }> {
  const ct = req.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) {
    return { payload: await req.json(), isForm: false };
  }
  if (ct.includes('multipart/form-data') || ct.includes('application/x-www-form-urlencoded')) {
    const form = await req.formData();
    return { payload: Object.fromEntries(form.entries()), isForm: true };
  }
  return { payload: {}, isForm: false };
}

export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  let raw: unknown;
  let isForm = false;
  try {
    const r = await readPayload(req);
    raw = r.payload;
    isForm = r.isForm;
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

  let removed = false;

  if (isSupabaseConfigured()) {
    const supabase = getSupabaseServerClient();
    const { data } = supabase ? await supabase.auth.getUser() : { data: { user: null } };
    if (data?.user) {
      const service = getSupabaseServiceClient();
      if (service) {
        const { error } = await service
          .from('cart_items')
          .delete()
          .eq('buyer_id', data.user.id)
          .eq('tarball_id', tarballId);
        if (error) {
          return NextResponse.json(
            { error: 'cart_items delete failed', details: error.message },
            { status: 500 },
          );
        }
        removed = true;
      }
    }
  }

  if (!removed) {
    // Anonymous / DEV: filter cookie.
    const ids = readCartCookie().filter((id) => id !== tarballId);
    writeCartCookie(ids);
    removed = true;
  }

  if (isForm) {
    // 303 ensures the browser re-issues a GET for /cart.
    const url = new URL('/cart', req.url);
    return NextResponse.redirect(url, { status: 303 });
  }
  return NextResponse.json({ removed: true });
}
