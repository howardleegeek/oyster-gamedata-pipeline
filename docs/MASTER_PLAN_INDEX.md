# Master Plan Index — 2026-05-05

> Howard: "之前的不需要删除 — MECE"
>
> All prior strategy docs stay. This index makes them MECE
> (Mutually Exclusive lanes, Collectively Exhaustive coverage)
> so anyone landing in the repo can find the right doc in one hop.

---

## 5-minute read order (top → bottom)

| # | Doc | Lane (what it owns) |
|---|---|---|
| 1 | `MASTER_PLAN_INDEX.md` (this file) | navigation hub; read first |
| 2 | `EPAL_INTEGRATION_STRATEGY.md` | **business model** — EPal companion network is the data source; lifecycle diagram, buyer pitch, 4-week launch plan |
| 3 | `CONSUMER_PIVOT_STRATEGY.md` | **product positioning** — B2B → B2C pivot; what stays, what re-frames, what's cut; v1 scope narrowing addendum |
| 4 | `CONSUMER_EXE_CRITICAL_PATH.md` | **release scope** — 10 P0 specs that must ship for `v0.2.0-consumer-beta`; what's deferred; UX-first principles |
| 5 | `SOP_LAO_LIU.md` | **vendor/internal SOP** — 30-second handoff for setting up MC stack |
| 6 | `MC_STACK_VERSIONS.md` | **MC stack pin authority** — Java Edition 1.20.4 / Mineflayer ^4.20 / Java 21 / Node 20; install flow + compatibility matrix |
| 7 | `ANTI_CHEAT_COMPATIBILITY.md` | **anti-cheat policy** — green/yellow/red tier classification; mitigation paths |
| 8 | `PER_GAME_DATA_SOURCES.md` | **per-game extraction strategy** — official channels per game; never memory reads |
| 9 | `SINGLE_PLAYER_GAMES.md` | **catalog + roadmap** — 21 single-player titles, 4 priority tiers, SDK pins |
| 10 | `PRD_AUDIT_2026_05_04.md` | **buyer-spec authority** — 24 PRD acceptance criteria, canonical 20-field schema, all gaps closed by G161-G165 |
| 11 | `PRD_OPTIMIZATION_PROPOSAL.md` | **PRD upgrades** — research-backed proposals (RLDS / VPT / IMU / language instruction) for v1.1+ |
| 12 | `BUG_HUNT_2026_05_04.md` | **bug audit** — 6 production bugs found via distributed inspection, status |
| 13 | `RECORDER_BOOT_RELIABILITY.md` | **recorder reliability** — 10/10 boot reps, deterministic, OBS init verified |
| 14 | `MINIPC_VERIFICATION_2026_05_05.md` | **minipc cross-machine reproducibility** — 63 events identical to mac-air-4 |
| 15 | `LANDING_PUNCHLIST.md` | **release engineering** — Tier 1/2/3 punch list, 5-line MVP commands |
| 16 | `HARNESS_ARCHITECTURE.md` + `HARNESS_FAILOVER.md` | **harness internals** — daemon, lock, watchdog, multi-host failover |

---

## MECE coverage check

Each lane below is owned by exactly one doc:

| Lane | Doc | Mutually exclusive? |
|---|---|---|
| Business model (who's the data source) | `EPAL_INTEGRATION_STRATEGY.md` | ✅ |
| Product positioning (B2B vs B2C) | `CONSUMER_PIVOT_STRATEGY.md` | ✅ |
| Next release scope | `CONSUMER_EXE_CRITICAL_PATH.md` | ✅ |
| MC version authority | `MC_STACK_VERSIONS.md` | ✅ |
| Anti-cheat policy | `ANTI_CHEAT_COMPATIBILITY.md` | ✅ |
| Per-game extraction | `PER_GAME_DATA_SOURCES.md` | ✅ |
| Game catalog | `SINGLE_PLAYER_GAMES.md` | ✅ |
| Buyer-spec PRD | `PRD_AUDIT_2026_05_04.md` | ✅ |
| Recorder reliability | `RECORDER_BOOT_RELIABILITY.md` | ✅ |
| Cross-machine repro | `MINIPC_VERIFICATION_2026_05_05.md` | ✅ |
| Harness internals | `HARNESS_ARCHITECTURE.md` | ✅ |

**No overlap. No gap. MECE confirmed.**

---

## Critical-path snapshot (what's in flight RIGHT NOW)

10 P0 specs in cluster queue for `v0.2.0-consumer-beta`:

```
G214  installer_one_click_windows.py  ─┐
G216  onboarding_consumer_splash.py    ├─ what consumer sees
G219  system_tray_consumer_ui.py       │
G220  consumer_privacy_dashboard.py    ┘
G217  game_auto_detector.py            ─┐
G218  auto_record_orchestrator.py      ├─ what runs in background
G221  consent_log_signed.py            │
G253  epal_session_lifecycle_hook.py   ┘
G241  code_signing_windows_authenticode.py  ─┐── release pipeline
G243  release_builder_consumer.py            ┘
```

After cluster ships these → manual sign + test on minipc → tag `v0.2.0-consumer-beta` → first 10 EPal companions.

---

## Spec freeze in effect

No new specs until `v0.2.0` ships. Per `CONSUMER_EXE_CRITICAL_PATH.md` § "Spec freeze":

> Adding more specs delays the .msi. Anything beyond the 10 critical-path specs goes into a "post-v0.2.0" bucket.

If a new requirement surfaces, append to the post-v0.2.0 bucket here, do NOT queue it in audit_gaps.yaml until v0.2.0 ships:

### post-v0.2.0 bucket (parking lot)

(empty for now — add here when needed; format: bullet + 1-line rationale)

---

## Autonomous loop note (Howard: "你可否自动跑？")

The cluster is autonomous: harness daemon picks up pending specs every iteration, dispatches to mac-2 cluster (minimax_agent_simple.py), polls completion, commits + pushes. Howard does not need to baby-sit. New `feat(harness:G2xx)` commits arrive automatically.

What's NOT autonomous and needs Howard:
- Code-signing certificate procurement (Authenticode + Apple Developer ID)
- EPal API spec coordination with EPal team
- Beta companion outreach (recruit first 10)
- Final release tag + GitHub Release publish

Everything else runs unattended.
