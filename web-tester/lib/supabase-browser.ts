'use client';

/**
 * Browser-side Supabase client. Lazily instantiated so that the bundle
 * doesn't crash during pre-render when env vars are missing.
 *
 * Note: we let TS infer the client type from createBrowserClient() rather
 * than annotating with SupabaseClient<any> — newer @supabase/ssr versions
 * use a stricter generic schema and the types diverge from the v1 alias.
 */

import { createBrowserClient } from '@supabase/ssr';
import { env, isSupabaseConfigured } from './env';

type BrowserClient = ReturnType<typeof createBrowserClient>;

let _client: BrowserClient | null = null;

export function getSupabaseBrowserClient(): BrowserClient | null {
  if (!isSupabaseConfigured()) return null;
  if (_client) return _client;
  _client = createBrowserClient(env.supabaseUrl, env.supabaseAnonKey);
  return _client;
}
