---
spec_id: SM_backend_mvp
project: gamedata-backend
priority: P0
estimated_minutes: 120
depends_on: [SF_terminator_manifest, SG_heartbeat_watchdog]
modifies: ["backend/api/", "backend/db/migrations/", "backend/workers/"]
executor: glm
---

# SM — GameData Recorder Backend MVP

## Goal
Replace manual SSH-pull workflow with a production HTTP ingest pipeline that scales from 3 testers (pilot) to 100 sessions/day (buyer commit). Each session = 80MB tarball + telemetry. Backend must be self-healing, quarantine-aware, and resumable.

## Constraints
- **Stack**: FastAPI (Python 3.11+) on Fly.io, Cloudflare R2 for blobs, Supabase Postgres + TimescaleDB extension for telemetry.
- **No prototypes** (iron law): production-grade error handling, structured logging, graceful degradation. Spec qualifies only when deployed at a real URL with TLS + JWT working end-to-end.
- **Brand isolation**: per-brand R2 buckets, per-brand JWT issuer keys. Never co-mingle.
- **Cost ceiling**: 100 sessions/day × 80MB × 30 days = 240GB R2/month = ~$3.60/mo blob + Supabase free tier. Must stay under $20/mo at 100/day.

## 1. HTTP Endpoints

All endpoints under `/v1`. JWT bearer required except `/healthz`.

### POST /v1/sessions/upload
- **Protocol**: tus.io 1.0.0 resumable. Client first POSTs `Upload-Length`, `Upload-Metadata` (b64-encoded `session_id,filename,sha256`), receives `Location: /v1/sessions/upload/{upload_id}`. Subsequent PATCH requests stream chunks ≥10MB with `Upload-Offset`.
- **Server-side**: writes chunks to R2 multipart upload. On final PATCH, validates SHA-256, parses tarball manifest, inserts `sessions` row (status=`ingested`), enqueues quarantine check.
- **Idempotency**: `session_id` is client-generated UUID; duplicate POST returns existing `upload_id`.
- **Rejection**: 413 if `Upload-Length` > 200MB. 401 if JWT missing/invalid.

### POST /v1/sessions/heartbeat
- **Body**: `{session_id, ts, pid, mc_window_alive, obs_recording, disk_free_gb, cpu_pct}` (matches `health.json` schema).
- **Action**: insert into `heartbeats` hypertable. If 3 missed heartbeats (90s gap), mark session `status=stalled`.
- **Cardinality**: 1 row per 30s × ~12 active testers = ~35K rows/day. TimescaleDB compresses 7d+ chunks.

### POST /v1/sessions/terminator
- **Body**: matches `terminator.json` from rc11/12 — `{session_id, reason, mp4_clean_close, exit_code, last_frame_ts, errors[], duration_s}`.
- **Action**: write to `terminator_events` hypertable. Run quarantine logic synchronously (see §3). Return `{quarantined: bool, reason: str}` so client can prompt re-record.

### GET /v1/sessions/{id}/status
- **Returns**: `{session_id, status, quarantined, r2_url|null, ingested_at, terminator_reason|null, heartbeat_count}`.
- **Status enum**: `uploading | ingested | quarantined | accepted | failed`.

### GET /v1/healthz
- Returns 200 with `{db: ok, r2: ok, version: <git_sha>}`. No auth.

### POST /v1/diagnostics
**Howard 2026-05-10 explicit add: tester 一键上传整个 diagnostic zip (含
javaw + heal_events + sysinfo + recorder log) 直接到后端, 替代当前
catbox.moe 短期方案. 启用前提: tester ≥ 5 人.**

- **Protocol**: multipart POST. Single `file` field = .zip blob ≤ 200MB.
  Optional `metadata` field = JSON { tester_id, session_id (if linked),
  recorder_version, brand, comment }.
