# CHANGELOG

## [0.6.3] - 2026-05-20

### Other

- docs(specs): Wave 11 — release asset, appcast, runtime check, auto-tag (3fd3e09)
- docs(specs): Wave 12 preload (R2 upload, Sentry stub, VRChat, registry) (67b0247)
- docs(specs): S91v2 imperative — appcast server (S91 v1 stuck at 89 lines) (5e62b3b)


## v0.4.1 · 2026-05-19

### Added
- `--strict-buyer` flag on `bin/end_to_end_gate_smoke.py`: BLOCK on SKIP/PASS_DEGRADED for H8/S1/V1/V2/B2 gates. Required for production buyer deliverables.
- `bin/provenance_verify.py`: Ed25519-signed batch manifest verifier with `--expect-pubkey` fingerprint check.
- `verify.sh` bundle script: one-command integrity check for buyer tarballs.

### Changed
- Gate smoke test now distinguishes DEMO mode (SKIP permitted) from production mode (`--strict-buyer`).
- Provenance verification returns distinct exit codes: 0=OK, 1=verify fail, 2=pubkey mismatch.

### Fixed
- Yaw drift fix in adapter clamp.
- bot.position nesting fix in `_position_from_obs`.
- Pathfinder hang fix (move_radius 1.5, weights 25% move).

---

## v0.1.0-rc2 · 2026-05-02

### Added
- Vendor PRD package (PRD.md, VENDOR_ONBOARDING.md, SUBMISSION_FORMAT.md)
- Real capture scripts (mc_launcher_real.py, spectator_follow.py, real_depth_filler.py)
- Vendor toolchain (generate_manifest.py, upload_s3.sh, doctor.sh, sprint_dashboard.py)
- Internationalization (PRD_EN.md, FAQ.md)
- Phase 2 integration (obs_capture_real.py, depth_inference_pipeline.py, semantic_validator.py)

### Fixed
- Yaw drift, bot.position nesting, pathfinder hang, Phase 2 test imports, OBS WebSocket mocking, DepthAnything lazy import.
