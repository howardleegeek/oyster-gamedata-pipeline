-- =============================================================================
-- Gap #8 — direct-to-Supabase signed-URL upload split.
--
-- The legacy /api/upload-tarball POSTed the binary through Next.js, which works
-- locally but trips Vercel's 4.5 MB body cap in production. The new flow is:
--
--   1. recorder POSTs metadata to /api/upload-tarball/sign
--      -> server inserts a `tarballs` row with upload_status='pending_upload'
--         and returns a Supabase Storage signed PUT URL (15 min TTL).
--   2. recorder PUTs the tarball directly to Supabase (browser/recorder ->
--      Supabase, Vercel bypassed).
--   3. recorder POSTs to /api/upload-tarball/finalize
--      -> server HEADs the storage object, verifies size + sha256, flips
--         upload_status='uploaded'.
--
-- We add:
--   - `upload_status` enum-style text column (pending_upload | uploaded | failed)
--   - `signed_url_expires_at` so a follow-up reaper can clean up abandoned rows
--   - dedicated `tarball-uploads` bucket so signed-PUT keys don't pollute the
--     legacy `tarballs` read bucket; finalize is responsible for either moving
--     the object or recording its bucket so downloads work either way. We keep
--     it in the same bucket here (`tarballs`) but allow override via env. The
--     default bucket id is `tarball-uploads` per task spec.
--
-- Howard 2026-05-13.
-- =============================================================================

-- ----- tarballs.upload_status -----------------------------------------------
alter table public.tarballs
  add column if not exists upload_status text not null default 'uploaded'
    check (upload_status in ('pending_upload', 'uploaded', 'failed'));

alter table public.tarballs
  add column if not exists signed_url_expires_at timestamptz;

-- Partial index helps a future reaper sweep abandoned pending rows cheaply.
create index if not exists tarballs_pending_upload_idx
  on public.tarballs (signed_url_expires_at)
  where upload_status = 'pending_upload';

-- ----- new bucket: tarball-uploads (private) --------------------------------
-- Direct PUT lands here. Backwards compatible: rows with storage_path keyed
-- inside `tarballs` keep working. New rows store the bucket explicitly so the
-- buyer download path can fetch from either bucket without server-side guesswork.
alter table public.tarballs
  add column if not exists storage_bucket text not null default 'tarballs';

insert into storage.buckets (id, name, public)
  values ('tarball-uploads', 'tarball-uploads', false)
  on conflict (id) do nothing;

-- Owner-only read policy on the new bucket — mirrors the existing one.
drop policy if exists "tester reads own tarball-uploads blobs" on storage.objects;
create policy "tester reads own tarball-uploads blobs" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'tarball-uploads'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- ----- Recompute aggregates ignores non-uploaded rows ----------------------
-- The existing recompute_tester_aggregates() function already filters on
-- d5_verdict = 'accepted'. Pending uploads never get d5 verdict set, so they
-- are correctly excluded. We don't need to touch the trigger.

-- ----- Verification ----------------------------------------------------------
-- After this migration:
--   SELECT column_name, data_type
--     FROM information_schema.columns
--     WHERE table_schema='public' AND table_name='tarballs';
-- should include: upload_status, signed_url_expires_at, storage_bucket.
