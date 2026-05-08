import Link from 'next/link';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../lib/supabase-server';
import { isSupabaseConfigured, env } from '../../lib/env';
import { samplePayouts, sampleStats } from '../../lib/sample-data';
import { formatCents, formatRelativeTime } from '../../lib/format';
import type { PayoutRow } from '../../types/database';

export const dynamic = 'force-dynamic';

async function loadPayouts(): Promise<{
  payouts: PayoutRow[];
  pendingCents: number;
  paidCents: number;
  email: string;
  liveMode: boolean;
  stripeConnected: boolean;
}> {
  if (!isSupabaseConfigured()) {
    const fakeId = '00000000-0000-0000-0000-000000000001';
    const stats = sampleStats(fakeId);
    const payouts = samplePayouts(fakeId);
    return {
      payouts,
      pendingCents: stats.pending_earnings_cents,
      paidCents: stats.paid_earnings_cents,
      email: stats.email,
      liveMode: false,
      stripeConnected: false,
    };
  }

  const supabase = getSupabaseServerClient();
  if (!supabase) throw new Error('Supabase server client unavailable.');
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not signed in (middleware should have redirected).');

  const service = getSupabaseServiceClient();
  if (!service) throw new Error('Service role client unavailable.');

  const { data: rows } = await service
    .from('payouts')
    .select('id, tester_id, amount_cents, status, paid_at, stripe_payout_id, created_at')
    .eq('tester_id', user.id)
    .order('created_at', { ascending: false });

  const payouts = (rows ?? []) as PayoutRow[];
  const pending = payouts.filter((p) => p.status === 'pending').reduce((a, p) => a + p.amount_cents, 0);
  const paid = payouts.filter((p) => p.status === 'paid').reduce((a, p) => a + p.amount_cents, 0);

  const { data: tester } = await service
    .from('testers')
    .select('email, stripe_account_id')
    .eq('id', user.id)
    .single();

  return {
    payouts,
    pendingCents: pending,
    paidCents: paid,
    email: tester?.email ?? user.email ?? '',
    liveMode: true,
    stripeConnected: Boolean(tester?.stripe_account_id),
  };
}

export default async function PayoutsPage() {
  const { payouts, pendingCents, paidCents, email, liveMode, stripeConnected } = await loadPayouts();

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <div className="flex items-end justify-between gap-3 mb-8">
        <div>
          <h1 className="text-3xl font-bold">Payouts</h1>
          <p className="text-sm text-oyster-300 mt-1">{email}</p>
        </div>
        <Link href="/dashboard" className="btn-ghost">← Back to dashboard</Link>
      </div>

      {!liveMode && (
        <div className="mb-6 p-3 rounded-lg bg-amber-accent/10 border border-amber-accent/30 text-sm text-amber-accent">
          [DEV MODE: showing sample data]
        </div>
      )}

      {/* Stripe Connect placeholder — to be wired once accounts go live */}
      <div className={`card p-5 mb-8 ${stripeConnected ? 'border-emerald-500/30' : 'border-amber-accent/40'}`}>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold text-oyster-50">
              {stripeConnected ? 'Bank account connected ✓' : 'Connect a bank account to receive payouts'}
            </h3>
            <p className="text-sm text-oyster-300 mt-1">
              We use{' '}
              <a href="https://stripe.com/connect" target="_blank" rel="noreferrer" className="underline hover:text-oyster-100">
                Stripe Connect
              </a>{' '}
              to send weekly payouts. One-time onboarding.
            </p>
          </div>
          <button
            className="btn-primary"
            disabled
            title="Stripe Connect integration is enabled by Howard once Stripe account is provisioned."
          >
            {stripeConnected ? 'Manage payout method' : 'Connect Stripe (coming soon)'}
          </button>
        </div>
      </div>

      {/* Aggregates */}
      <div className="grid grid-cols-2 gap-4 mb-8">
        <div className="card p-5">
          <div className="text-xs uppercase tracking-wider text-oyster-400">Pending</div>
          <div className="mt-1 text-3xl font-bold text-amber-accent">{formatCents(pendingCents)}</div>
          <div className="mt-1 text-xs text-oyster-400">
            Pays out automatically when ≥ {formatCents(env.minPayoutCents)}
          </div>
        </div>
        <div className="card p-5">
          <div className="text-xs uppercase tracking-wider text-oyster-400">Paid all-time</div>
          <div className="mt-1 text-3xl font-bold">{formatCents(paidCents)}</div>
          <div className="mt-1 text-xs text-oyster-400">{payouts.filter((p) => p.status === 'paid').length} transfers</div>
        </div>
      </div>

      {/* History */}
      <h2 className="text-xl font-semibold mb-3">Transaction history</h2>
      <div className="card overflow-hidden">
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
                    No payouts yet — earn at least {formatCents(env.minPayoutCents)} to trigger your first payout.
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
                    {p.stripe_payout_id ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
