/**
 * Stripe SDK accessor — server-only.
 *
 * Howard 2026-05-07 IRON-LAW: throws when Stripe isn't configured. The
 * previous behaviour returned `null` so callers could fall through to a
 * "dev_session_*" fabricated checkout URL — that is fake data shipping
 * in source. Page-level callers MUST guard with `isStripeConfigured()`
 * and render <NotConfigured>; route handlers MUST return 503 with a
 * remediation message when not configured.
 */

import Stripe from 'stripe';
import { env, isStripeConfigured } from './env';

let _stripe: Stripe | null = null;

export function getStripe(): Stripe {
  if (!isStripeConfigured()) {
    throw new Error(
      'Stripe Checkout is not configured. Set STRIPE_SECRET_KEY and ' +
        'STRIPE_PUBLISHABLE_KEY before calling getStripe(). Page callers ' +
        'must hard-gate with isStripeConfigured(); API routes must return ' +
        '503 with envVars: ["STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"].',
    );
  }
  if (_stripe) return _stripe;
  _stripe = new Stripe(env.stripeSecretKey, {
    // Pin a recent stable API version that matches what the installed
    // Stripe SDK accepts at the type level. Real Stripe accepts any
    // valid version string at runtime; the SDK's TS narrowing changes
    // per-release.
    apiVersion: '2024-06-20',
    appInfo: {
      name: 'Oyster GameData Buyer Portal',
      version: '0.1.0',
    },
  });
  return _stripe;
}
