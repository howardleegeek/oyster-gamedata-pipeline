/**
 * OAuth / magic-link callback. Supabase appends `?code=...` here after the
 * user clicks the email link or completes the GitHub flow. We exchange the
 * code for a session cookie, then bounce to the requested `next` URL.
 *
 * Docs: https://supabase.com/docs/guides/auth/server-side/nextjs
 */

import { NextRequest, NextResponse } from 'next/server';
import { getSupabaseServerClient } from '../../../lib/supabase-server';
import { isSupabaseConfigured } from '../../../lib/env';

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get('code');
  const next = searchParams.get('next') ?? '/dashboard';

  if (!isSupabaseConfigured()) {
    return NextResponse.redirect(`${origin}${next}`);
  }

  if (code) {
    const supabase = getSupabaseServerClient();
    if (supabase) {
      const { error } = await supabase.auth.exchangeCodeForSession(code);
      if (error) {
        return NextResponse.redirect(`${origin}/login?error=${encodeURIComponent(error.message)}`);
      }
    }
  }

  return NextResponse.redirect(`${origin}${next}`);
}
