# MASTER ROADMAP — 2026 Q2
## Vertical Depth (Minecraft 99%+) → Horizontal Breadth (单机游戏) → Scale (千游戏)

**Owner:** Howard Li · **Last Updated:** 2026-05-06 · **Status:** AUTONOMOUS EXECUTION
**Strategic Premise:** 数据准确度 > 数据数量. 脏数据污染买家 world-model training pipeline = 业务终结.
**Cluster Discipline:** mac-1 仅做 commit/push + decision. 所有 implementation → mac-2 / opencode / claude-glm / GHA.

---

## ISC TRACKER · Strategic Layer (Roadmap-level criteria)

```
┌─ 🎯 ISC: Strategic Roadmap Criteria ──────────────────┐
│ Phase: ROADMAP DEFINITION (2026-05-06)                │
│ ✅ Strategic Criteria: 0 → 7  (+7)                    │
│ ⛔ Anti-criteria:      0 → 5  (+5)                    │
└───────────────────────────────────────────────────────┘
```

**[SC1]** 99%+ BFT detection rate on Minecraft achieved before any horizontal expansion.
**[SC2]** ≥ 5 honest tester sessions PASS BFT cleanly (zero false positives) before scale.
**[SC3]** Per-game generalization audit complete before adding game #2.
**[SC4]** All implementation work occurs on non-mac-1 cluster nodes (mac-2 / opencode / claude-glm / GHA).
**[SC5]** Every spec dispatch has measurable exit criterion (PR merged + BFT regression GREEN).
**[SC6]** Adversarial RED-team scorecard reaches ≥ 19/20 caught (95%) before Phase 2 entry.
**[SC7]** v0.23.0+ recorder distributed to ≥ 3 testers with ≥ 5 honest sessions recorded.

**Anti-criteria (must remain AVOIDED):**
**[SA1]** No mac-1 subagent dispatches for code modification (only commit/push allowed).
**[SA2]** No horizontal game expansion before Phase 1 exit criteria are 100% met.
**[SA3]** No "demo" / prototype releases — every shipped artifact is production-grade.
**[SA4]** No buyer dataset shipped without V₄ buyer-signature attached.
**[SA5]** No threshold tuning without ≥ 10 honest reference sessions used as calibration baseline.

---

## Baseline Snapshot (2026-05-06 — DO NOT REGENERATE)

