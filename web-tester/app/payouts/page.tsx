import Link from 'next/link';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../lib/supabase-server';
import { isSupabaseConfigured, isStripeConfigured, env } from '../../lib/env';
import { formatCents, formatRelativeTime } from '../../lib/format';
import { getStripeClient, type StripePayout } from '../../lib/stripe';
import { StripeConnectButton } from '../../components/StripeConnectButton';
import { NotConfigured } from '../../components/NotConfigured';
import type { PayoutRow } from '../../types/database';

export const dynamic = 'force-dynamic';

type ConnectState = 'none' | 'incomplete' | 'ready';

interface PageData {
  payouts: PayoutRow[];
  stripePayouts: StripePayout[];
  pendingCents: number;
  paidCents: number;
  email: string;
  connectState: ConnectState;
  stripeAccountId: string | null;
  flashError: string | null;
  flashInfo: string | null;
}

async function loadPageData(searchParams: {
  stripe_return?: string;
  stripe_error?: string;
}): Promise<PageData> {
  const flashError = searchParams.stripe_error ?? null;
  const flashInfo =
    searchParams.stripe_return === 'ready'
      ? 'Stripe onboarding complete — payouts are now enabled.'
      : searchParams.stripe_return === 'incomplete'
        ? 'Stripe still needs a few details before payouts are enabled. Click "Finish setup" to continue.'
        : null;

  // Howard 2026-05-07 IRON-LAW: caller renders <NotConfigured> when
  // Supabase or Stripe missing. We never fabricate connect_state /
  // payouts. Returning here forces caller to short-circuit.
  if (!isSupabaseConfigured()) {
    throw new Error('Supabase not configured — caller should render <NotConfigured>.');
  }

  // ----- LIVE MODE -----
  const supabase = getSupabaseServerClient();
  if (!supabase) throw new Error('Supabase server client unavailable.');
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) throw new Error('Not signed in (middleware should have redirected).');

  const service = getSupabaseServiceClient();
  if (!service) throw new Error('Service role client unavailable.');

  const { data: rows } = await service
    .from('payouts')
    .select(
      'id, tester_id, amount_cents, status, paid_at, stripe_payout_id, stripe_transfer_id, idempotency_key, failure_reason, created_at'
    )
    .eq('tester_id', user.id)
    .order('created_at', { ascending: false });

  const payouts = (rows ?? []) as PayoutRow[];
  const pending = payouts
    .filter((p) => p.status === 'pending')
    .reduce((a, p) => a + p.amount_cents, 0);
  const paid = payouts.filter((p) => p.status === 'paid').reduce((a, p) => a + p.amount_cents, 0);

  const { data: tester } = await service
    .from('testers')
    .select(
      'email, stripe_account_id, stripe_charges_enabled, stripe_payouts_enabled, stripe_details_submitted'
    )
    .eq('id', user.id)
    .single();

  const accountId = tester?.stripe_account_id ?? null;
  const connectState: ConnectState = !accountId
    ? 'none'
    : tester?.stripe_charges_enabled && tester?.stripe_payouts_enabled
      ? 'ready'
      : 'incomplete';

  // Pull live Stripe payout history when the account is fully set up.
  let stripePayouts: StripePayout[] = [];
  if (connectState === 'ready' && accountId) {
    try {
      const list = await getStripeClient().listPayouts({ accountId, limit: 10 });
      stripePayouts = list.data;
    } catch {
      // Non-fatal: degrade to DB-only history.
      stripePayouts = [];
    }
  }

  return {
    payouts,
    stripePayouts,
    pendingCents: pending,
    paidCents: paid,
    email: tester?.email ?? user.email ?? '',
    connectState,
    stripeAccountId: accountId,
    flashError,
    flashInfo,
  };
}

