'use client';

/**
 * Browser-side Supabase client. Lazily instantiated so that the bundle
 * doesn't crash during pre-render when env vars are missing.
 */

import { createBrowserClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';
import { env, isSupabaseConfigured } from './env';

let _client: SupabaseClient | null = null;

export function getSupabaseBrowserClient(): SupabaseClient | null {
  if (!isSupabaseConfigured()) return null;
  if (_client) return _client;
  _client = createBrowserClient(env.supabaseUrl, env.supabaseAnonKey);
  return _client;
}
