/**
 * Database row shapes — match the SQL schema in
 * `supabase/migrations/20260507000000_init.sql`.
 *
 * Kept as plain interfaces so they're shareable between server + client
 * without pulling in the @supabase/supabase-js Database<...> generic.
 */

export interface TesterRow {
  id: string;
  email: string;
  github_handle: string | null;
  github_id: string | null;
  created_at: string;
  total_hours: number;
  total_earnings_cents: number;
  stripe_account_id: string | null;
}

export interface TarballRow {
  id: string;
  tester_id: string;
  uploaded_at: string;
  size_bytes: number;
  sha256: string;
  duration_seconds: number;
  d5_verdict: 'pending' | 'accepted' | 'rejected';
  d5_score: number | null;
  paid: boolean;
}

export interface PayoutRow {
  id: string;
  tester_id: string;
  amount_cents: number;
  status: 'pending' | 'paid' | 'failed';
  paid_at: string | null;
  stripe_payout_id: string | null;
  created_at: string;
}

/**
 * Aggregated view returned by /api/stats/[testerId] — flatter than
 * the raw rows to make dashboard rendering trivial.
 */
export interface TesterStats {
  tester_id: string;
  email: string;
  github_handle: string | null;
  total_hours: number;
  total_earnings_cents: number;
  pending_earnings_cents: number;
  paid_earnings_cents: number;
  upload_count: number;
  last_upload_at: string | null;
  joined_at: string;
}
