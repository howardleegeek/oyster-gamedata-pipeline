import { NextRequest, NextResponse } from 'next/server';
import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { env, isSupabaseConfigured } from './lib/env';

/**
 * Refresh the Supabase session on every request and gate the buyer
 * portal's authenticated routes (`/downloads`, `/checkout`) behind it.
 *
 * `/cart` and `/browse` are intentionally accessible without auth — buyers
 * can fill a cart anonymously and we'll persist it once they sign in (the
 * cart cookie merges into cart_items on first login).
 *
 * Howard 2026-05-07 IRON-LAW: when Supabase isn't configured, the
 * middleware short-circuits to the next handler and the page-level
 * <NotConfigured> hard-gate takes over. The previous comment said
 * "pages themselves render sample data with the [DEV MODE] banner" —
 * that pattern shipped fabricated catalog data and is gone.
 */
export async function middleware(request: NextRequest) {
  const response = NextResponse.next({ request: { headers: request.headers } });

  if (!isSupabaseConfigured()) return response;

  const supabase = createServerClient(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      get(name: string) {
        return request.cookies.get(name)?.value;
      },
      set(name: string, value: string, options: CookieOptions) {
        response.cookies.set({ name, value, ...options });
      },
      remove(name: string, options: CookieOptions) {
        response.cookies.set({ name, value: '', ...options });
      },
    },
  });

  // Touching getUser() refreshes the access token cookie if needed.
  const { data: { user } } = await supabase.auth.getUser();

  const protectedPrefixes = ['/downloads', '/checkout'];
  const path = request.nextUrl.pathname;
  const needsAuth = protectedPrefixes.some((p) => path === p || path.startsWith(p + '/'));

  if (needsAuth && !user) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.searchParams.set('next', path);
    return NextResponse.redirect(url);
  }

  return response;
}

export const config = {
  matcher: [
    // Run on every route except static assets and the Stripe webhook
    // (which has its own raw-body verification and never has a session).
    '/((?!_next/static|_next/image|favicon.ico|samples/|api/checkout/webhook).*)',
  ],
};
