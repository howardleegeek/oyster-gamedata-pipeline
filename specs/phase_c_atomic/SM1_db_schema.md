# SM1 — Phase C Atomic: Postgres Schema + Migrations

> Atomic sub-spec of SM_backend_mvp.md. Cluster-dispatchable in isolation.
> Don't dispatch until Howard says GO (currently spec-only per
> 2026-05-10 directive).

## Goal
Set up Supabase Postgres schema + TimescaleDB hypertables for the
backend. Output: `backend/db/migrations/0001_initial.sql` + Alembic
config.

## Files to create
- `backend/pyproject.toml` (pinned deps: alembic, asyncpg, sqlalchemy 2.x)
- `backend/db/__init__.py`
- `backend/db/models.py` (SQLAlchemy 2.0 declarative)
- `backend/db/migrations/0001_initial.sql` (idempotent CREATE TABLE)
- `backend/db/migrations/0002_timescale_hypertables.sql`
- `backend/.env.example` (DATABASE_URL placeholder)

## Tables (verbatim from SM_backend_mvp.md §2 + diagnostics endpoint add)

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
    recorder_version TEXT NOT NULL,
    duration_sec REAL,
    frame_count INT,
    sha256 TEXT NOT NULL,
    r2_key TEXT NOT NULL,
    quarantined BOOL NOT NULL DEFAULT FALSE,
    quarantine_reason TEXT,
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE diagnostics (
    diagnostic_id UUID PRIMARY KEY,
    tester_id UUID REFERENCES testers,
    brand TEXT NOT NULL,
    session_id UUID REFERENCES sessions,
    recorder_version TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    comment TEXT,
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    triaged_at TIMESTAMPTZ,
    triaged_by TEXT
);
```

## Hypertables (TimescaleDB)
```sql
CREATE TABLE heal_events (
    event_id UUID NOT NULL,
    tester_id UUID,
    session_id UUID,
    feature_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT,
    details JSONB,
    remediation JSONB,
    recorder_version TEXT,
    ts TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (ts, event_id)
);
SELECT create_hypertable('heal_events', 'ts');
CREATE INDEX heal_events_feature_ts ON heal_events (feature_id, ts DESC);
CREATE INDEX heal_events_tester_ts ON heal_events (tester_id, ts DESC);

CREATE TABLE heartbeats (
    tester_id UUID NOT NULL,
    session_id UUID,
    state TEXT NOT NULL,
    frame_count INT,
    pid INT,
    ts TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (ts, tester_id)
);
SELECT create_hypertable('heartbeats', 'ts');
```

## Verification
- [ ] `alembic upgrade head` against fresh Supabase project succeeds
- [ ] All FK constraints enforced (tester delete cascades to sessions)
- [ ] heal_events + heartbeats both registered as hypertables
- [ ] Brand check constraint allows 'pilot' (early-stage default)
- [ ] Indexes on (feature_id, ts) for fast aggregation queries

## Do NOT
- Implement application code (that's SM2-SM5)
- Deploy to Supabase (Howard explicit: spec-only)
- Touch R2 (that's SM3)
