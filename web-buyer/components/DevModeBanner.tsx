import { isSupabaseConfigured } from '../lib/env';

/**
 * Renders a sticky banner at the top of the page when Supabase is not
 * configured. Hides itself silently in production once the env vars are
 * set.
 */
export function DevModeBanner() {
  if (isSupabaseConfigured()) return null;

  return (
    <div className="bg-amber-accent text-oyster-950 text-sm font-medium px-4 py-2 text-center">
      <span className="font-bold">[DEV MODE]</span>{' '}
      Supabase &amp; Stripe are not configured — pages render with sample data
      and Checkout is faked. Copy{' '}
      <code className="font-mono bg-oyster-950/15 px-1.5 py-0.5 rounded">.env.example</code>{' '}
      to <code className="font-mono bg-oyster-950/15 px-1.5 py-0.5 rounded">.env.local</code>{' '}
      and fill in keys to switch to live mode.
    </div>
  );
}
