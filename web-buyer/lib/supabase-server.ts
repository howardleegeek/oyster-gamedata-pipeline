/**
 * Server-side Supabase clients.
 *
 * - `getSupabaseServerClient()` — cookie-bound client used inside Server
 *   Components and Route Handlers. Reads/writes the buyer's auth session.
 * - `getSupabaseServiceClient()` — service-role client for trusted server
 *   operations (signing tarball download URLs, inserting purchase rows
 *   from the Stripe webhook). NEVER pass this to a client component.
 */

import { cookies } from 'next/headers';
import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { env, isSupabaseConfigured } from './env';

export function getSupabaseServerClient() {
  if (!isSupabaseConfigured()) return null;

  const cookieStore = cookies();
  return createServerClient(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      get(name: string) {
        return cookieStore.get(name)?.value;
      },
      set(name: string, value: string, options: CookieOptions) {
        try {
          cookieStore.set({ name, value, ...options });
        } catch {
          // Server Components can't set cookies — ignore. Middleware /
          // route handlers will write any refreshed session on the next
          // request.
        }
      },
      remove(name: string, options: CookieOptions) {
        try {
          cookieStore.set({ name, value: '', ...options });
        } catch {
          /* see above */
        }
      },
    },
  });
}

let _serviceClient: SupabaseClient | null = null;

export function getSupabaseServiceClient(): SupabaseClient | null {
  if (!isSupabaseConfigured() || !env.supabaseServiceRoleKey) return null;
  if (_serviceClient) return _serviceClient;
  _serviceClient = createClient(env.supabaseUrl, env.supabaseServiceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return _serviceClient;
}
