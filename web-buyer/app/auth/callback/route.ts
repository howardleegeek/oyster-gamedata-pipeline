/**
 * OAuth / magic-link callback. Supabase appends `?code=...` here after the
 * user clicks the email link or completes the GitHub flow. We exchange the
 * code for a session cookie, then bounce to the requested `next` URL.
 *
 * After a successful sign-in we ALSO migrate any anonymous cart cookie
 * into the buyer's `cart_items` table so they don't lose what they had
 * already added before signing in.
 *
 * Docs: https://supabase.com/docs/guides/auth/server-side/nextjs
 */

import { NextRequest, NextResponse } from 'next/server';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../../lib/supabase-server';
import { isSupabaseConfigured } from '../../../lib/env';
import { readCartCookie, clearCartCookie } from '../../../lib/cart-cookie';
import { sanitizeNextPath } from '../../../lib/safe-redirect';

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get('code');
  // Howard 2026-05-08: sanitizeNextPath blocks open-redirect via `?next=//evil.com`.
  const next = sanitizeNextPath(searchParams.get('next'), '/browse');

  if (!isSupabaseConfigured()) {
    return NextResponse.redirect(`${origin}${next}`);
  }

  if (!code) return NextResponse.redirect(`${origin}${next}`);

  const supabase = getSupabaseServerClient();
  if (!supabase) return NextResponse.redirect(`${origin}${next}`);

  const { data: exchangeData, error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    return NextResponse.redirect(
      `${origin}/login?error=${encodeURIComponent(error.message)}`,
    );
  }

  // -- merge anonymous cart cookie -> cart_items rows ---------------------
  const userId = exchangeData?.user?.id;
  const cookieIds = readCartCookie();
  if (userId && cookieIds.length > 0) {
    const service = getSupabaseServiceClient();
    if (service) {
      try {
        const rows = cookieIds.map((tarball_id) => ({ buyer_id: userId, tarball_id }));
        // Upsert against the unique (buyer_id, tarball_id) constraint so
        // duplicates from a re-login are no-ops.
        await service.from('cart_items').upsert(rows, {
          onConflict: 'buyer_id,tarball_id',
          ignoreDuplicates: true,
        });
        clearCartCookie();
      } catch {
        // If the buyer schema isn't applied yet (DEV-ish), keep the cookie.
      }
    }
  }

  return NextResponse.redirect(`${origin}${next}`);
}
