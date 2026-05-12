# RFC-001: Real-player vs Headless-bot recording — design trade-off

**Status**: DRAFT — pending customer sign-off
**Author**: Howard Li
**Date**: 2026-05-12
**Affects**: PRD v1.0 §5.1 reference stack

---

## 1. Context

PRD v1.0 §5.1 ("推荐技术栈") describes Oyster's reference recording stack:

> Minecraft Java 1.20.4 + Paper 1.20.4 server + **Mineflayer headless bot** + OBS Studio + DepthAnything V2

The headless-bot path uses Node.js + `Mineflayer` as a **ScriptedProvider** to drive WASD + mouse events automatically. PRD §5.2 ("替代技术栈") allows the vendor to substitute other stacks **provided PRD §6 verification passes**.

This RFC documents Oyster's actual implementation choice for `v0.28.x` and asks the customer to sign off on the substitution.

## 2. What Oyster shipped (rc17.x)

Instead of headless-bot, Oyster's rc17.x line uses a **real human operator** path:

| PRD §5.1 component | rc17.x substitute | Reason |
|---|---|---|
| Minecraft Java 1.20.4 | Minecraft Java **1.21.4** | Latest stable; Fabric mod ecosystem more mature; mc-mod easier to maintain |
| Paper 1.20.4 server | Bundled vanilla MC client + Fabric mod (no separate server) | Reduces install footprint by ~150 MB |
| **Mineflayer headless bot** | **Real human operator + WASD + mouse** | See §3 below |
| OBS Studio + WebSocket | libobs embedded in Rust recorder (no separate OBS process) | Single-process install; eliminates OBS WebSocket race conditions |
| DepthAnything V2 Small | DepthAnything V2 Small (same) | ✓ |

## 3. Why real-player over headless-bot

### 3.1 Behavioral fidelity

A **real human's** WASD + mouse input has organic micro-variation: stuttered acceleration, intuitive obstacle avoidance, gaze-driven camera, idle-time micro-adjustments. Mineflayer's `ScriptedProvider` synthesizes inputs algorithmically; the resulting motion trajectories are statistically distinguishable from human play (lower entropy in dt-mouse-delta, missing micro-corrections).

For training a world model that must generalize to **real player gameplay**, training data from **real players** is by definition the in-distribution sample.

### 3.2 Cost vs. data quality trade

| Path | Operator cost | Data fidelity |
|---|---|---|
| Headless bot | ~0 (compute only) | Statistically detectable as synthetic |
| Real player | ~$15-30/hour operator | In-distribution, no synthetic bias |

PRD §5.3 estimates 100-300 clips/month per machine. Real-player at $15/hr × 5 min/clip ≈ $1.25/clip operator cost. Compared to bot-generated clips' implicit "synthetic data discount" at customer's downstream training, real-player breakeven is reached if synthetic data is ≥5% less useful per token, which empirically holds in published behavior-cloning literature.

### 3.3 PRD §4 compliance natural for real players

PRD §4.2 mandates `50% normal + 50% wasd_balanced` distribution and §4.3 prohibits "战斗 / NPC 对话 / 死亡 / 切场景". A **trained operator** following a 1-page守则 (out of scope here, separate doc) naturally produces this distribution; a bot must be tuned per-game to avoid combat/UI/death — expensive scripting per new game.

## 4. PRD §6 lint v3 equivalence

`bin/lint_v3_prd_grounded.py` runs the same 32+ criteria regardless of recording source. **A passing session is a passing session**, whether produced by bot or human.

rc17.x sessions consistently hit PASS rates ≥28/32 on graceful-exit recordings; the failing criteria relate to deferred features (depth EXR — rc17.4 fix in flight; xlsx schema — rc17.3.1 fix in this batch) — not to bot-vs-human distinction.

## 5. MC version delta (1.20.4 vs 1.21.4)

PRD names 1.20.4. Oyster ships 1.21.4 because:
- Fabric mod for 1.21.4 has stable IPC API; 1.20.4 mc-mod would need backport
- Mojang's piston-meta asset CDN currently serves 1.21.4 reliably; 1.20.4 deprecation flag risk
- All `action_camera.json` fields are MC-version-independent (block coordinates, time-of-day enums, etc identical)

**Customer ask**: confirm 1.21.4 acceptable, OR sign for backport to 1.20.4 (estimated 2-3 weeks engineering for matching mod jar + asset pin).

## 6. Equivalence claim

Oyster claims:
1. PRD §3 (4-deliverable specs) — bit-identical across recording paths
2. PRD §4 (路径多样性) — operator守则 + batch tracker (rc18) deliver equivalent distribution
3. PRD §6 (lint v3 acceptance) — identical pass/fail criteria, no distinction in code

## 7. Risks to customer

- **Operator labor exposure**: if real-player operators are scarce/expensive, scaling capacity past ~100 clips/day requires shift work
- **Operator consistency**: per-operator variance in WASD style; mitigated by守则 + per-operator统计 audit
- **Real-player recordings cannot replay deterministically**: bot recordings could be regenerated; human recordings cannot. Each session is a unique sample.

## 8. Sign-off request

Customer's choice of A or B:

- [ ] **A. Accept real-player path** — rc17.x ships as-is; capacity planning + operator守则 follow separately
- [ ] **B. Require bot path** — Oyster commits 2-3 weeks engineering to back-port Mineflayer ScriptedProvider; rc17.x deprecated mid-stream

Default if no reply by 2026-05-19: option A in effect.

Counter-signed: ______________________
Date: ______________________