- **JWT auth**: required (per-tester scoped). Brand inherited from JWT.
- **Server-side**:
  1. Validate zip magic bytes + size limit
  2. SHA-256 hash → if duplicate (tester sent same zip twice), 200 with
     existing diagnostic_id (idempotent)
  3. Store blob at `oyster-gamedata-{brand}` R2 bucket key
     `diagnostics/{tester_id}/{yyyy}/{mm}/{diagnostic_id}.zip`
  4. Insert row in `diagnostics` table:
     ```sql
     CREATE TABLE diagnostics (
       diagnostic_id UUID PRIMARY KEY,
       tester_id UUID REFERENCES testers,
       brand TEXT NOT NULL CHECK (brand IN ('pilot','clawglasses','oyster','puffy','clawphones','dauth')),
       session_id UUID REFERENCES sessions,  -- nullable, if linkable
       recorder_version TEXT NOT NULL,
       size_bytes INT NOT NULL,
       sha256 TEXT NOT NULL UNIQUE,
       comment TEXT,
       uploaded_at TIMESTAMPTZ DEFAULT NOW(),
       triaged_at TIMESTAMPTZ,
       triaged_by TEXT
     );
     ```
  5. Auto-extract heal_events.jsonl entries → bulk-insert into
     `heal_events` hypertable (TimescaleDB) for cross-tester aggregation
     (the actual long-term value of this endpoint vs catbox.moe)
  6. Return 201 with `{diagnostic_id, url, size_bytes, link_to_dashboard}`
- **Rate limit**: 10/hour/tester (anti-abuse — diagnostic zip can be 200MB)
- **Privacy**: PII filter — refuse zips containing `*.jpg`/`*.png` outside
  `clip-*/`, refuse if path includes home dir absolute names. Tester
  warned at upload time if filter triggers.

### GET /v1/diagnostics/{id}
- Engineer fetches a diagnostic with full metadata + presigned R2 URL
  for the zip. Per-engineer auth (different JWT scope than tester).
- Used by triage dashboard.

### POST /v1/diagnostics/{id}/triage
- Engineer marks diagnostic as triaged with notes. Updates
  `triaged_at` + `triaged_by` columns. Used to track which diagnostics
  have been reviewed.

## 2. Storage

