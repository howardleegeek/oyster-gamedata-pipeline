-- =====================================================================
-- Stripe Connect — schema additions
-- =====================================================================
-- Adds the columns used by the Connect onboarding flow + the payout cron
-- job. Idempotent on re-run: every column / index / policy is guarded.
--
-- Apply with `supabase db reset` (local) or paste into the Supabase
-- dashboard SQL editor (production).
-- =====================================================================

-- ----- testers.stripe_account_id (already declared in init.sql, but
--       guard anyway for older deployments that pre-date that field) ----
alter table public.testers
  add column if not exists stripe_account_id text;

-- New: track Stripe Connect onboarding state without an extra round-trip
-- to the Stripe API on every page render. `null` until the tester finishes
-- their first onboarding session.
alter table public.testers
  add column if not exists stripe_charges_enabled boolean not null default false;

alter table public.testers
  add column if not exists stripe_payouts_enabled boolean not null default false;

alter table public.testers
  add column if not exists stripe_details_submitted boolean not null default false;

create index if not exists testers_stripe_account_id_idx
  on public.testers(stripe_account_id)
  where stripe_account_id is not null;

-- ----- payouts.idempotency_key ----------------------------------------
-- The cron job derives this from `tester_id || '-' || payout_date` so a
-- repeated run on the same day cannot create duplicate Stripe transfers.
-- Unique index makes the database itself the second line of defence.
alter table public.payouts
  add column if not exists idempotency_key text;

alter table public.payouts
  add column if not exists stripe_transfer_id text;

alter table public.payouts
  add column if not exists failure_reason text;

create unique index if not exists payouts_idempotency_key_idx
  on public.payouts(idempotency_key)
  where idempotency_key is not null;

create index if not exists payouts_status_idx
  on public.payouts(status, created_at desc);

-- ----- view: tester_unpaid_balance ------------------------------------
-- Sum of accepted-but-unpaid earnings per tester. The cron script reads
-- from this view rather than recomputing the join in Python.
--
-- Earnings model: `accepted` tarballs at GAMEDATA_RATE_PER_HOUR_CENTS,
-- minus the sum of `paid` payouts. The rate is *not* in the database
-- (it lives in the env), so the view exposes raw seconds and the cron
-- script applies the rate.
create or replace view public.tester_unpaid_balance as
  with earned as (
    select
      tester_id,
      coalesce(sum(duration_seconds), 0) as accepted_seconds
    from public.tarballs
    where d5_verdict = 'accepted'
    group by tester_id
  ),
  paid as (
    select
      tester_id,
      coalesce(sum(amount_cents), 0) as paid_cents
    from public.payouts
    where status = 'paid'
    group by tester_id
  )
  select
    t.id as tester_id,
    t.email,
    t.stripe_account_id,
    t.stripe_payouts_enabled,
    coalesce(e.accepted_seconds, 0) as accepted_seconds,
    coalesce(p.paid_cents, 0) as paid_cents
  from public.testers t
  left join earned e on e.tester_id = t.id
  left join paid   p on p.tester_id = t.id;

comment on view public.tester_unpaid_balance is
  'Cron-side input for payout_cron.py: yields per-tester unpaid balance. '
  'Caller multiplies accepted_seconds * (rate_per_hour_cents / 3600) and '
  'subtracts paid_cents to compute the transfer amount.';
