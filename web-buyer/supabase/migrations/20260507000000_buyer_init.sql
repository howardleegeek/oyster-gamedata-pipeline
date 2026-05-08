-- =====================================================================
-- Oyster GameData — Buyer Web Portal: initial schema
-- =====================================================================
-- This migration ADDS the buyer-side tables on top of the existing
-- tester-side schema (web-tester/supabase/migrations/20260507000000_init.sql).
--
-- DEV (shared project): apply BOTH migrations against the same Supabase
-- project. The buyer migration is additive — it only references existing
-- tables (`tarballs`, `auth.users`).
-- PROD (separate projects): mirror the relevant parts of the tester
-- schema (just the `tarballs` shape) before applying this file.
--
-- Idempotent on re-run.
-- =====================================================================

create extension if not exists "pgcrypto";

-- ----- buyers ----------------------------------------------------------
-- One row per signed-up buyer. Linked 1:1 with auth.users via id.
create table if not exists public.buyers (
  id                  uuid primary key references auth.users(id) on delete cascade,
  email               text not null unique,
  github_id           text,
  github_handle       text,
  company_name        text,
  created_at          timestamptz not null default now(),
  total_spent_cents   bigint not null default 0
);

create index if not exists buyers_github_id_idx on public.buyers(github_id);

-- ----- catalog_metadata ------------------------------------------------
-- Curated buyer-facing metadata for tarballs. Keyed 1:1 by tarball_id;
-- absence of a row is fine — the buyer portal falls back to derived
-- defaults so the catalog is never empty.
create table if not exists public.catalog_metadata (
  tarball_id          uuid primary key references public.tarballs(id) on delete cascade,
  title               text not null,
  description         text not null,
  mc_task_type        text not null
                      check (mc_task_type in (
                        'survival', 'creative', 'redstone', 'pvp',
                        'mining', 'building', 'exploration', 'farming'
                      )),
  price_cents         bigint not null check (price_cents > 0),
  poster_url          text,
  video_preview_url   text,
  curated_at          timestamptz not null default now()
);

create index if not exists catalog_metadata_task_idx on public.catalog_metadata(mc_task_type);
create index if not exists catalog_metadata_price_idx on public.catalog_metadata(price_cents);

-- ----- purchases -------------------------------------------------------
-- One row per (buyer, tarball) license. A buyer can purchase the same
-- tarball multiple times (e.g. after a license type change) — the
-- (buyer_id, tarball_id, stripe_session_id) tuple is unique to make the
-- webhook idempotent against retries.
create table if not exists public.purchases (
  id                  uuid primary key default gen_random_uuid(),
  buyer_id            uuid not null references public.buyers(id) on delete cascade,
  tarball_id          uuid not null references public.tarballs(id) on delete restrict,
  amount_cents        bigint not null check (amount_cents >= 0),
  stripe_session_id   text,
  purchased_at        timestamptz not null default now(),
  license_id          uuid -- back-reference, populated by trigger below
);

create unique index if not exists purchases_buyer_tarball_session_idx
  on public.purchases (buyer_id, tarball_id, coalesce(stripe_session_id, ''));
create index if not exists purchases_buyer_idx on public.purchases(buyer_id, purchased_at desc);

-- ----- licenses --------------------------------------------------------
-- One row per purchase. We separate this from purchases.row to make it
-- easy to issue multiple license certificates (e.g. research → commercial
-- upgrade) against the same purchase in the future.
create table if not exists public.licenses (
  id                  uuid primary key default gen_random_uuid(),
  purchase_id         uuid not null references public.purchases(id) on delete cascade,
  type                text not null check (type in ('research', 'commercial')),
  terms_url           text not null,
  issued_at           timestamptz not null default now()
);

create unique index if not exists licenses_purchase_idx on public.licenses(purchase_id);

-- ----- cart_items ------------------------------------------------------
create table if not exists public.cart_items (
  id                  uuid primary key default gen_random_uuid(),
  buyer_id            uuid not null references public.buyers(id) on delete cascade,
  tarball_id          uuid not null references public.tarballs(id) on delete cascade,
  added_at            timestamptz not null default now()
);

create unique index if not exists cart_items_buyer_tarball_idx
  on public.cart_items(buyer_id, tarball_id);
create index if not exists cart_items_buyer_idx
  on public.cart_items(buyer_id, added_at desc);

-- ----- aggregate trigger ----------------------------------------------
-- Keep `buyers.total_spent_cents` in sync with sum(purchases.amount_cents).
create or replace function public.recompute_buyer_aggregates(p_buyer uuid)
returns void
language plpgsql
security definer
as $$
declare
  v_spent bigint;
