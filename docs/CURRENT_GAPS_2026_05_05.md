# Current Gaps — 2026-05-05 (snapshot)

> Howard: "现在缺什么"
>
> Live status across all dimensions. Anything not on this list = done or out of scope.

---

## A. Cluster-shipping (10 critical-path specs PENDING)

These are blocking `v0.2.0-consumer-beta`. Cluster autonomously dispatches them; ETA depends on velocity.

| Spec | What it produces | Status |
|---|---|---|
| G214 | Windows .msi installer (Java bundled) | pending |
| G216 | first-run splash (single opt-in checkbox) | pending |
| G217 | game auto-detector (process scanner) | pending |
| G218 | auto-record orchestrator (start/stop) | pending |
| G219 | system tray UI (cross-platform) | pending |
| G220 | privacy dashboard (per-game opt-in) | pending |
| G221 | signed consent log | pending |
| G241 | Authenticode code signing | pending |
| G243 | release builder (assembles .msi) | pending |
| G253 | EPal session lifecycle hook | pending |

**Plus G228 e2e_smoke** as gate before tagging release.

→ **Howard does not need to act** for this column. Cluster runs autonomously.

---

## B. Howard's manual blockers (need your hands)

These can't be cluster-shipped:

| # | Item | Cost / time | Why |
|---|---|---|---|
| B1 | **Authenticode code-signing certificate** | $200-400/year (EV cert from Sectigo/DigiCert/SSL.com) | Without this, .msi triggers SmartScreen warning → consumers click "Don't run" → ship is dead |
| B2 | **Apple Developer ID account** | $99/year | Same for macOS; needed for G215 .pkg in v0.3.0 (post-v0.2.0) |
| B3 | **EPal API contract** | EPal team time | We've designed the integration (G253-G258) but not coordinated yet. Need: EPal endpoint URLs, auth scheme, sandbox env. Estimate: 2 meetings + 1 doc exchange |
| B4 | **Backend hosting setup** | $20-50/mo | Render/Railway + Postgres + S3 bucket for ingest (G190-G191). Can defer to post-beta if we hand-deliver clips on flash drive at first |
| B5 | **Domain + TLS** | $12/year | `recorder.oysterlabs.ai` or similar; needed before public beta links |
| B6 | **Beta companion recruitment** | EPal team relationship | 10 EPal companions willing to install + test for first 2 weeks |
| B7 | **Legal review of Consumer EULA + privacy policy** | $500-2k one-time | G213 privacy policy + new Consumer EULA (G212 was vendor-version, skipped). Outside counsel review for COPPA/GDPR/CCPA |
| B8 | **Buyer contract** | depends on buyer | Per-clip pricing, volume commitment, payment terms; the actual money pipeline |

→ **You do these in parallel with cluster's column A**.

---

## C. Build / release pipeline (gap, partial)

| Component | Status |
|---|---|
| PyInstaller for Windows | ✅ already used (`gamedata-recorder.exe` exists in v0.1.0-rc9) |
| WiX Toolset for .msi packaging | ❌ not yet — G214 will set this up |
| CI auto-build on tag | ❌ not yet — needs new workflow + signing secrets |
| Auto-update server | ⚠️ G250 queued, not deployed |

→ **Cluster column A handles this**.

---

## D. The "Minecraft license" question

Howard: "我没买这个游戏"

**You don't need to buy it.** Our QA pipeline runs:
- **Paper server** (free, BSD-licensed) → already cached on minipc at `~/oyster-gamedata-pipeline/bin/.cache/paper-1.20.4.jar`
- **Mineflayer bot** (free, MIT) → connects in offline-mode, plays for us
- **mock provider** (built-in) → drives the bot through scripted actions

This is what produces the **63-event identical output across mac + minipc** that we verified.

**When does someone need a real Minecraft license?**
- Only when a HUMAN tester (= EPal companion) wants to play through the consumer flow.
- All EPal companions ALREADY own Minecraft (they play it for clients — that's their job).
- WE never need to own it.

Prism Launcher is installed for FUTURE manual testing. For automated QA, we keep using Paper+Mineflayer.

---

## E. Documentation (mostly done)

✅ MASTER_PLAN_INDEX.md — navigation
✅ EPAL_INTEGRATION_STRATEGY.md — business model
✅ CONSUMER_PIVOT_STRATEGY.md — B2C positioning
✅ CONSUMER_EXE_CRITICAL_PATH.md — release scope + UX commandments
✅ CONSUMER_QA_CHECKLIST.md — tester-friendly QA flow
✅ MC_STACK_VERSIONS.md — version pin authority
✅ ANTI_CHEAT_COMPATIBILITY.md — green/yellow/red tiers
✅ PER_GAME_DATA_SOURCES.md — extraction strategy per game
✅ SINGLE_PLAYER_GAMES.md — 21-title roadmap
✅ PRD_AUDIT_2026_05_04.md — buyer-spec authority
✅ MINIPC_VERIFICATION_2026_05_05.md — cross-machine repro
✅ RECORDER_BOOT_RELIABILITY.md — 10/10 boot reps proof

→ **No new doc gaps**. If something's not on this list, it's not blocking v0.2.0.

---

## F. Money / runway (Howard's call)

Beyond the $200-400 cert + $20-50/mo hosting:

| Cost | Estimate | When |
|---|---|---|
| EPal companion bonus pool | $500-2k for first month | At beta launch |
| Server scaling (after first 100 companions) | $100-300/mo | Post-beta |
| Outside legal review (one-time) | $500-2k | Before public beta |
| Code signing renewal | $200-400/yr | Year 2 |

→ **You decide funding source** (Oysterworld treasury / personal advance / EPal cost-share).

---

## G. The beta launch checklist (Howard's manual flow)

Once cluster ships A + you do B1-B6:

1. ☐ Tag `v0.2.0-consumer-beta`
2. ☐ Build signed .msi via `bin/release_builder_consumer.py` (cluster ships G243)
3. ☐ Test on a fresh Windows VM (or minipc with Prism)
4. ☐ Upload to GitHub Release
5. ☐ Send link to first 10 EPal companions with `CONSUMER_QA_CHECKLIST.md`
6. ☐ Monitor error service dashboard (G233) for failures
7. ☐ Iterate on lint failures + UX feedback
8. ☐ After 7 days at lint-PASS-rate ≥ 90%: open to entire EPal companion base

---

## H. What's NOT on this list (out of scope for v0.2.0)

These are deferred to v0.3.0+:

- macOS support (.pkg notarization)
- Auto-updater rollout
- Backend ingest deployment
- Earnings dashboard / payouts
- Multi-game beyond Minecraft (BeamNG / Stardew / CP2077 / Cities Skylines specs are queued but not in v0.2.0)
- i18n (English-only first)
- Marketing site
- Mobile companion app

Each of these has specs queued; they ship in subsequent releases.

---

## TL;DR — what's missing right now

1. **10 critical-path specs in cluster queue** (autonomous, ETA hours)
2. **8 Howard-manual items** (B1–B8: cert + EPal API + hosting + recruitment + legal + buyer contract)
3. **NOT blocking**: owning Minecraft (we use Paper+Mineflayer)
4. **NOT blocking**: more specs (spec freeze in effect)

Once the 10 cluster-spec ship and B1 (signing cert) + B3 (EPal API) + B6 (beta companions) are arranged, we tag v0.2.0 and ship.