| Metric | Value | Source |
|--------|-------|--------|
| Recorder version | `recorder-v0.24.0-wave3-73pct` | git tag |
| Smoke-test detection | 87% (W4 partial wires) | adversarial harness |
| Adversarial scorecard | 13/15 caught | RED_TEAM_TAXONOMY |
| BFT stack | N=4 (V₁ Claude + V₂ MiniMax + V₂' GLM + V₃ Physics + V₄ buyer-signed) | ARCH_BFT_CONSENSUS |
| Residuals shipped | 24 (R01..R12 + R13/R15/R16/R18/R20a-e/R21/R22/R23) | git log |
| Specs design-only | R25 cross-frame, R26 video-hash, V₄ ops, R20 drift partial | docs/SPEC_R*.md |
| Critical attack gaps | B-01 (closed when V₄ wired end-to-end), B-03 (closed by V₄) | RED_TEAM_TAXONOMY |
| Producer pipeline | sample_tarball_builder + recorder_consumer_lite (v0.23.0-lite) | src/ |

**Spec implementation status:**
- ✅ Implementation-ready (impl exists in src/): PRD_FORMULAS, ARCH_BFT_CONSENSUS, SPEC_R13_MULTIMODAL, SPEC_R20_STATISTICAL_DRIFT (R20a-e)
- ⚠️ Design-only (spec written, code not written): SPEC_R25_CROSS_FRAME_ANOMALY, SPEC_R26_VIDEO_CONTENT_HASH, SPEC_V4_BUYER_SIGNED_PROTOCOL (operational layer)
- 📋 Reference: PRD_DIGEST, ARCH_PINNS_BUYER_SPEC, RED_TEAM_TAXONOMY (no code, used for cross-checks)

---

# Phase 1 — VERTICAL DEPTH (Minecraft 99%+ accuracy)

**Time budget:** 4–6 weeks (start 2026-05-06).
**Cluster:** mac-2 primary (Engineer), opencode for V₂''/R26, claude-glm for V₂' expansion, GHA for gates.
**Mac-1 role:** orchestration + commit/push only. NO code edits.

## 1.1 Detection rate 87% → 95% (closing the 13% gap)

| Work | Cluster | Status | Closes |
|------|---------|--------|--------|
| **R25a yaw-mouse correlation** (impl SPEC_R25 §3.2) | mac-2 Engineer | spec done, impl pending | B-01 multi-LLM bypass (removes V₄ dep for that gap) |
| **R26 video-content-hash** (impl SPEC_R26 §4) | opencode (Kimi) | spec done, impl pending | B-01 alternate route (recorded-video replay attack) |
| **V₃ physics expansion: R10 speed_max + R23 codec lookup** | mac-2 Engineer | partial (R10 in src), R23 in spec | physics-impossible trajectories |
| **V₂'' Codex/o4-mini as third independent LLM (BFT N=5)** | opencode dispatch | not started | LLM-collusion attacks (CO-01, CO-02 in RED_TEAM_TAXONOMY) |
| **R20 drift threshold tightening** (post 10-honest-ref) | claude-glm | R20a-e shipped, threshold tuning pending | distributional-drift evasion |

**Mathematical justification (PRD_FORMULAS §7):** Adding R25a + R26 + V₂'' raises BFT consensus from p=0.87 to p ≥ 0.95 because:
- Gap composition: B-01 ≈ 6%, B-03 ≈ 4%, drift edge ≈ 3% = 13% uncaught
- R25a closes ~5%, R26 closes ~3%, V₂'' closes ~3%, threshold tuning ~2% = 13% recovered
- Confidence: ≥ 95% binomial (n=200 adversarial samples, observed catch ≥ 190)

## 1.2 Real-data validation pipeline

| Work | Cluster | Output |
|------|---------|--------|
| Tester recruitment (5+ Minecraft players) | mac-1 (Howard manual) | tester_pool.yaml |
| Real session ingest pipeline | mac-2 Engineer | scripts/ingest_real_session.py |
| Per-residual fire-rate audit on real data | claude-glm (analysis) | docs/REAL_DATA_AUDIT.md |
| Threshold re-calibration based on real noise | opencode | updated `_auto_bft.yaml` |
| Reference dataset library (20 honest sessions) | mac-2 Engineer | `samples/reference_honest/*.tar.gz` |

**Exit gate:** zero false-positive on 20 honest sessions, AND ≥ 95% catch on adversarial harness.

## 1.3 V₄ buyer-sign operationalization (closes B-01, B-03 permanently)

| Work | Cluster | Output |
|------|---------|--------|
| Buyer signing web tool (Ed25519) | mac-2 Engineer | `web/buyer-signer/` |
| HMAC-SHA256 → Ed25519 migration | opencode | `src/.../v4_signature.py` |
| 24hr SLA tracker + alerting | claude-glm | `daemons/v4_sla_monitor.py` |
| 48hr no-sign → HUMAN_REVIEW state machine | mac-2 Engineer | state column in tasks.db |
| V₄ end-to-end BFT integration test | GHA | `.github/workflows/v4-e2e.yml` |

**Reference:** SPEC_V4_BUYER_SIGNED_PROTOCOL §5 (state machine), ARCH_PINNS_BUYER_SPEC §11 (key schedule).

## 1.4 Recorder ship-ready (v0.23.0 → v0.25.0 production)

| Work | Cluster | Output |
|------|---------|--------|
| v0.23.0 .exe with full _auto_bft (15 residuals fire E2E) | GHA Windows runner | release artifact |
| Auto-update v0.20.x → v0.23.0 path verified | GHA | smoke-test on Win10/11 |
| Tester onboarding doc (Chinese) | claude-glm | `docs/TESTER_ONBOARDING_CN.md` |
| Privacy/data-collection disclosure (EULA) | mac-2 Engineer | `docs/EULA_v0.23.md` |
| Recorder boot-reliability hardening (auto-restart + crash-dump) | mac-2 Engineer | `src/.../recorder_supervisor.py` |

## Phase 1 ISC Criteria (binary YES/NO)

```
┌─ 🎯 ISC: Phase 1 Exit Criteria ───────────────────────┐
│ ✅ Criteria: 0 → 7                                    │
│ ⛔ Anti-criteria: 0 → 3                               │
└───────────────────────────────────────────────────────┘
```

**[P1-C1]** Adversarial RED-team scorecard ≥ 19/20 (≥ 95%) caught at HEAD.
**[P1-C2]** Zero false positives across 20 honest reference sessions.
**[P1-C3]** ≥ 5 real Minecraft tester sessions PASS BFT (no manual override).
**[P1-C4]** v0.23.0+ .exe distributed to ≥ 3 testers (verified by ingest endpoint logs).
**[P1-C5]** V₄ buyer-signature wired end-to-end (signed dataset → BFT GREEN → buyer accept).
**[P1-C6]** R25a + R26 + V₂'' all merged to main (PR closed + GHA gate GREEN).
**[P1-C7]** GHA daily regression GREEN for ≥ 7 consecutive days.

**Anti-criteria:**
**[P1-A1]** Zero mac-1 code commits (only commit/push of mac-2-generated diffs).
**[P1-A2]** Zero recorder releases shipped without `make smoke && make adv-test` GREEN.
**[P1-A3]** Zero buyer datasets shipped pending V₄ signature.

---

# Phase 2 — HORIZONTAL BREADTH (≥ 3 single-player games at 90%+)

**Entry gate:** Phase 1 exit criteria 100% met. NO partial entry.
**Time budget:** 6–8 weeks post Phase 1.
**Cluster:** mac-2 for per-game integrations, opencode for per-game R26 hash tables, claude-glm for generalization.

## 2.1 Generalization audit (one-time, blocks all per-game work)

| Work | Cluster | Output |
|------|---------|--------|
| Identify Minecraft-specific assumptions in 24 residuals | claude-glm | `docs/MC_SPECIFIC_ASSUMPTIONS.md` |
| Make VK-code table game-agnostic (config-driven) | mac-2 Engineer | `config/game_input_schemes/<game>.yaml` |
| Per-game `expected_hash_table` schema for R26 | opencode | `src/.../r26_per_game_table.py` |
| Per-game `speed_max` config for R10 | mac-2 Engineer | `config/game_physics/<game>.yaml` |
| Per-game spec template (auto-generation prompt) | claude-glm | `docs/PER_GAME_SPEC_TEMPLATE.md` |

## 2.2 Game priority list (ranked by ease × buyer demand)

| Rank | Game | Difficulty | Buyer Demand | Notes |
|------|------|-----------|--------------|-------|
| 1 | **Stardew Valley** | EASY (2D, simple input) | MEDIUM | First port — proves generalization |
| 2 | **Hollow Knight** | MODERATE (2D platformer) | MEDIUM-HIGH | Combat physics interesting for world-models |
| 3 | **Cyberpunk 2077** | HARD (3D AAA) | HIGH | High-value buyer use cases |
| 4 | **Elden Ring** | HARD (3D ARPG) | HIGH | Combat + open world |
| 5 | **Civilization VI** | MODERATE (turn-based, different input pattern) | LOW-MEDIUM | Strategic-AI training set |

## 2.3 Per-game integration sprint template (10 days)

| Day | Activity | Cluster | Output |
|-----|----------|---------|--------|
| 1–2 | Scenes / camera / input survey | claude-glm (analysis) | `docs/<game>_SURVEY.md` |
| 3–5 | Per-game R26 hash table generation | opencode (Kimi vision) | `config/<game>/r26_table.json` |
| 6–7 | 5 honest sessions BFT validation | mac-2 Engineer + tester | session pass/fail report |
| 8–9 | Threshold tuning per noise profile | claude-glm | updated `_auto_bft.yaml` |
| 10 | Tester onboarding (per-game CN doc) | claude-glm | `docs/TESTER_<game>_CN.md` |

## 2.4 Multi-game tester recruitment

| Work | Cluster | Output |
|------|---------|--------|
| Discord / Reddit / Steam community outreach | mac-1 (Howard manual) | per-game tester pool |
| Per-game training video | mac-1 (record) → opencode (edit) | YouTube unlisted |
| Cross-game tester reputation system | mac-2 Engineer | `daemons/tester_reputation.py` |

## Phase 2 ISC Criteria

```
┌─ 🎯 ISC: Phase 2 Exit Criteria ───────────────────────┐
│ ✅ Criteria: 0 → 6                                    │
│ ⛔ Anti-criteria: 0 → 2                               │
└───────────────────────────────────────────────────────┘
```

**[P2-C1]** ≥ 3 games at ≥ 90% BFT detection rate on per-game adversarial harness.
**[P2-C2]** ≥ 50 honest tester sessions across the 3 games (no per-game < 10).
**[P2-C3]** Per-game data-delivery pipeline documented + automated (S3 push + buyer webhook).
**[P2-C4]** Generalization audit complete (no Minecraft-specific code in core BFT path).
**[P2-C5]** Per-game R26 hash-table generation reproducible by single command.
**[P2-C6]** GHA matrix regression GREEN for all 3 games for ≥ 7 consecutive days.

**Anti-criteria:**
**[P2-A1]** Zero per-game ports started while Phase 1 [P1-Cn] criteria are not all GREEN.
**[P2-A2]** Zero buyer dataset shipped per-game without per-game V₄ signing flow validated.

---

# Phase 3 — SCALE (千游戏)

**Entry gate:** Phase 2 exit criteria 100% met.
**Time budget:** ongoing post Phase 2.

## 3.1 LLM-driven per-game configuration

| Work | Cluster | Output |
|------|---------|--------|
| Auto-generate per-game `expected_hash_table` from gameplay video | opencode (Kimi vision) | `tools/auto_r26_table.py` |
| Auto-detect input scheme (WASD vs arrows vs gamepad) | claude-glm | `tools/auto_input_scheme.py` |
| Per-game physics extraction from public game-engine specs | opencode + web-fetch | `tools/auto_physics_extract.py` |

## 3.2 Tester recruitment automation

| Work | Cluster | Output |
|------|---------|--------|
| Auto-onboarding flow (signup → tutorial → first session) | mac-2 Engineer | `web/tester-onboarding/` |
| Crypto + fiat payments (Stripe + USDC) | mac-2 Engineer | `web/payments/` |
| Reputation system v2 (cross-game scoring) | claude-glm | `daemons/tester_reputation_v2.py` |

## 3.3 Buyer-side data delivery

| Work | Cluster | Output |
|------|---------|--------|
| S3 / private bucket per buyer | mac-2 Engineer | `daemons/buyer_delivery.py` |
| Webhook on dataset PASS | mac-2 Engineer | webhook spec |
| Live BFT scorecard dashboard | claude-glm | `web/buyer-dashboard/` |

## Phase 3 ISC Criteria

```
┌─ 🎯 ISC: Phase 3 Exit Criteria ───────────────────────┐
│ ✅ Criteria: 0 → 5                                    │
│ ⛔ Anti-criteria: 0 → 2                               │
└───────────────────────────────────────────────────────┘
```

**[P3-C1]** ≥ 100 games supported via auto-config (no manual per-game code edits).
**[P3-C2]** ≥ 1,000 honest tester sessions across catalog (no per-game < 5).
**[P3-C3]** ≥ 3 paying buyers with active webhook + dashboard access.
**[P3-C4]** Per-game R26 table auto-generation < 1hr per game on opencode.
**[P3-C5]** Tester payment automation processes ≥ $10K/month with zero manual ops.

**Anti-criteria:**
**[P3-A1]** Zero buyer dataset shipped with BFT detection rate < 90%.
**[P3-A2]** Zero buyer data-delivery without V₄ signature in delivery payload.

---

# Cluster Resource Allocation Matrix

| Resource | Phase 1 (now) | Phase 2 | Phase 3 | Capacity Notes |
|----------|---------------|---------|---------|----------------|
| **mac-1** (Howard's Mac) | commit/push only, /dispatch orchestration, decision | same | same | NO code modification ever |
| **mac-2 cluster** (SSH `howard-mac2`) | **PRIMARY Engineer** for residual impl (R25a, R10/R23, V₄ web tool) | game-port engineering | scale automation | 5 slots concurrent |
| **opencode (Kimi/MiniMax)** | V₂'' Codex/o4-mini wiring + R26 impl | per-game R26 tables | LLM-driven config | 12 scheduler jobs, free model |
| **claude-glm** | V₂' GLM expansion, threshold tuning, audit reports | game generalization audit | scale automation | 8 slots, GLM-4.6 |
| **GHA workflows** | per-PR BFT gate + daily regression + Windows recorder build | multi-game gate matrix | bulk regression | unlimited (GitHub-hosted) |
| **aliyun** (if alive) | Phase 1 calibration testing if available | per-game testing | scale testing | conditional — health-check first |

**Cluster routing rule (Iron Law for this roadmap):**
- Code changes that touch `src/oyster_agent_runner/bft/` → **mac-2 Engineer**.
- Code changes that touch `src/oyster_agent_runner/v2_*` (LLM verifier wiring) → **opencode**.
- Audit / threshold-tuning / docs → **claude-glm**.
- CI / release-gate → **GHA**.
- mac-1 only does `git commit -m "..." && git push` of generated diffs.

---

# Risk Register (Phase 1 critical risks)

| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| Tester churn (< 3 testers respond) | MEDIUM | HIGH | Backup: synthetic + Discord paid | mac-1 outreach |
| V₄ Ed25519 key-mgmt complexity | MEDIUM | HIGH | Start with HMAC, migrate iteratively | mac-2 Engineer |
| R25a yaw-mouse correlation false-positive on legit speedrunners | MEDIUM | MEDIUM | Threshold tuning vs 20-honest-ref | claude-glm |
| R26 video hash table size explosion (Minecraft scenes large) | LOW | MEDIUM | Bloom filter + tiered hashes | opencode (per SPEC_R26 §6) |
| GHA Windows runner cost overrun | LOW | LOW | Cache Windows env, run nightly only | GHA config |

---

# AUTONOMOUS EXECUTION ORDER

**These are the next 12 atomic spec dispatches. Execute in order. Each spec has explicit cluster + dep + turnaround.**
**mac-1 = orchestration only. Howard runs `/dispatch <spec>` from mac-1. All code lands on remote cluster.**

## DISPATCH #1 — `R25a-yaw-mouse-correlation-impl`
- **Title:** Implement R25a yaw-mouse Δ correlation residual per SPEC_R25_CROSS_FRAME_ANOMALY §3.2
- **Cluster:** mac-2 (Engineer dispatch via SSH)
- **Expected output:** PR adding `src/oyster_agent_runner/bft/r25a_yaw_mouse.py` + tests; integration into `_auto_bft` pipeline; smoke shows R25a fires on synthetic mouse-replay attack.
- **Dependencies:** none (spec ready)
- **Est turnaround:** 6h (impl + tests + PR)
- **Exit:** GHA gate GREEN, R25a present in BFT consensus output.

## DISPATCH #2 — `R26-video-content-hash-impl`
- **Title:** Implement R26 video-content-hash per SPEC_R26_VIDEO_CONTENT_HASH §4
- **Cluster:** opencode (Kimi K2.5 — strong at numpy/cv2)
- **Expected output:** PR adding `src/oyster_agent_runner/bft/r26_video_hash.py` + per-Minecraft `config/minecraft/r26_table.json` (seed: 100 honest scene hashes); integration into `_auto_bft`.
- **Dependencies:** DISPATCH #1 (shared `_auto_bft` wiring conventions)
- **Est turnaround:** 8h
- **Exit:** R26 catches replayed-video attack RT-04 in adversarial harness.

## DISPATCH #3 — `V4-ops-state-machine-impl`
- **Title:** Implement V₄ buyer-signed state machine (24hr SLA + 48hr HUMAN_REVIEW) per SPEC_V4_BUYER_SIGNED_PROTOCOL §5
- **Cluster:** mac-2 (Engineer)
- **Expected output:** PR adding `daemons/v4_sla_monitor.py` + state column in tasks.db + `web/buyer-signer/` Ed25519 web tool.
- **Dependencies:** none (spec ready)
- **Est turnaround:** 10h (state machine + web tool)
- **Exit:** end-to-end test signs synthetic dataset → BFT GREEN → buyer accept.

## DISPATCH #4 — `V2-prime-prime-codex-wiring`
- **Title:** Wire Codex/o4-mini as V₂'' independent LLM (BFT N=5)
- **Cluster:** opencode (already runs Codex CLI)
- **Expected output:** PR adding `src/oyster_agent_runner/v2_codex.py` + dispatch wiring; BFT consensus operates on N=5 with majority quorum 3/5.
- **Dependencies:** none
- **Est turnaround:** 4h
- **Exit:** N=5 quorum visible in `bft_consensus_output.json`; collusion attack CO-01 caught.

## DISPATCH #5 — `R10-speed-max-R23-codec-lookup-expansion`
- **Title:** Expand V₃ Physics Oracle: R10 speed_max + R23 codec lookup per PRD_FORMULAS §4
- **Cluster:** mac-2 (Engineer)
- **Expected output:** PR extending `src/oyster_agent_runner/bft/v3_physics.py` with R10 (Minecraft sprint = 5.612 m/s + jump bonus) + R23 (codec→fps→keystroke-rate physical constraint).
- **Dependencies:** none
- **Est turnaround:** 5h
- **Exit:** physics-impossible trajectory PI-02 caught; codec-mismatch attack CM-01 caught.

## DISPATCH #6 — `honest-reference-dataset-collection`
- **Title:** Collect 20 honest Minecraft reference sessions (≥ 5 minutes each) for threshold calibration baseline
- **Cluster:** mac-1 outreach (Howard) → mac-2 Engineer ingest
- **Expected output:** `samples/reference_honest/session_{01..20}.tar.gz` + `docs/REFERENCE_DATASET_PROVENANCE.md`
- **Dependencies:** v0.23.0 .exe distributed (DISPATCH #9)
- **Est turnaround:** 5–10 days (depends on tester response)
- **Exit:** 20 sessions ingested + `_auto_bft` re-run shows zero false-positive across all 20.

## DISPATCH #7 — `R20-drift-threshold-tightening`
- **Title:** Re-tune R20a-e statistical drift thresholds against 20 honest reference sessions per SPEC_R20_STATISTICAL_DRIFT
- **Cluster:** claude-glm (statistical analysis)
- **Expected output:** PR updating `config/_auto_bft_thresholds.yaml` + analysis report `docs/R20_THRESHOLD_TUNING_2026Q2.md`.
- **Dependencies:** DISPATCH #6 (need 20 sessions first)
- **Est turnaround:** 3h post #6
- **Exit:** R20 false-positive < 1% on honest set; catch rate ≥ 90% on adv DR-01..05.

## DISPATCH #8 — `adversarial-harness-N5-regression`
- **Title:** Re-run RED_TEAM_TAXONOMY 20-attack adversarial harness with N=5 BFT + R25a + R26 + R10/R23
- **Cluster:** GHA (regression workflow)
- **Expected output:** updated scorecard in `docs/RED_TEAM_TAXONOMY.md` + `BFT_TRUST_REPORT_FOR_BUYER.md` regenerated.
- **Dependencies:** DISPATCH #1, #2, #4, #5 all merged
- **Est turnaround:** 2h
- **Exit:** ≥ 19/20 (≥ 95%) attacks caught; B-01 + B-03 marked CLOSED.

## DISPATCH #9 — `recorder-v0.25.0-build-and-distribute`
- **Title:** Build recorder v0.25.0 .exe with full _auto_bft (15 residuals) + auto-update from v0.20.x
- **Cluster:** GHA (Windows runner)
- **Expected output:** signed .exe artifact + release notes + Chinese tester onboarding.
- **Dependencies:** DISPATCH #1, #2, #4, #5 merged + #8 GREEN
- **Est turnaround:** 4h (build + sign + smoke on Win10/11)
- **Exit:** auto-update succeeds from a v0.20.x test install; BFT runs end-to-end on Windows.

## DISPATCH #10 — `tester-onboarding-CN-doc`
- **Title:** Write Chinese tester onboarding doc (install → record → submit → payout)
- **Cluster:** claude-glm
- **Expected output:** `docs/TESTER_ONBOARDING_CN.md` + `docs/TESTER_FAQ_CN.md` + Lark-shareable version.
- **Dependencies:** DISPATCH #9 (need shippable .exe)
- **Est turnaround:** 2h
- **Exit:** doc covers install, first session, submission, payment, troubleshooting.

## DISPATCH #11 — `daily-bft-regression-GHA-workflow`
- **Title:** Implement daily BFT adversarial regression GHA workflow (full 20-attack harness nightly)
- **Cluster:** GHA
- **Expected output:** `.github/workflows/bft-daily-regression.yml` + Slack/Telegram alert on RED.
- **Dependencies:** DISPATCH #8 (harness must be N=5 ready)
- **Est turnaround:** 3h
- **Exit:** workflow runs nightly; first 7 consecutive days GREEN required for [P1-C7].

## DISPATCH #12 — `phase1-exit-audit`
- **Title:** Phase 1 exit audit — verify all [P1-C1..C7] criteria GREEN, all [P1-A1..A3] anti-criteria respected
- **Cluster:** claude-glm (audit) + mac-2 (verification)
- **Expected output:** `docs/PHASE1_EXIT_AUDIT.md` with binary YES/NO per criterion + evidence link.
- **Dependencies:** DISPATCH #1..#11 all complete
- **Est turnaround:** 2h
- **Exit:** all 7 criteria YES + 3 anti-criteria respected → Phase 2 entry approved.

---

# Reference Spec Index (status as of 2026-05-06)

| Spec | Status | Used By |
|------|--------|---------|
| `PRD.md` / `PRD_EN.md` / `PRD_DIGEST.md` | reference | strategic baseline |
| `PRD_FORMULAS.md` | implementation-ready | R10, R20, R23 math |
| `ARCH_BFT_CONSENSUS.md` | implementation-ready | N=4 → N=5 expansion |
| `ARCH_PINNS_BUYER_SPEC.md` | implementation-ready | V₄ key schedule |
| `SPEC_R13_MULTIMODAL.md` | shipped (R13 in src) | reference |
| `SPEC_R20_STATISTICAL_DRIFT.md` | shipped (R20a-e), threshold-tuning pending | DISPATCH #7 |
| `SPEC_R25_CROSS_FRAME_ANOMALY.md` | **design-only**, R25a impl pending | DISPATCH #1 |
| `SPEC_R26_VIDEO_CONTENT_HASH.md` | **design-only**, impl pending | DISPATCH #2 |
| `SPEC_V4_BUYER_SIGNED_PROTOCOL.md` | **design-only**, ops layer pending | DISPATCH #3 |
| `RED_TEAM_TAXONOMY.md` | reference scorecard | DISPATCH #8 |

---

# Operating Protocol (during this roadmap)

1. **Daily standup (mac-1):** Howard runs `/cluster-status` + `/dispatch status` to see what's RUNNING / DONE / FAILED.
2. **Failure handling:** spec FAIL twice → escalate to [Dispatch #N+] new approach (do not auto-retry 3rd time).
3. **Commit protocol:** mac-2 / opencode / claude-glm produce diffs → push to feature branch → mac-1 reviews PR → squash-merge to main.
4. **Tag protocol:** every merged dispatch advances `recorder-v0.X.Y` minor tag; major tags reserved for Phase exits.
5. **No mac-1 code edits:** if a fix is < 5 lines and urgent, dispatch to opencode (5-min turnaround) — never edit on mac-1.
6. **Iron Law check (every 5 dispatches):** "Have I touched code on mac-1?" → if YES, audit and migrate.

---

**END OF MASTER ROADMAP — execute DISPATCH #1 immediately.**