### Cloudflare R2
- **Bucket per brand**: `oyster-gamedata-{brand}` (e.g., `oyster-gamedata-clawglasses`).
- **Pilot bucket** (Howard 2026-05-09 explicit decision): `oyster-gamedata-pilot` for single-tester early-stage rollout. Brand-independent per iron law (recorder is a tool, not a product brand). Once we onboard 5+ testers per brand, migrate sessions out of pilot bucket via per-brand workers.
- **Key format**: `sessions/{tester_id}/{yyyy}/{mm}/{session_id}.tar.gz`.
- **Lifecycle**: keep 90 days hot, archive to R2 Glacier after. No deletes (testers' raw data is gold for buyer).
- **Cost**: $0.015/GB-month × 240GB = $3.60/mo at 100 sessions/day.

### Postgres (Supabase) Schema

```sql
CREATE TABLE testers (
    tester_id UUID PRIMARY KEY,
    brand TEXT NOT NULL CHECK (brand IN ('pilot','clawglasses','oyster','puffy','clawphones','dauth')),
    email TEXT UNIQUE NOT NULL,
    jwt_kid TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

CREATE TABLE sessions (
    session_id UUID PRIMARY KEY,
    tester_id UUID REFERENCES testers,
    brand TEXT NOT NULL,
    r2_key TEXT,
    sha256 TEXT,
    bytes BIGINT,
    duration_s INT,
    status TEXT NOT NULL DEFAULT 'uploading',
    quarantined BOOLEAN DEFAULT FALSE,
    quarantine_reason TEXT,
    manifest JSONB,
    started_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ,
    accepted_at TIMESTAMPTZ
);
CREATE INDEX ON sessions (tester_id, started_at DESC);
CREATE INDEX ON sessions (status) WHERE status != 'accepted';

-- TimescaleDB hypertables
CREATE TABLE heartbeats (
    ts TIMESTAMPTZ NOT NULL,
    session_id UUID NOT NULL,
    tester_id UUID NOT NULL,
    pid INT, mc_window_alive BOOL, obs_recording BOOL,
    disk_free_gb REAL, cpu_pct REAL
);
SELECT create_hypertable('heartbeats', 'ts', chunk_time_interval => INTERVAL '1 day');
SELECT add_compression_policy('heartbeats', INTERVAL '7 days');

CREATE TABLE terminator_events (
    ts TIMESTAMPTZ NOT NULL,
    session_id UUID NOT NULL,
    tester_id UUID NOT NULL,
    reason TEXT NOT NULL,
    mp4_clean_close BOOL,
    exit_code INT,
    duration_s INT,
    errors JSONB
);
SELECT create_hypertable('terminator_events', 'ts', chunk_time_interval => INTERVAL '7 days');
```

## 3. Quarantine Logic

Run on every `terminator` POST and on tarball finalize. Auto-quarantine when:

1. `terminator.reason != 'clean_exit'` (rc11 enum: `crash | obs_timeout | disk_full | mc_died | user_abort`).
2. `terminator.mp4_clean_close == false` (ffmpeg didn't write moov atom).
3. Manifest field count mismatch (e.g., inputs.jsonl line count < expected for duration).
4. `heartbeats` last-seen > 60s before `terminator.ts` (stall before exit).

When quarantined: set `sessions.quarantined=true`, `quarantine_reason=<rule>`, return reason to client. Client prompts tester via desktop toast: "Session X failed: <reason>. Please re-record."

Quarantined sessions are NOT deleted — kept in R2 for forensics. They just don't count toward buyer's daily quota.

## 4. Resume Support (tus.io)

- Implement [tus 1.0.0 core protocol](https://tus.io/protocols/resumable-upload) extensions: `creation`, `expiration`, `checksum`, `termination`.
- Minimum chunk: 10 MiB. Maximum chunk: 50 MiB.
- Server expires unfinished uploads after 48h.
- Use `tuspy`-compatible server library (e.g., `python-tus`) or roll thin wrapper around R2 multipart upload API.
- On reconnect, client HEAD `/v1/sessions/upload/{upload_id}` returns `Upload-Offset` so client resumes from byte N.

## 5. Auth + Rate Limit

- **JWT**: HS256, per-brand signing key in `JWT_SECRET_<BRAND>`. Claims: `{sub: tester_id, brand, exp, kid}`. 7-day expiry.
- **Refresh endpoint**: `POST /v1/auth/refresh` with current valid token issues new 7-day token.
- **Rate limit**: 100 req/min per tester via `slowapi` (Redis-backed). Upload chunk PATCH counts as 1 req regardless of size.
- **Revocation**: setting `testers.revoked_at` blocks all future requests within 5min (Redis cache TTL).

## 6. Telemetry + Daily Rollup

- `terminator_events` hypertable populated by §1 endpoint.
- **Daily rollup job** (Supabase pg_cron at 03:00 UTC):
  - Compute per-tester: sessions_total, clean_exits, crashes, avg_duration_s, quarantine_rate.
  - Insert into `daily_tester_stats` (regular table) for dashboard queries.
  - Alert via Slack webhook if any tester's quarantine_rate > 30% over 24h.

## Verification Criteria

- [ ] `GET /v1/healthz` returns 200 from production URL with TLS.
- [ ] Upload 80MB tarball via tus from recorder; verify R2 object + `sessions` row + sha256 match.
- [ ] Kill upload mid-stream, restart, verify resume from correct offset (no duplicate bytes in R2).
- [ ] POST malformed `terminator.json` (`reason=crash`) → session quarantined, client receives `{quarantined: true}`.
- [ ] 100 concurrent heartbeats inserted in <1s; verify hypertable partition correct.
- [ ] JWT signed with wrong brand key → 401.
- [ ] Exceed 100 req/min → 429 with `Retry-After` header.
- [ ] Daily rollup job populates `daily_tester_stats` for prior day.
- [ ] `pytest backend/tests/` all green; coverage ≥80%.
- [ ] Deploy to Fly.io, hit endpoints from external host, document URL in `backend/README.md`.

## Do NOT
- Do not implement web UI (separate spec).
- Do not delete or rewrite quarantined sessions.
- Do not mix brands in shared R2 buckets or shared JWT keys.
- Do not skip TimescaleDB — Supabase Postgres alone won't compress at scale.
- Do not bypass tus protocol with custom resumable scheme.
