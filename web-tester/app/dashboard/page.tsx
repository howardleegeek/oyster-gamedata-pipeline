import Link from 'next/link';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../lib/supabase-server';
import { isSupabaseConfigured, env } from '../../lib/env';
import { formatCents, formatHours, formatBytes, formatRelativeTime } from '../../lib/format';
import { StatCard } from '../../components/StatCard';
import { NotConfigured } from '../../components/NotConfigured';
import type { TesterStats, TarballRow } from '../../types/database';

export const dynamic = 'force-dynamic';

// Howard 2026-05-07 IRON-LAW: dashboard renders REAL tester data only.
// If Supabase isn't configured, show a hard-gate NotConfigured panel —
// never fabricate "24.7 hours / $148.20 earnings" sample numbers. The
// previous DEV MODE fallback to lib/sample-data.ts has been deleted.
async function loadDashboard(): Promise<{
  stats: TesterStats;
  tarballs: TarballRow[];
}> {
  const supabase = getSupabaseServerClient();
  if (!supabase) {
    // Caller must check isSupabaseConfigured() before invoking us.
    throw new Error('Supabase not configured — caller should render <NotConfigured>.');
  }

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    // Middleware should have redirected already.
    throw new Error('Not authenticated.');
  }

  const service = getSupabaseServiceClient();
  if (!service) throw new Error('Service role client unavailable.');

  // Tester profile
  const { data: tester } = await service
    .from('testers')
    .select('id, email, github_handle, total_hours, total_earnings_cents, created_at')
    .eq('id', user.id)
    .single();

  // Recent tarballs
  const { data: tarballs } = await service
    .from('tarballs')
    .select('id, tester_id, uploaded_at, size_bytes, sha256, duration_seconds, d5_verdict, d5_score, paid')
    .eq('tester_id', user.id)
    .order('uploaded_at', { ascending: false })
    .limit(20);

  // Pending = sum(payouts.amount_cents where status='pending')
  const { data: pendingRows } = await service
    .from('payouts')
    .select('amount_cents, status')
    .eq('tester_id', user.id);

  const pending = (pendingRows ?? [])
    .filter((p: any) => p.status === 'pending')
    .reduce((acc: number, p: any) => acc + p.amount_cents, 0);
  const paid = (pendingRows ?? [])
    .filter((p: any) => p.status === 'paid')
    .reduce((acc: number, p: any) => acc + p.amount_cents, 0);

  const stats: TesterStats = {
    tester_id: user.id,
    email: tester?.email ?? user.email ?? 'unknown@example.com',
    github_handle: tester?.github_handle ?? null,
    total_hours: Number(tester?.total_hours ?? 0),
    total_earnings_cents: Number(tester?.total_earnings_cents ?? 0),
    pending_earnings_cents: pending,
    paid_earnings_cents: paid,
    upload_count: tarballs?.length ?? 0,
    last_upload_at: tarballs?.[0]?.uploaded_at ?? null,
    joined_at: tester?.created_at ?? new Date().toISOString(),
  };

  return { stats, tarballs: (tarballs ?? []) as TarballRow[] };
}

export default async function DashboardPage() {
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
  const { stats, tarballs } = await loadDashboard();

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3 mb-8">
        <div>
          <h1 className="text-3xl font-bold">Welcome back</h1>
          <p className="text-sm text-oyster-300 mt-1">
            Signed in as <span className="text-oyster-100">{stats.email}</span>
            {stats.github_handle && (
              <>
                {' '}· GitHub <span className="text-oyster-100">@{stats.github_handle}</span>
              </>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/download" className="btn-primary">Download recorder</Link>
          <Link href="/payouts" className="btn-secondary">Manage payouts</Link>
        </div>
      </div>

      {/* Stat grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
        <StatCard
          label="Total hours"
          value={formatHours(stats.total_hours)}
          hint={stats.last_upload_at ? `Last upload ${formatRelativeTime(stats.last_upload_at)}` : 'No uploads yet'}
        />
        <StatCard
          label="Total earned"
          value={formatCents(stats.total_earnings_cents)}
          hint={`At ${formatCents(env.ratePerHourCents)}/hr`}
          accent
        />
        <StatCard
          label="Pending"
          value={formatCents(stats.pending_earnings_cents)}
          hint={`Min payout ${formatCents(env.minPayoutCents)}`}
        />
        <StatCard
          label="Uploads"
          value={String(stats.upload_count)}
          hint={`Joined ${formatRelativeTime(stats.joined_at)}`}
        />
      </div>

      {/* Recent tarballs */}
      <section>
        <h2 className="text-xl font-semibold mb-4">Recent uploads</h2>
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-oyster-950/40 text-xs uppercase tracking-wider text-oyster-400">
                <tr>
                  <th className="text-left  px-4 py-3 font-semibold">Uploaded</th>
                  <th className="text-left  px-4 py-3 font-semibold">Duration</th>
                  <th className="text-left  px-4 py-3 font-semibold">Size</th>
                  <th className="text-left  px-4 py-3 font-semibold">Verdict</th>
                  <th className="text-left  px-4 py-3 font-semibold">Paid</th>
                  <th className="text-right px-4 py-3 font-semibold">SHA-256</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-oyster-800/40">
                {tarballs.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center text-oyster-400">
                      No uploads yet —{' '}
                      <Link href="/download" className="text-amber-accent hover:underline">
                        download the recorder
                      </Link>{' '}
                      to get started.
                    </td>
                  </tr>
                )}
                {tarballs.map((t) => (
                  <tr key={t.id} className="hover:bg-oyster-800/30">
                    <td className="px-4 py-3 whitespace-nowrap">
                      {formatRelativeTime(t.uploaded_at)}
                    </td>
                    <td className="px-4 py-3">{formatHours(t.duration_seconds / 3600)}</td>
                    <td className="px-4 py-3">{formatBytes(t.size_bytes)}</td>
                    <td className="px-4 py-3">
                      <span className={`tag tag-${t.d5_verdict}`}>
                        {t.d5_verdict}
                        {t.d5_score != null && ` · ${(t.d5_score * 100).toFixed(0)}`}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {t.paid ? (
                        <span className="tag tag-paid">paid</span>
                      ) : (
                        <span className="tag tag-pending">pending</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-xs text-oyster-400">
                      {t.sha256.slice(0, 12)}…
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}
