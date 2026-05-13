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
  // Honor both the repo-original NEXT_PUBLIC_SUPABASE_URL and the task-spec
  // SUPABASE_URL alias. Repo-original wins because that's what every deploy
  // already has set.
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL ?? '',
  supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? '',

  // Supabase (server-only). Two acceptable names for the service-role key
  // and the bucket — keep the legacy names canonical, treat the spec names
  // (SUPABASE_SERVICE_KEY / SUPABASE_BUCKET) as aliases for new deploys.
  supabaseServiceRoleKey:
    process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_SERVICE_KEY ?? '',
  /** Read bucket for legacy tarballs that were uploaded through Next.js. */
  tarballBucket: process.env.SUPABASE_TARBALL_BUCKET ?? 'tarballs',
  /** Bucket where the recorder PUTs directly via signed URLs (Gap #8). */
  tarballUploadBucket:
    process.env.SUPABASE_TARBALL_UPLOAD_BUCKET ?? process.env.SUPABASE_BUCKET ?? 'tarball-uploads',
  /** Signed-URL expiry. 15 min = enough to start the upload, short enough
   *  to limit blast radius if the URL leaks. */
  signedUploadUrlTtlSeconds: parseInt(
    process.env.SUPABASE_SIGNED_UPLOAD_URL_TTL_SECONDS ?? '900',
    10,
  ),

  // App
  siteUrl: process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000',

  // Recorder — defaults to the v0.26.0 GitHub Release asset so the
  // download button works out of the box on a fresh Vercel deploy
  // even before RECORDER_EXE_URL is set.
  recorderExeUrl:
    process.env.RECORDER_EXE_URL ??
    'https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/recorder-v0.26.0-real-game-state/OysterRecorder.exe',
  recorderVersion: process.env.RECORDER_VERSION ?? '0.26.0',

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
