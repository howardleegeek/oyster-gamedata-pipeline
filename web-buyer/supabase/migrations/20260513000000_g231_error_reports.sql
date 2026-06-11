-- =====================================================================
-- G231-G240 · W28 Error Reporting Service — error_reports table
-- =====================================================================
-- One row per crash fingerprint. Same crash from 1000 testers => 1 row
-- with count incremented (see web-buyer/app/api/error-report/route.ts).
--
-- Idempotent on re-run.
-- =====================================================================

create extension if not exists "pgcrypto";

create table if not exists public.error_reports (
    fingerprint        text primary key,
    first_seen         timestamptz not null default now(),
    last_seen          timestamptz not null default now(),
    count              integer not null default 1,
    recorder_version   text not null,
    os                 text not null,
    severity           text not null default 'crash',
    stack_trace        text not null,
    context_json       jsonb not null default '{}'::jsonb,
    sample_anon_id     text,

    -- Defensive constraints
    constraint error_reports_count_nonneg check (count >= 0),
    constraint error_reports_severity_allowed
        check (severity in ('crash', 'error', 'warn', 'info'))
);

create index if not exists error_reports_last_seen_idx
    on public.error_reports (last_seen desc);

create index if not exists error_reports_count_idx
    on public.error_reports (count desc, last_seen desc);

create index if not exists error_reports_recorder_version_idx
    on public.error_reports (recorder_version);

create index if not exists error_reports_severity_idx
    on public.error_reports (severity);

-- Hot path is `select count, first_seen where fingerprint = ?` already
-- served by the primary key; no extra index needed there.

-- Row Level Security: only the service role can read/write the table.
-- Buyers and anonymous users have no access — the summary endpoint
-- proxies via the service-role key on the server side.
alter table public.error_reports enable row level security;

drop policy if exists error_reports_service_role_all on public.error_reports;
create policy error_reports_service_role_all
    on public.error_reports
    for all
    using (auth.role() = 'service_role')
    with check (auth.role() = 'service_role');

-- Comments
comment on table public.error_reports is
    'G231-G240 W28 crash telemetry. One row per dedup fingerprint. All PII scrubbed before persist.';
comment on column public.error_reports.fingerprint is
    'sha256(scrubbed_stack || os_family || recorder_major_version) truncated to 32 hex chars.';
comment on column public.error_reports.stack_trace is
    'PII-scrubbed stack trace. Filesystem paths, usernames, machine names, IPv4 and emails redacted.';
comment on column public.error_reports.sample_anon_id is
    'Opaque per-install id from the recorder; not linked to tester identity.';
