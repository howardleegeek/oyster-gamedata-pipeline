# Migration: catbox.moe → Backend /v1/diagnostics

> When Phase C SM5 is deployed at a real URL, recorder client must swap
> upload endpoint with zero downtime / zero confusion for the pilot
> tester base. This doc captures the transition path.

## Pre-deploy state (rc15.14, current)

`bin/recorder_consumer_lite.py:_upload_diagnostic_zip()` tries:
1. catbox.moe (permanent, 200MB, anonymous, no auth)
2. file.io (14 days, anonymous, no auth)
3. Returns None on all-fail

`_export_diagnostic_only()` calls it on tester button click,
copies URL to clipboard, displays in helpbar.

Tester sends URL to engineer via WeChat. Engineer manually downloads
zip from URL, debugs.

## Post-deploy state (Phase C SM5 live)

New tier inserted at top of `_upload_diagnostic_zip()` chain:

```python
# Tier 0 (NEW): backend /v1/diagnostics with JWT (when configured)
if (jwt := _read_tester_jwt()) and (backend_url := _config_value("BACKEND_URL")):
    try:
        # POST diagnostic zip to backend, JWT in Authorization header.
        # Backend stores in R2 + heal_events extracted to TimescaleDB.
        result = _upload_to_backend(backend_url, jwt, zip_path)
        return result["dashboard_url"]  # backend returns URL pointing
                                         # to the engineer triage view
    except Exception as exc:
        _trace(f"upload_diag: backend tier failed [{type(exc).__name__}]: {exc}")
# Fallback to existing catbox + file.io chain
```

JWT distribution:
- Each tester gets a JWT issued via `backend/cli/issue_jwt.py`
- JWT delivered to tester out-of-band (email / WeChat) on onboarding
- Stored at `%LOCALAPPDATA%/OysterRecorder/auth/tester_jwt.txt`
- Recorder reads on startup; missing JWT → falls through to catbox.moe

## Migration timeline

1. **Phase C SM1-5 deployed** + smoke-tested at staging URL
2. **Issue 5 pilot JWTs** via SM4 CLI to first 5 testers
3. **Ship rc16.0 with Tier 0 backend upload** + bundled config that
   includes `BACKEND_URL`
4. **Telemetry watch**: 7 days of dual-path (Tier 0 + Tier 1 fallback)
   to confirm Tier 0 success rate ≥ 95%
5. **Sunset catbox.moe** in rc17.0 (only after data shows backend stable)
6. **Engineer dashboard live**: triage diagnostics via `/v1/diagnostics/{id}`

## Privacy + compliance touchpoints

- Backend stores zip permanently → tester opt-in / TOS acceptance
- Add `--no-upload-diagnostic` env var for tester opt-out
- Auto-redact: filename of any `*.png`/`*.jpg` outside `clip-*/`
  removed from zip pre-upload (defense against accidental screenshot
  inclusion that may contain non-game content)

## Cost gate

Switch to backend ONLY when:
- ≥ 5 active testers (1 tester catbox.moe is sufficient)
- ≥ 50 diagnostic uploads/week (telemetry signal worth aggregating)
- Howard explicit GO

Until then: catbox.moe is the right tool. Don't over-engineer.
