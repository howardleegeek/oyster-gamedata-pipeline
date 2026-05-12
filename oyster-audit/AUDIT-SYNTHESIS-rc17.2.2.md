# Audit Synthesis — rc17.2.2

**Date**: 2026-05-12
**Cluster status**: Aliyun token 429-blocked; 4 agents (RED/BLUE/GRILL/SUPER) dispatched but only reached exploration phase (21-70 turns each), did not produce final reports. This synthesis is **combined from agent-trace observations + direct session knowledge** of all rc17.x work tonight.

---

## 🔴 RED — Attack surface

### R1 (P1): Path injection in `gameinfo_writer.rs` + `depth_exr_writer.rs`
Both Rust writers shell out to Python with `session_dir` as a command argument. Currently `session_dir` is provided by the recorder (under `%LOCALAPPDATA%\GameData Recorder\recordings\`), so trusted. **But**: BH-narrow's sibling-dir fallback can pick a `game_state.jsonl` from ANY sibling dir under recordings/. If an attacker drops a malicious dir with a shell-metachar name, and the recorder picks it, the Python shell-out may break/execute.
- **Mitigation**: validate session_dir matches `^session_\d{8}_\d{6}_[0-9a-f]{8}$` before shell-out.

### R2 (P1): Filesystem race in BH-narrow sibling-dir fallback
The fallback scans `recordings/` for the most-recently-modified `game_state.jsonl` within ±10 min window. Two concurrent MC instances + 2 OysterPlay launchers (the dup-launch bug Howard hit earlier) = ambiguous which `game_state.jsonl` is which session's. **Wrong sample of camera_position gets attached to wrong mp4.**
- **Mitigation**: write `OYSTER_SESSION_DIR` env value into a marker file in the dir; cross-check identity before attaching data.

### R3 (P2): TOCTOU on lint v3
BN runs lint v3 AFTER metadata flush via subprocess. Window between metadata write and lint read allows tampering of session files. Production deployment with malicious local writer (not realistic in consumer use) could fool the validate-then-upload gate.
- **Mitigation**: hash session_dir contents during write, re-verify hash after lint.

### R4 (P2): Resource exhaustion via depth EXR tokio spawn
`Recording::stop()` calls `tokio::spawn` for the depth EXR job per session, fire-and-forget. 10 back-to-back sessions = 10 concurrent DepthAnything inference jobs hammering CPU/DML.
- **Mitigation**: bounded global semaphore (max 1 concurrent depth job).

### R5 (P3): mc-mod data trust
`mc-mod/.../GameStateCapture.java` writes player position values straight from MC's API. A modded MC client could inject fake coordinates (e.g., teleport hacks) and recorder would faithfully record them. For ML training, that's *poisoned data*.
- **Mitigation** (long): sign mc-mod jar + verify hash at runtime. For now, accept (consumer trust model).

**RED verdict**: SHIP-WITH-CAVEATS. R1/R2 should be addressed in rc17.3; R3-R5 documented as known.

---

## 🔵 BLUE — Coverage + defense gaps

### B1 (P0): No telemetry to Oyster servers
Zero metric/log/error reporting flows back. If 100 users install rc17.2.2 and 30% fail, we won't know which 30 or why. Sessions just don't upload.
- **Fix**: rc17.3 add OTLP collector + `tracing-opentelemetry` to ship `Recording::stop()` outcomes + lint v3 verdicts + crash reports.

### B2 (P0): `Recording::stop()` not idempotent
If called twice (e.g., signal handler + crash handler race), metadata gets re-written, lint v3 runs twice, depth EXR spawned twice. Each second call may corrupt outputs.
- **Fix**: AtomicBool "already-stopped" guard at function entry.

### B3 (P1): Lint v3 has 32 criteria but PRD has more
PRD spec specifies several requirements not encoded as criteria (e.g., session duration upper bound, audio absence, depth EXR per-frame existence count match frames.jsonl). Some gaps:
- No criterion checks `depth/*.exr` count equals `frames.jsonl` entry count
- No criterion checks `gameinfo.xlsx` schema matches PRD sheets
- **Fix**: rc17.3 add criteria #33-#37 to lint v3.

### B4 (P1): Silent failures in `gameinfo_writer` + `depth_exr_writer`
Both writers `tracing::warn!` on error and proceed. There's NO mechanism to surface failures to the user — the toast only fires on lint v3 FAIL, not writer FAIL. If Python script is missing, user silently has no xlsx and no idea.
- **Fix**: writer errors should set a flag that lint v3 reads + reports.

### B5 (P2): Test coverage gap on Windows-only paths
`cargo test` runs only on `aarch64-apple-darwin` cross-platform shims. Real Windows path (e.g., `validation.rs` Windows toast PowerShell shell-out, `gameinfo_writer.rs` Python invocation) tested only via integration on user's actual machine.
- **Fix**: CI step `cargo check --target x86_64-pc-windows-msvc` (no MSVC needed, just type-check) catches Windows compile errors before tag.

### B6 (P2): Recovery path on mid-session crash
If recorder PID dies during recording, session_dir is left with `recording.mp4` + partial `inputs.jsonl` + missing finalize files. **Stream BD's session at 22:35 today is exactly this case** (2 files only). No code path tries to finalize on crash recovery.
- **Fix**: rc17.3 add `try_finalize_orphaned_session_on_startup()` to OysterPlay launcher.

**BLUE verdict**: NOT PRODUCTION-READY at scale. B1+B2 hard blockers for >10 users. rc17.3 must.

---

## 🧪 GRILL — Decisions interrogated

### G1: ±10 min mtime window in BH-narrow sibling-dir fallback
**Why 10?** Stream BH-narrow agent reported "10 min is the recency window" — no measurement cited. Conservative guess for typical session duration (5 min) + clock-skew tolerance (5 min).
- **Risk**: 10 min is **arbitrary**. If user records 6-min session + immediately starts another, sibling-dir scan might pick the WRONG game_state.jsonl from the just-ended session (which is 0 min old, beats new one).
- **RETRACT** — should be: bind game_state.jsonl identity to a session marker, not mtime alone.

### G2: BN lint v3 60s subprocess timeout
**Why 60s?** Spec said "lint typically takes 1-30s based on frames.jsonl size". 60s = 2x buffer. No empirical P99 measurement.
- **Risk**: depth-aware lint v3 (future criterion #25-26) reads EXR files. ~300 EXR @ 1080p might push lint to 120s+. Single false timeout = false FAIL toast.
- **CONDITIONAL** — survives now; revisit when criteria #25-26 land.

### G3: BJ depth EXR cadence 1 Hz
**Why 1 Hz?** Spec said "30 Hz × CPU too slow". Estimate not measurement.
- **Risk**: customer's downstream ML model may want 5-10 Hz. 1 Hz might be too sparse for action prediction.
- **RETRACT** — needs benchmark on minipc1's actual DML throughput @ 1080p with DepthAnything V2 small.

### G4: session_id 8-hex suffix
**Why 8 hex?** Stream-historic; no spec.
- **Risk**: 32 bits = collision at ~65k sessions (birthday paradox). PRD volume target = 100 users × 10 sessions/day = 365k/year. **Within 2 months we collide.**
- **RETRACT** — bump to 16 hex (64-bit, never collides realistically).

### G5: `OYSTER_CAPTURE_MODE=game` whitelist (9 OpenGL processes)
**Why these 9?** Stream BB hardcoded: javaw, java, factorio, kerbal, ksp, ksp_x64, minetest, 0ad, supertuxkart.
- **Risk**: Vulkan / DirectX games (most modern AAA) excluded → DXGI fallback → AMD 780M 1Hz throttle bug returns. Doom Eternal, Cyberpunk, etc. NOT in list.
- **RETRACT** — needs per-game probe + decision matrix.

**GRILL verdict**: 3/5 retract, 1 conditional, 1 survive. **Several "magic numbers" are unbacked**. rc17.3 should fix G1+G4 minimum.

---

## ⚡ SUPER — Engineering practice

### S1: TDD compliance
- **BD**: tests written ALONGSIDE implementation (action-camera-tests `+11/-3` lines). ✅ Grade B.
- **BH-narrow**: 1 integration test for sibling-dir fallback added in same commit. ✅ Grade B.
- **BN-postvalidate**: 7 unit tests + 1 integration test (`validation-tests` crate). ✅ Grade A.
- **BJ**: NO tests for `gameinfo_writer.rs` or `depth_exr_writer.rs`. ❌ Grade F.
- **BL/BM (installer .iss)**: no tests possible for Inno Setup; manual verify only. Grade D (acceptable).

### S2: Systematic debugging
- **BH-narrow**: gold standard — agent identified root cause (session_dir mismatch) before fixing. 5-whys evident in report.
- **BD**: minimal — assumed schema fields would be filled, didn't probe why null.
- **BN**: no debug needed (new feature).
- **BJ**: shelled out to Python without measuring DepthAnything V2 perf first.
- Grade: **B-** overall, dragged down by BJ.

### S3: Brainstorming alternatives
- **BH-narrow**: picked sibling-dir fallback. 3 alts that I see: (a) write OYSTER_SESSION_DIR into a header in game_state.jsonl, (b) IPC handshake at session start, (c) atomic rename of dir after both sides agree. Not documented in PR body.
- Grade: **C** — no documented alts.

### S4: Verification before completion
- **BN**: real-runs validation-tests with Python stub. Best. **A.**
- **BH-narrow**: integration test with synthetic dir topology. **B.**
- **BD**: ran tests but didn't end-to-end recorder. **C.**
- **BJ**: cargo check only, no end-to-end. **D.**

### S5: Code review
- **All streams**: agent self-merged via path-scoped commit. **No second pair of eyes** before merging into rc17.2 batch.
- Grade: **F** — unreviewed.

### S6: First principles
- BH-narrow report explicitly did 5-whys back to "Python predicts dir, Rust creates dir, no contract". **A.**
- Others: less rigorous.

**SUPER verdict**: ENGINEERING PRACTICE = **C+**. Acceptable for v0.28 RC; **must improve** before v0.29 / public release. rc17.3 mandate code review (even by Claude reviewing Aliyun agent's code).

---

## 🎯 TOP 5 ACTIONABLE for rc17.3

1. **B1 telemetry** — add OTLP collector to ship `lint_result.json` + crash dumps + session counts to Oyster ingest endpoint. **Hard block for scaling beyond 10 users.**
2. **B2 idempotent `Recording::stop()`** — AtomicBool guard. Trivial fix, big safety win.
3. **R1+R2 path/race hardening** — validate session_dir format + use marker file (not mtime) for sibling-dir identity.
4. **G4 session_id collision** — bump to 16 hex.
5. **B3 lint v3 criteria #33-37** — add depth/EXR/xlsx count + schema criteria.

## ⚠️ KNOWN LIMITATIONS of this synthesis

- 4 cluster agents (RED/BLUE/GRILL/SUPER) hit Aliyun 429 before writing reports — content above is **partially extracted from their exploration traces, partially direct knowledge of code from this session's work**.
- Findings would be **stronger if agents had completed**. Re-dispatch when Aliyun cooldown over OR new token provisioned.
- Specific code-line citations are skipped where agent didn't reach the relevant file.

## Next steps

- Howard reviews top-5
- Decide rc17.3 scope (recommend B1+B2+R1+G4 + B3, defer rest to rc17.4)
- Aliyun cooldown over → re-run audit agents for fresh-eyes second pass
