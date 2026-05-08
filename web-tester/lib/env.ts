/**
 * Centralised env access. Howard 2026-05-07 IRON-LAW: when a service
 * isn't configured, callers MUST hard-gate the UI with <NotConfigured>
 * or return 503 with envVars from API routes. The previous "graceful
 * DEV MODE degrade with sample data" pattern is gone.
 *
 * IMPORTANT: only NEXT_PUBLIC_* values are visible in the browser. Server-only
 * keys (service role, Stripe secret) MUST stay out of client components.
 */

export const env = {
  // Supabase (browser-safe)
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? '',
  supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? '',

  // Supabase (server-only)
  supabaseServiceRoleKey: process.env.SUPABASE_SERVICE_ROLE_KEY ?? '',
  tarballBucket: process.env.SUPABASE_TARBALL_BUCKET ?? 'tarballs',

  // App
  siteUrl: process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000',

  // Recorder
  recorderExeUrl: process.env.RECORDER_EXE_URL ?? '/downloads/OysterRecorder.exe',
  recorderVersion: process.env.RECORDER_VERSION ?? '0.1.0',

  // Earnings
  ratePerHourCents: parseInt(process.env.GAMEDATA_RATE_PER_HOUR_CENTS ?? '600', 10),
  minPayoutCents: parseInt(process.env.GAMEDATA_MIN_PAYOUT_CENTS ?? '2000', 10),

  // Stripe Connect (server-only)
  stripeSecretKey: process.env.STRIPE_SECRET_KEY ?? '',
  stripeWebhookSecret: process.env.STRIPE_WEBHOOK_SECRET ?? '',
  stripeConnectClientId: process.env.STRIPE_CONNECT_CLIENT_ID ?? '',

} as const;

/**
 * Returns true when the app has enough config to actually talk to Supabase.
 * If false, callers MUST hard-gate (page-level: render <NotConfigured>;
 * API: return 503 with envVars). Never fabricate data behind a banner.
 */
export function isSupabaseConfigured(): boolean {
  return Boolean(env.supabaseUrl) && Boolean(env.supabaseAnonKey);
}

/**
 * Returns true when Stripe is configured for real API calls.
 *
 * Howard 2026-05-07 IRON-LAW: when this returns false, every Stripe code
 * path either throws (lib/stripe.ts → getStripeClient) or returns 503
 * with envVars (api routes). The `__testOnlyMockClient()` helper exists
 * solely for unit tests; production code must never call it.
 */
export function isStripeConfigured(): boolean {
  return Boolean(env.stripeSecretKey) && env.stripeSecretKey.startsWith('sk_');
}
