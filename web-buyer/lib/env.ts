/**
 * Centralised env access for the buyer portal.
 *
 * Same pattern as web-tester/lib/env.ts. Howard 2026-05-07 IRON-LAW:
 * when a service is not configured, callers MUST hard-gate the UI with
 * <NotConfigured> or return 503 with envVars from API routes. Never
 * fabricate data behind a banner.
 *
 * IMPORTANT:
 *   - Only NEXT_PUBLIC_* values are visible in the browser.
 *   - Server-only secrets (Stripe secret, Supabase service role) MUST stay
 *     out of client components.
 */

export const env = {
  // Supabase (browser-safe)
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? '',
  supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? '',

  // Supabase (server-only)
  supabaseServiceRoleKey: process.env.SUPABASE_SERVICE_ROLE_KEY ?? '',
  tarballBucket: process.env.SUPABASE_TARBALL_BUCKET ?? 'tarballs',

  // App
  siteUrl: process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3001',

  // Pricing
  pricePerGbCents: parseInt(process.env.GAMEDATA_PRICE_PER_GB_CENTS ?? '2500', 10),
  researchDiscountPct: parseInt(process.env.GAMEDATA_RESEARCH_DISCOUNT_PCT ?? '40', 10),

  // Downloads
  downloadLinkTtlSeconds: parseInt(process.env.DOWNLOAD_LINK_TTL_SECONDS ?? '86400', 10),

  // Stripe (browser-safe)
  stripePublishableKey: process.env.STRIPE_PUBLISHABLE_KEY ?? '',

  // Stripe (server-only)
  stripeSecretKey: process.env.STRIPE_SECRET_KEY ?? '',
  stripeWebhookSecret: process.env.STRIPE_WEBHOOK_SECRET ?? '',
  stripeSuccessPath:
    process.env.STRIPE_CHECKOUT_SUCCESS_PATH ?? '/downloads?session_id={CHECKOUT_SESSION_ID}',
  stripeCancelPath: process.env.STRIPE_CHECKOUT_CANCEL_PATH ?? '/cart',

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
 * Returns true when Stripe is configured for live Checkout sessions.
 * If false, /api/checkout returns 503 with envVars and pages render
 * <NotConfigured>. The previous behaviour minted `dev_session_*`
 * fabricated session ids — that was iron-law violation and is gone.
 */
export function isStripeConfigured(): boolean {
  return Boolean(env.stripeSecretKey) && Boolean(env.stripePublishableKey);
}