begin
  select coalesce(sum(amount_cents), 0)
    into v_spent
    from public.purchases
    where buyer_id = p_buyer;

  update public.buyers
    set total_spent_cents = v_spent
    where id = p_buyer;
end;
$$;

create or replace function public.purchases_after_change()
returns trigger
language plpgsql
as $$
begin
  perform public.recompute_buyer_aggregates(coalesce(new.buyer_id, old.buyer_id));
  return null;
end;
$$;

drop trigger if exists purchases_after_change_trg on public.purchases;
create trigger purchases_after_change_trg
  after insert or update or delete on public.purchases
  for each row execute function public.purchases_after_change();

-- ----- license back-reference ----------------------------------------
-- When a license row is inserted, populate purchases.license_id so the
-- /downloads page can join purchases→licenses without a separate query.
create or replace function public.licenses_after_insert()
returns trigger
language plpgsql
as $$
begin
  update public.purchases
    set license_id = new.id
    where id = new.purchase_id;
  return null;
end;
$$;

drop trigger if exists licenses_after_insert_trg on public.licenses;
create trigger licenses_after_insert_trg
  after insert on public.licenses
  for each row execute function public.licenses_after_insert();

-- ----- row-level security --------------------------------------------
alter table public.buyers           enable row level security;
alter table public.purchases        enable row level security;
alter table public.licenses         enable row level security;
alter table public.cart_items       enable row level security;
alter table public.catalog_metadata enable row level security;

-- Each buyer can read/update their own row.
drop policy if exists "buyer reads own profile" on public.buyers;
create policy "buyer reads own profile" on public.buyers
  for select using (auth.uid() = id);

drop policy if exists "buyer updates own profile" on public.buyers;
create policy "buyer updates own profile" on public.buyers
  for update using (auth.uid() = id);

-- Purchases / licenses / cart_items: own-row-only reads.
drop policy if exists "buyer reads own purchases" on public.purchases;
create policy "buyer reads own purchases" on public.purchases
  for select using (auth.uid() = buyer_id);

drop policy if exists "buyer reads own licenses" on public.licenses;
create policy "buyer reads own licenses" on public.licenses
  for select using (
    exists (
      select 1 from public.purchases p
      where p.id = licenses.purchase_id and p.buyer_id = auth.uid()
    )
  );

drop policy if exists "buyer rw own cart_items" on public.cart_items;
create policy "buyer rw own cart_items" on public.cart_items
  for all using (auth.uid() = buyer_id) with check (auth.uid() = buyer_id);

-- catalog_metadata is publicly readable so the /browse page works for
-- anonymous visitors.
drop policy if exists "anyone reads catalog_metadata" on public.catalog_metadata;
create policy "anyone reads catalog_metadata" on public.catalog_metadata
  for select using (true);

-- Service role bypasses RLS — no policies needed for the trusted server
-- code that signs download URLs and writes purchases from the webhook.

-- ----- handy seed: auto-provision buyers row on signup --------------
-- Distinct trigger function name to avoid clobbering the tester seed when
-- both portals share the same project.
create or replace function public.handle_new_buyer()
returns trigger
language plpgsql
security definer
as $$
begin
  insert into public.buyers (id, email, github_handle, github_id, company_name)
    values (
      new.id,
      new.email,
      new.raw_user_meta_data ->> 'user_name',
      new.raw_user_meta_data ->> 'provider_id',
      new.raw_user_meta_data ->> 'company_name'
    )
    on conflict (id) do nothing;
  return new;
end;
$$;

-- Fire AFTER the tester portal's `on_auth_user_created` so both rows
-- exist when the user signs in. If the tester portal isn't installed,
-- the trigger still fires standalone.
drop trigger if exists on_auth_user_created_buyer on auth.users;
create trigger on_auth_user_created_buyer
  after insert on auth.users
  for each row execute function public.handle_new_buyer();

-- =====================================================================
-- Optional: seed catalog_metadata for any tarballs that already exist in
-- the shared project. Skips tarballs that are already curated.
-- =====================================================================
insert into public.catalog_metadata (
  tarball_id, title, description, mc_task_type, price_cents
)
select
  t.id,
  'Minecraft session — ' || round(t.duration_seconds / 3600.0, 1) || ' h',
  'Auto-curated tarball seeded by the buyer migration. Replace via the curation admin.',
  'survival',
  greatest(ceil((t.size_bytes::numeric / 1024 / 1024 / 1024) * 2500)::bigint, 100)
from public.tarballs t
where not exists (
  select 1 from public.catalog_metadata cm where cm.tarball_id = t.id
)
  and t.d5_verdict = 'accepted';
