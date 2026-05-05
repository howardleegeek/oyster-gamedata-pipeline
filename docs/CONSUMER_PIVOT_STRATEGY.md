# Consumer Pivot Strategy — 2026-05-05

> Howard's directive: "我们最终产品要面向 游戏爱好者和消费者
> 让他们直接装我们的软件 来满足buyer的需求"
>
> **Final product = B2C software gamers install themselves.
> Buyer's data needs are met by aggregating consumer-captured clips.**
> NOT a professional vendor pipeline.

---

## What changes (B2B → B2C)

| Dimension | OLD (B2B) | NEW (B2C) |
|---|---|---|
| User | ~hundreds of pro vendors | millions of gamers |
| Onboarding | KYC + W-9 + runbook | email + PayPal in 2 clicks |
| Setup | manual config + CLI | zero-config one-click .msi |
| Operation | F9 manual record | auto-detect game + auto-record |
| UI | none / CLI / portal | system tray icon + native UI |
| Payout | Stripe Connect monthly ACH | PayPal micro-payouts on threshold |
| Legal | Vendor TOS (heavy contract) | Consumer EULA + privacy dashboard |
| Support | manual ticket | Discord + FAQ + auto-update + crash reporter |
| Growth | manual recruitment | referral bonus + viral loop |

## What stays (core architecture is consumer-ready by accident)

✅ `recorder.exe` is already passive OBS + Raw Input (anti-cheat-safe; same as Twitch streaming)
✅ Buyer-spec v1 schema is game-agnostic — same data shape regardless of who captures
✅ Multi-game extractor architecture (W21 specs) — Minecraft + BeamNG + Stardew + CP2077 + Cities Skylines
✅ Lint v3 PRD validation (G165) — consumer captures linted same as vendor captures
✅ Cluster-shipped backend ingest (G190) + S3 path — works for either user model
✅ Anti-cheat compatibility doc (`ANTI_CHEAT_COMPATIBILITY.md`) — already consumer-aware

## What was over-engineered for B2B (re-frame, don't delete)

- W23 G190 backend_ingest_handler.py → keep, add anonymous-user mode
- W23 G191 s3_presigned_url_issuer.py → keep, scale rate-limits up
- W23 G192 vendor_portal → re-frame as "earnings dashboard inside the app"
- W23 G193 payout_calculator → keep math, swap Stripe Connect for PayPal API
- W24 G202 vendor_signup_flow → soften to consumer signup (email + PayPal only)
- W24 G212 TOS_VENDOR.md → rewrite as Consumer EULA
- W24 G208 / G209 buyer SDKs → still useful (B2C is on the producer side; buyers are still B2B)

## What's brand-new for B2C (W25 specs queued)

| Spec | NEW file | Why consumer-only |
|---|---|---|
| G214 | bin/installer_one_click_windows.py | Windows .msi with bundled Java + auto-launch — consumers won't run pip |
| G215 | bin/installer_macos_pkg.py | macOS .pkg installer for Mac gamers |
| G216 | bin/onboarding_consumer_splash.py | First-run splash + age verification + region + opt-in |
| G217 | bin/game_auto_detector.py | Windows process scanner detects when supported games launch |
| G218 | bin/auto_record_orchestrator.py | Game launches → auto-start; exit → auto-stop + upload |
| G219 | bin/system_tray_consumer_ui.py | Tray icon + native menu — consumers expect a UI, not CLI |
| G220 | bin/consumer_privacy_dashboard.py | Per-game opt-in, what's recorded, "delete my data" button |
| G221 | bin/consent_log_signed.py | Legally-binding signed consent per session (GDPR + CCPA + COPPA) |
| G222 | bin/paypal_micropayout_handler.py | PayPal API for $1-50 micro-payouts (vs Stripe KYC heavy) |
| G223 | bin/referral_system.py | Consumer A invites B → A gets bonus credit (B2C growth) |
| G224 | bin/in_app_earnings_counter.py | Real-time earnings inside the tray UI |
| G225 | bin/auto_updater_winsparkle.py | Auto-update infrastructure (consumers won't manually update) |
| G226 | bin/crash_reporter_sentry.py | Sentry-style crash reporting (consumers won't file GitHub issues) |
| G227 | docs/CONSUMER_LANDING_COPY.md | Marketing site rewritten "play games, earn money" |

## The 30-second consumer pitch

1. **Download** → click installer → done
2. **Play games** → we record the ones we support, auto-paused on others
3. **Get paid** → PayPal threshold $5 → real money for time you'd play anyway
4. **Privacy** → you opt-in per game, you can delete anything anytime, we never share your account info

## What this does to the buyer story

Buyer's data needs are now satisfied by **scale + diversity** (millions of gamer-hours across 21+ games) instead of curated professional captures. That's actually a STRONGER buyer pitch:
- Larger N → better world-model training
- Real-player diversity → not synthetic / scripted
- Always-on growth → buyer's dataset grows monthly without our headcount growing
- Behavioral authenticity → real gameplay, not paid-vendor "I need to hit my quota" patterns

## Anti-cheat re-confirmation

Already covered in `docs/ANTI_CHEAT_COMPATIBILITY.md` — consumer running our recorder = consumer running OBS Studio. Streamers already do this on every game. **No additional anti-cheat exposure introduced by going consumer.**

## Compliance sanity

- **COPPA**: under-13 users blocked at signup; age-gate in installer
- **GDPR**: EU users get explicit consent + DSAR endpoint + 30-day deletion
- **CCPA**: California users get "do not sell" opt-out
- **Children's data**: even 13-17 capped account features (no payouts to minors)

## Open questions for Howard

1. PayPal vs Cash App vs Steam Wallet credits as primary payout?
2. Threshold for first payout — $1 (low friction) vs $5 (less spam)?
3. App-store distribution (Microsoft Store / Mac App Store) or direct download only?
4. iOS / Android companion app for "monitor your earnings" — needed v1 or post-MVP?
