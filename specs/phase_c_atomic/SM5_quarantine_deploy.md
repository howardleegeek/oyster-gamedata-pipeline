# SM5 — Phase C Atomic: Quarantine Workers + Daily Rollup + Fly.io Deploy

> Depends on: SM1-SM4 all deployed. Final atomic spec.

## Goal
Background quarantine workers + daily telemetry rollup + Fly.io
deployment. Output: `backend/workers/` module + `fly.toml` + GitHub
Actions deploy workflow.

## Files to create
- `backend/workers/__init__.py`
- `backend/workers/quarantine.py` (post-upload hook, reads terminator.json
  + mp4_clean_close from manifest, flags `quarantined=true` on bad sessions)
- `backend/workers/daily_rollup.py` (cron job: aggregate heal_events
  per (brand, tester, feature_id, day) into a materialized view)
- `backend/workers/depth_inference.py` (the actual depth backend
  service consuming server_pending sessions and running DepthAnything
  on real GPU — closes the rc15.7 fallback loop)
- `fly.toml` (Fly.io config: 1 web + 1 worker process)
- `Dockerfile` (Python 3.11 slim, multi-stage)
- `.github/workflows/deploy-backend.yml` (deploy on tag `backend-v*`)

## Quarantine logic (from SM_backend_mvp.md §3)
- Trigger: post-upload, after row inserted
- Read terminator.json from R2, check `reason`:
  - `clean_exit` + `mp4_clean_close=true` + `mod_handshake_ok=true` → no quarantine
  - Anything else → set `quarantined=true`, `quarantine_reason=<reason>`
- Tester sees `quarantined=true` in `/v1/sessions/{id}/status` → must re-record

## Depth backend worker
- Polls sessions where `depth_manifest.status='server_pending'`
- Downloads tarball, runs DepthAnything V2 on Modal GPU (or Lambda Labs)
- Writes depth/*.exr back into the tarball, updates depth_manifest.json
  `status=server_complete, server_inference_at=<ts>`, re-uploads to R2
- ~$0.10-0.50 per session at Modal pricing for vits

## Deploy
- Fly.io free tier: 256MB shared CPU x 2 processes, $0/mo
- Scale up to 512MB when traffic grows
- Postgres via Supabase free tier ($0)
- R2 via Cloudflare ($0.015/GB-month, ~$3-10/mo at 100 sessions/day)
- Total Phase C cost @ 100 sessions/day = ~$15/mo

## Verification
- [ ] Deploy via `fly deploy` produces public URL with TLS
- [ ] Curl `/v1/healthz` returns 200 with `{db: ok, r2: ok}`
- [ ] Tester upload via SN client end-to-end → R2 object + `sessions` row
- [ ] Quarantine fires on `mp4_clean_close=false` session
- [ ] Daily rollup cron writes `daily_heal_summary` materialized view
- [ ] Depth worker consumes server_pending → uploads with status=server_complete

## Do NOT
- Touch SN (recorder uploader client) — separate spec, separate timing
- Custom domain / branded URL (overkill, .fly.dev fine for pilot)
