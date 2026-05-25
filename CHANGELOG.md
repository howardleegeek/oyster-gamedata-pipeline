# CHANGELOG

## [0.11.9] - 2026-05-25

### Fixed

- require game window before strict recorder launch (12d9e73)

### Other

- chore(ci): format windows smoke contract test (d12e4e4)


## [0.11.8] - 2026-05-24

### Fixed

- keep detected game handle send-safe (b64de9e)
- align release contract with v0.11.7 (6b241e4)
- clean stale recorder before strict session (81bc3d4)


## [0.11.7] - 2026-05-24

### Fixed

- harden CI auto-record no-popup path (73a8e50)

### Other

- ci(recorder): maximize Minecraft in strict smoke (e29ef91)
- ci(recorder): default Windows smokes to no-gui preflight (e2eafeb)


## [0.11.6] - 2026-05-24

### Other

- ci(recorder): verify Minecraft foreground in real session smoke (d17830e)
- ci(recorder): enable CI capture mode for real session smoke (e8cf5c8)
- ci(recorder): trigger F9 video chain in strict smoke (2719f58)


## [0.11.5] - 2026-05-24

### Other

- ci(recorder): harden strict session smoke entry (cc78ec3)
- ci(recorder): use named splatting for strict smoke (5ca076c)
- test(recorder): focus Minecraft during strict smoke (f1ae232)


## [0.11.4] - 2026-05-24

### Other

- test(recorder): require strict real-session evidence (d9124fd)
- ci(recorder): add strict Windows real-session workflow (9c4b6e8)
- test(recorder): force upload config in strict session smoke (0fb396c)


## [0.11.2] - 2026-05-24

### Other

- ci(installer): require OBS runtime in Windows package (775c23d)


## [0.11.1] - 2026-05-23

### Other

- docs: align version docs with v0.11.0 (7c79a4d)
- docs(release): codify channel fallback contract (2ebb1c4)


## [0.11.0] - 2026-05-22

### Added

- codify recorder pipeline contract (72168ff)

### Other

- ci(backend): smoke current production backend (d5e246f)
- chore(recorder): align submodule to current main (30b1d34)


## [0.10.0] - 2026-05-22

### Added

- add plug-and-play plugin profiles (13ad27a)
- add smoke-ready single-player adapters (72e8e88)

### Other

- ci(backend): tolerate appcast sync race (44c4feb)


## [0.9.4] - 2026-05-22

### Fixed

- accept recorder LEM sessions (f84a7ce)

### Other

- ci(release): sync backend appcast after auto release (a4b603f)


## [0.9.3] - 2026-05-22

### Fixed

- harden Windows real session cleanup (027a0db)

### Other

- ci(mc-mod): make fabric watcher permission aware (830d1e7)
- test(recorder): add Windows real session smoke harness (0889d2e)


## [0.9.2] - 2026-05-22

### Other

- ci(backend): add optional admin state smoke (f54c366)


## [0.9.1] - 2026-05-22

### Other

- ci(backend): script GCP appcast release sync (299cada)


## [0.9.0] - 2026-05-22

### Added

- add admin state summary (947ce58)


## [0.8.15] - 2026-05-22

### Fixed

- require explicit tester admin token (0ef7801)


## [0.8.14] - 2026-05-22

### Fixed

- persist stub state across restarts (f199d3b)


## [0.8.13] - 2026-05-22

### Other

- ci(backend): sync appcast metadata on deploy (a66182c)


## [0.8.12] - 2026-05-22

### Fixed

- align appcast with latest recorder release (4942868)

### Other

- ci(release): harden auto release race handling (cbdb980)


## [0.8.11] - 2026-05-22

### Other

- ci(installer): report Authenticode status in smoke (ee7c033)
- test(backend): harden deployed recorder smoke (991d692)
- test(backend): avoid unresolved metadata literals (6043539)


## [0.8.10] - 2026-05-21

### Other

- ci(installer): harden Windows signing gates (540853e)
- ci(installer): unify EV signing secret (2b3ada4)


## [0.8.9] - 2026-05-21

### Other

- ci(backend): require deployed recorder e2e smoke (84b4f97)


## [0.8.8] - 2026-05-21

### Fixed

- update income on session upload (cf0a605)

### Other

- style(test): format backend deploy contract (dfc25ba)


## [0.8.7] - 2026-05-21

### Other

- ci(backend): add Fly deploy workflow (c344395)
- ci(release): verify recorder tray launch (3075d7d)
- ci(backend): codify deploy smoke release blocker (eee00b8)


## [0.8.6] - 2026-05-21

### Other

- ci(release): smoke latest installer distribution (9260f24)
- ci(backend): add remote smoke guard (2267b11)
- ci(release): smoke Windows installer install path (54d6eda)


## [0.8.5] - 2026-05-21

### Fixed

- retry installer asset carry-forward (788d68e)
- publish installer assets atomically (2e87915)
- document installer in release notes (b710188)


## [0.8.4] - 2026-05-21

### Fixed

- carry installer assets onto auto releases (0e53778)


## [0.8.3] - 2026-05-21

### Fixed

- tolerate missing process metrics in load test (16c6377)
- isolate gh cli absence check (6f5d76d)

### Other

- docs: PARTNER_BRIEF_v0.8.2.md — Bruno review, first real .exe + 36h progress (b5ecb4d)


## [0.8.2] - 2026-05-21

### Fixed

- unblock workflow environment checks (73ac2f3)
- support python 3.10 toml parsing (f5576bf)
- honor explicit iron law diff base (7d2946b)


## [0.8.1] - 2026-05-21

### Fixed

- replace fake mareangler/iscc-action with real choco install Inno Setup (#90) (9573f4d)
- skip missing enrichment submodule (#91) (59321b2)
- update recorder lock and version parsing (#92) (c4af85c)
- shorten recorder cargo target path (#93) (40627b9)
- scope runner temp env to recorder job (#94) (cfe42fa)
- use fixed short cargo target path (#95) (caf24d5)
- pin recorder submodule to v2.6.0 release (7718de6)
- remove --locked from cargo build (Layer 6 unblock) (#96) (71b31fc)
- Layer 7 — workflow looks for gamedata-recorder.exe not oyster-recorder.exe (#97) (2864df7)
- Layer 8 pre-emptive — .iss references gamedata-recorder.exe (#98) (3f14638)
- normalize recorder binary for installer (062c18e)
- use single-line recorder artifact paths (add0631)
- Layer 8 — correct upload path to installer/installer/output/ (where ISCC actually writes) (#99) (cce7f8c)
- write setup artifact to expected output dir (4587890)
- upload recorder artifact from final output dir (a750adb)
- stabilize recorder tests and release automation (62e8d79)

### Other

- docs(specs): S60v2 imperative — fix fake mareangler/iscc-action with real choco install (6c15927)
- docs(specs): S60v3 skip missing enrichment submodule (d6b81ac)
- docs(specs): S121 — fix 6+ Rust compile errors (TrayIcon::menu, missing rand/urlencoding/hex/fastrand, Notification::timeout_ms API) (3cc7e49)
- docs(specs): S60v7 — remove --locked flag (Layer 6 CI fix) (7799e77)


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
