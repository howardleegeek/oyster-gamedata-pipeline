-- =====================================================================
-- Oyster GameData — Tester Web Portal: initial schema
-- =====================================================================
-- Apply with `supabase db reset` (local) or via the Supabase dashboard
-- SQL editor (production). Idempotent on re-run.
-- =====================================================================

create extension if not exists "pgcrypto";

-- ----- testers ---------------------------------------------------------
-- One row per signed-up tester. Linked 1:1 with auth.users via id.
create table if not exists public.testers (
  id                  uuid primary key references auth.users(id) on delete cascade,
  email               text not null unique,
  github_handle       text,
  github_id           text,
  created_at          timestamptz not null default now(),
  total_hours         numeric(10, 2) not null default 0,
  total_earnings_cents bigint not null default 0,
  stripe_account_id   text -- filled in once the tester onboards via Stripe Connect
);

create index if not exists testers_github_id_idx on public.testers(github_id);

-- ----- tarballs --------------------------------------------------------
-- One row per uploaded recording bundle.
create table if not exists public.tarballs (
  id                uuid primary key default gen_random_uuid(),
  tester_id         uuid not null references public.testers(id) on delete cascade,
  uploaded_at       timestamptz not null default now(),
  size_bytes        bigint not null,
  sha256            text not null,
  duration_seconds  integer not null default 0,
  d5_verdict        text not null default 'pending'
                    check (d5_verdict in ('pending', 'accepted', 'rejected')),
  d5_score          numeric(4, 3),
  storage_path      text not null, -- key inside the `tarballs` bucket
  paid              boolean not null default false
);

create unique index if not exists tarballs_sha256_idx on public.tarballs(sha256);
create index if not exists tarballs_tester_id_idx on public.tarballs(tester_id, uploaded_at desc);

-- ----- payouts ---------------------------------------------------------
create table if not exists public.payouts (
  id                  uuid primary key default gen_random_uuid(),
  tester_id           uuid not null references public.testers(id) on delete cascade,
  amount_cents        bigint not null check (amount_cents > 0),
  status              text not null default 'pending'
                      check (status in ('pending', 'paid', 'failed')),
  paid_at             timestamptz,
  stripe_payout_id    text,
  created_at          timestamptz not null default now()
);

create index if not exists payouts_tester_id_idx on public.payouts(tester_id, created_at desc);

-- ----- aggregate trigger ----------------------------------------------
-- Keep `testers.total_hours` and `testers.total_earnings_cents` in sync.
create or replace function public.recompute_tester_aggregates(p_tester uuid)
returns void
language plpgsql
security definer
as $$
declare
  v_hours numeric(10, 2);
  v_earnings bigint;
begin
  select coalesce(sum(duration_seconds) / 3600.0, 0)
    into v_hours
    from public.tarballs
    where tester_id = p_tester
      and d5_verdict = 'accepted';

  -- Earnings = sum(paid payouts).
  select coalesce(sum(amount_cents), 0)
    into v_earnings
    from public.payouts
    where tester_id = p_tester
      and status = 'paid';

  update public.testers
    set total_hours = v_hours,
        total_earnings_cents = v_earnings
    where id = p_tester;
end;
$$;

create or replace function public.tarballs_after_change()
returns trigger
language plpgsql
as $$
begin
  perform public.recompute_tester_aggregates(coalesce(new.tester_id, old.tester_id));
  return null;
end;
$$;

drop trigger if exists tarballs_after_change_trg on public.tarballs;
create trigger tarballs_after_change_trg
  after insert or update or delete on public.tarballs
  for each row execute function public.tarballs_after_change();

create or replace function public.payouts_after_change()
returns trigger
language plpgsql
as $$
begin
  perform public.recompute_tester_aggregates(coalesce(new.tester_id, old.tester_id));
  return null;
end;
$$;

drop trigger if exists payouts_after_change_trg on public.payouts;
create trigger payouts_after_change_trg
  after insert or update or delete on public.payouts
  for each row execute function public.payouts_after_change();

-- ----- row-level security --------------------------------------------
alter table public.testers   enable row level security;
alter table public.tarballs  enable row level security;
alter table public.payouts   enable row level security;

-- Each tester can read/update their own row.
drop policy if exists "tester reads own profile" on public.testers;
create policy "tester reads own profile" on public.testers
  for select using (auth.uid() = id);

drop policy if exists "tester updates own profile" on public.testers;
create policy "tester updates own profile" on public.testers
  for update using (auth.uid() = id);

drop policy if exists "tester reads own tarballs" on public.tarballs;
create policy "tester reads own tarballs" on public.tarballs
  for select using (auth.uid() = tester_id);

drop policy if exists "tester reads own payouts" on public.payouts;
create policy "tester reads own payouts" on public.payouts
  for select using (auth.uid() = tester_id);

-- Service role bypasses RLS — no policies needed for the upload endpoint.

-- ----- storage bucket -------------------------------------------------
-- Public bucket creation is a Supabase-API call (not SQL); we issue it
-- here for environments that support it. If the bucket already exists,
-- the call is a no-op.
insert into storage.buckets (id, name, public)
  values ('tarballs', 'tarballs', false)
  on conflict (id) do nothing;

-- Storage policy: only the owner (tester_id encoded as the first folder
-- in the storage_path) can read their own files via the API.
drop policy if exists "tester reads own tarball blobs" on storage.objects;
create policy "tester reads own tarball blobs" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'tarballs'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- ----- handy seed: auto-provision testers row on signup --------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
as $$
begin
  insert into public.testers (id, email, github_handle, github_id)
    values (
      new.id,
      new.email,
      new.raw_user_meta_data ->> 'user_name',
      new.raw_user_meta_data ->> 'provider_id'
    )
    on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