export default async function PayoutsPage({
  searchParams,
}: {
  searchParams: { stripe_return?: string; stripe_error?: string };
}) {
  // Howard 2026-05-07 IRON-LAW hard-gates.
  if (!isSupabaseConfigured()) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-16">
        <NotConfigured
          service="Supabase"
          envVars={[
            'NEXT_PUBLIC_SUPABASE_URL',
            'NEXT_PUBLIC_SUPABASE_ANON_KEY',
            'SUPABASE_SERVICE_ROLE_KEY',
          ]}
          docsUrl="/docs#supabase-setup"
        />
      </div>
    );
  }
  if (!isStripeConfigured()) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-16">
        <NotConfigured
          service="Stripe Connect"
          envVars={['STRIPE_SECRET_KEY', 'NEXT_PUBLIC_SITE_URL']}
          docsUrl="/docs#stripe-connect"
        />
      </div>
    );
  }

  const data = await loadPageData(searchParams);
  const {
    payouts,
    stripePayouts,
    pendingCents,
    paidCents,
    email,
    connectState,
    flashError,
    flashInfo,
  } = data;

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <div className="flex items-end justify-between gap-3 mb-8">
        <div>
          <h1 className="text-3xl font-bold">Payouts</h1>
          <p className="text-sm text-oyster-300 mt-1">{email}</p>
        </div>
        <Link href="/dashboard" className="btn-ghost">
          ← Back to dashboard
        </Link>
      </div>

      {flashInfo && (
        <div className="mb-6 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-sm text-emerald-300">
          {flashInfo}
        </div>
      )}
      {flashError && (
        <div className="mb-6 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-300">
          Stripe error: {flashError}
        </div>
      )}

      {/* Stripe Connect card — three states ----------------------------- */}
      <StripeConnectCard state={connectState} />

      {/* Aggregates ---------------------------------------------------- */}
      <div className="grid grid-cols-2 gap-4 mb-8">
        <div className="card p-5">
          <div className="text-xs uppercase tracking-wider text-oyster-400">Pending</div>
          <div className="mt-1 text-3xl font-bold text-amber-accent">
            {formatCents(pendingCents)}
          </div>
          <div className="mt-1 text-xs text-oyster-400">
            Pays out automatically when ≥ {formatCents(env.minPayoutCents)}
          </div>
        </div>
        <div className="card p-5">
          <div className="text-xs uppercase tracking-wider text-oyster-400">Paid all-time</div>
          <div className="mt-1 text-3xl font-bold">{formatCents(paidCents)}</div>
          <div className="mt-1 text-xs text-oyster-400">
            {payouts.filter((p) => p.status === 'paid').length} transfers
          </div>
        </div>
      </div>

      {/* Local DB history (always shown) ------------------------------- */}
      <h2 className="text-xl font-semibold mb-3">Transaction history</h2>
      <div className="card overflow-hidden mb-8">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-oyster-950/40 text-xs uppercase tracking-wider text-oyster-400">
              <tr>
                <th className="text-left px-4 py-3 font-semibold">Created</th>
                <th className="text-left px-4 py-3 font-semibold">Amount</th>
                <th className="text-left px-4 py-3 font-semibold">Status</th>
                <th className="text-left px-4 py-3 font-semibold">Paid</th>
                <th className="text-left px-4 py-3 font-semibold">Stripe ref</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-oyster-800/40">
              {payouts.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-oyster-400">
                    No payouts yet — earn at least {formatCents(env.minPayoutCents)} to trigger
                    your first payout.
                  </td>
                </tr>
              )}
              {payouts.map((p) => (
                <tr key={p.id} className="hover:bg-oyster-800/30">
                  <td className="px-4 py-3 whitespace-nowrap">{formatRelativeTime(p.created_at)}</td>
                  <td className="px-4 py-3 font-semibold">{formatCents(p.amount_cents)}</td>
                  <td className="px-4 py-3">
                    <span className={`tag tag-${p.status}`}>{p.status}</span>
                  </td>
                  <td className="px-4 py-3 text-oyster-300">
                    {p.paid_at ? formatRelativeTime(p.paid_at) : '—'}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-oyster-400">
                    {p.stripe_payout_id ?? p.stripe_transfer_id ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Live Stripe payout history (only when connected) -------------- */}
      {connectState === 'ready' && stripePayouts.length > 0 && (
        <>
          <h2 className="text-xl font-semibold mb-3">Stripe payout history (last 10)</h2>
          <p className="text-xs text-oyster-400 mb-3">
            Pulled live from Stripe — reflects bank-side transfer status.
          </p>
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-oyster-950/40 text-xs uppercase tracking-wider text-oyster-400">
                  <tr>
                    <th className="text-left px-4 py-3 font-semibold">Created</th>
                    <th className="text-left px-4 py-3 font-semibold">Amount</th>
                    <th className="text-left px-4 py-3 font-semibold">Status</th>
                    <th className="text-left px-4 py-3 font-semibold">Arrival</th>
                    <th className="text-left px-4 py-3 font-semibold">Payout ID</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-oyster-800/40">
                  {stripePayouts.map((p) => (
                    <tr key={p.id} className="hover:bg-oyster-800/30">
                      <td className="px-4 py-3">
                        {formatRelativeTime(new Date(p.created * 1000).toISOString())}
                      </td>
                      <td className="px-4 py-3 font-semibold">{formatCents(p.amount)}</td>
                      <td className="px-4 py-3">
                        <span className={`tag tag-${p.status === 'paid' ? 'paid' : 'pending'}`}>
                          {p.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-oyster-300">
                        {new Date(p.arrival_date * 1000).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-oyster-400">{p.id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function StripeConnectCard({ state }: { state: ConnectState }) {
  if (state === 'none') {
    return (
      <div className="card p-5 mb-8 border-amber-accent/40">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold text-oyster-50">
              Connect a bank account to receive payouts
            </h3>
            <p className="text-sm text-oyster-300 mt-1">
              We use{' '}
              <a
                href="https://stripe.com/connect"
                target="_blank"
                rel="noreferrer"
                className="underline hover:text-oyster-100"
              >
                Stripe Connect
              </a>{' '}
              to send payouts. One-time onboarding takes a few minutes — you'll need a US bank
              account or debit card.
            </p>
          </div>
          <StripeConnectButton
            endpoint="/api/stripe/connect/onboard"
            label="Connect bank account"
          />
        </div>
      </div>
    );
  }

  if (state === 'incomplete') {
    return (
      <div className="card p-5 mb-8 border-amber-accent/40">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold text-oyster-50">Stripe onboarding incomplete</h3>
            <p className="text-sm text-oyster-300 mt-1">
              You started Stripe onboarding but Stripe still needs a few more details before
              payouts can be sent. No earnings are lost — they accumulate as Pending.
            </p>
          </div>
          <StripeConnectButton endpoint="/api/stripe/connect/onboard" label="Finish setup" />
        </div>
      </div>
    );
  }

  return (
    <div className="card p-5 mb-8 border-emerald-500/30">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-oyster-50">Bank account connected ✓</h3>
          <p className="text-sm text-oyster-300 mt-1">
            Payouts run daily and land in your bank within 2–5 business days. Manage your account
            details (bank, tax info, payout schedule) on Stripe.
          </p>
        </div>
        <StripeConnectButton
          endpoint="/api/stripe/connect/dashboard"
          label="Manage on Stripe"
          variant="ghost"
        />
      </div>
    </div>
  );
}
