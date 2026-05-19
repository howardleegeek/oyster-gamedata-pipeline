# EPal Integration Strategy — 2026-05-05

> Howard: "我们的项目背景就是 EPal 通过我们的游戏陪玩社群来录这些数据"
>
> Recording is a **side-effect of paid EPal companion sessions**, not random consumer onboarding. This document captures what that means for the product.

---

## The actual business model

EPal already runs a vetted **game companion community** (professional 陪玩):
- Companions get paid to play games with clients
- EPal handles vetting, payment, dispute resolution, ratings
- Companions specialize per game (diversity is built-in)
- Sessions are time-bounded, payment-gated, quality-rated

Our recorder rides on top:
- **Companion launches an EPal session** → recorder auto-starts on companion's PC
- **Companion plays the paid session normally** → recorder captures buyer-spec tarball
- **Session ends** → recorder auto-stops + auto-uploads
- **Bonus payout** rides EPal's existing payment rails (companion sees `EPal session $X + recording bonus $Y` in one statement)

**Net result**: zero new behavior asked of companions, zero new payment infra, zero new onboarding — just an opt-in flag in the EPal companion app.

---

## Why this is a stronger moat than generic B2C

| Dimension | Generic B2C | EPal companion model |
|---|---|---|
| Trust | We build from zero | Inherited from EPal's existing vetting |
| Payment | We build Stripe/PayPal | Ride EPal's existing payout rails |
| Quality | Anomaly detection guesses at quality | Companion has a professional rating |
| Diversity | Hope users play different games | Pros specialize across all major games |
| Geographic reach | Marketing-budget-bound | EPal's CN + US + EU community |
| Acquisition cost | $$ per install | ~$0 (broadcast inside EPal app) |
| Quality of data | Random play, AFK farming risk | Professional gameplay during paid session |
| Legal floor | Per-user TOS | EPal already has companion + client TOS |

**Buyer pitch becomes**: "Professional-grade gameplay data at companion-community scale, with quality ratings already attached."

---

## What changes in the product roadmap

### SKIPPED (EPal already provides)
- G202 vendor_signup_flow → companions onboarded via EPal
- G212 TOS_VENDOR.md → wraps into EPal's existing companion agreement
- G222 paypal_micropayout_handler → use EPal payout API
- G223 referral_system → community already exists inside EPal

### KEPT (still our responsibility)
- G214/G215 installers (companions install on their PC)
- G216 splash (trimmed: opt-in only, no email/payout — EPal already has those)
- G217/G218 auto-detect + auto-record (the core recording loop)
- G219 system tray UI (companion's local UI)
- G220/G221 privacy dashboard + signed consent (legal floor for client recording)
- G225 auto-updater (consumer-grade reliability)
- G228 e2e smoke (gates v1 release)
- G229 FPS overhead monitor (companions WILL drop us if it lags their pro game)
- G230 in-app clip status (companion sees feedback)
- W28 error reporting service (we still need this)
- W29 code signing + reliability (still required)

### NEW (EPal integration — W30)
- EPal session lifecycle hook (record only during paid sessions)
- Companion quality score wiring (pro rating informs data weight)
- EPal payout passthrough (ride existing rails)
- Client consent handshake (the paying customer also consents)
- Community dashboard (companion sees their data contribution + bonus)
- EPal-flavored onboarding doc

---

## EPal session lifecycle (the core integration)

```
┌────────────────────────────────────────────────────────────────┐
│  EPal app (companion side)                                     │
│  - companion accepts a session: game=Minecraft, duration=2h    │
│  - "record this session for bonus" toggle  [✓]                 │
└────────────────────────────────────────────────────────────────┘
                              │
                  POST /v1/epal/session_start
                  {companion_id, session_id, game, opt_in}
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  Our recorder service (companion's PC)                         │
│  - receives session_start hook                                 │
│  - waits for game process to launch (G217 game_auto_detector) │
│  - starts recording (G218 auto_record_orchestrator)            │
│  - buffers clips locally                                       │
└────────────────────────────────────────────────────────────────┘
                              │
                  EPal session ends (companion clicks End in app)
                              │
                  POST /v1/epal/session_end
                  {companion_id, session_id, rating}
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  Recorder + backend                                            │
│  - stops recording                                             │
│  - finalizes tarball + lints (G165 v3)                         │
│  - uploads to S3 with companion_id + session_id + rating       │
│  - calls EPal payout passthrough: bonus = duration × rate × rating│
└────────────────────────────────────────────────────────────────┘
                              │
                  POST <epal>/v1/companion/bonus
                              ▼
                  EPal credits companion's existing balance
```

**Companion experience**: one toggle in the EPal app, then forget. Bonus shows up alongside their EPal session pay.

**Client experience**: notified at session start "this session may be recorded for AI training, opt-out available", click confirm. EPal already does similar disclosure for session terms.

---

## Compliance posture (re-confirmed for EPal context)

- **COPPA**: EPal's existing minimum-age check (16+) covers this.
- **GDPR**: companion's existing EPal consent + per-session client consent = explicit double opt-in.
- **CCPA**: EPal's existing privacy policy + our additional recording disclosure.
- **Anti-cheat**: same OBS+RawInput passive path; EPal companion accounts are not at risk because companions don't VAC-cheat.
- **Data ownership**: companion grants license at EPal-companion-onboarding sign; client grants per-session.

---

## v1 launch plan with EPal

| Step | Owner | Timing |
|---|---|---|
| Cluster ships W22-W30 (~50 specs) | autonomous cluster | days |
| EPal API hooks defined (jointly with EPal team) | Howard + EPal | week 1 |
| Recorder builds bundled installer (G243) | cluster G243 + manual sign | week 1 |
| Beta with 10 EPal companions | Howard | week 2 |
| Iterate quality bar + payout calc | feedback loop | week 2-3 |
| Open opt-in to entire EPal companion base | Howard + EPal | week 4 |

---

## What this means for the buyer story

> **"Where does the data come from?"** — A vetted community of paid game companions across N games. Each companion has a professional rating, plays for clients during pre-purchased session blocks, and explicitly opts into recording. Quality is rated end-to-end (we tag each clip with the companion's rating + session metadata).
>
> **"How do you scale?"** — EPal's existing community footprint. Marginal cost of a new companion = zero (they're already onboarded). We grow with EPal.
>
> **"Why isn't this just synthetic / random?"** — Companions are professionals; they play games deliberately. Clients pay for engaging gameplay (so companions don't AFK). Sessions are time-bounded (so we know when each capture starts/ends). All data has provenance back to a paid session.

That's a far stronger pitch than "trust us, our random users played a lot."
