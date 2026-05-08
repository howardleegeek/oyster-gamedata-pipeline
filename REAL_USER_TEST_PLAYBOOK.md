# Real-User Test Playbook — Tester + Buyer Flows

**Use this with a live human in the room. Each step is a check the
tester / buyer experiences themselves. Iron-law: every screen they
see has either real data or `<NotConfigured>` — never a fabrication.**

> **Howard 2026-05-08 — Stripe deferred for this demo.** Scope is:
>
> **Tester:** signup → download .exe → record gameplay → upload tarball → see real entry on /dashboard.
> Skip flow A4 (Stripe Connect onboarding); /payouts will render `<NotConfigured>` (that's the iron-law-honest "coming soon" state).
>
> **Buyer:** browse catalog → inspect a tarball detail → see real preview JSON.
> Stop at /tarball/[id]. Skip flows B3 (cart→checkout) + B4 (download); /cart and /checkout will render `<NotConfigured>`.
>
> The "early access" pitch is: *"This is what's live today — recording + catalog. Payments next week."*

---

## Setup before the tester arrives

```bash
# Both portals running on Vercel (or local for dry-run)
WEB_TESTER_URL=https://<your-tester-deploy>.vercel.app
WEB_BUYER_URL=https://<your-buyer-deploy>.vercel.app

# Sanity check both are reachable + not in NotConfigured mode
curl -sI "$WEB_TESTER_URL/dashboard" | head -1   # 200 OK if Supabase env wired
curl -sI "$WEB_BUYER_URL/browse"      | head -1  # 200 OK
```

If either returns a page containing "Supabase not configured", stop —
fill the missing env var in Vercel before continuing.

---

## Flow A — Tester journey (15 min)

### A1. Sign up
1. Tester opens `$WEB_TESTER_URL`
2. Click "Get paid to record →" → `/signup`
3. Enter their email → "Send sign-up link"
4. Click magic link in inbox → lands on `/dashboard`

**Pass:** They see their real email in the header, real (zero) earnings,
real (empty) tarball list. No "[DEV MODE]" banners anywhere.

**Fail mode:** Amber NotConfigured panel → Supabase env var missing.
**Fail mode:** Email never arrives → Supabase auth provider mis-configured
in Supabase dashboard.

### A2. Download recorder
1. From `/dashboard`, click "Download recorder"
2. Lands on `/download` showing their tester ID baked into filename
3. Click "Download .exe" → starts download of
   `OysterRecorder-<short>-<full>.exe`

**Pass:** Real .exe downloads (~30-50 MB).

**Fail mode:** 404 page → `RECORDER_EXE_URL` env var pointing at a
non-existent path. Either set the env var to a public GitHub Release
URL, or place the .exe at `web-tester/public/downloads/OysterRecorder.exe`.

### A3. Run the recorder + record gameplay
1. Tester runs the .exe on their Windows machine
2. Recorder validates auth, reads tester_id from filename
3. Tester launches Minecraft with the Fabric mod loaded
4. Tester plays for 5-10 minutes (any survival/creative session)
5. Recorder captures: video.mp4, action_camera.jsonl, depth/, gameinfo.xlsx
6. Recorder packages tarball, computes SHA-256, posts to /api/upload-tarball

**Pass:** `/dashboard` shows the new tarball within 30s of upload finish,
in `pending` verdict (D5 will grade it later).

**Fail mode:** 503 from /api/upload-tarball → Supabase env var missing.
**Fail mode:** 422 sha256 mismatch → recorder bug, file corruption.
**Fail mode:** Tarball appears but never moves out of `pending` → D5
classifier offline (separate cluster issue).

### A4. View earnings — DEFERRED (Stripe not in this demo)

**Skip this flow tomorrow.** Don't click "Payouts" in nav during the
live demo — it will render `<NotConfigured>` (which is iron-law-honest
but distracting in front of a fresh tester).

If a tester asks "when do I get paid?" the answer is:
> *"This week's payouts run as a one-off bank transfer based on accepted
> tarballs (your /dashboard shows duration + verdict). Stripe Connect
> automation ships next week — we wanted to ship the recording flow
> first and prove the data quality."*

When you wire Stripe later: set `STRIPE_SECRET_KEY` in Vercel +
redeploy. /payouts then shows real Stripe Express onboarding +
dashboard link, /api/stripe/connect/* routes go live.

---

## Flow B — Buyer journey (10 min)

### B1. Browse catalog
1. Buyer opens `$WEB_BUYER_URL`
2. Sees real marketing copy (license-clean, quality-graded, multi-modal)
3. If catalog has real tarballs: "Featured tarballs" grid renders
4. If catalog is empty (early days): no grid, just marketing
5. Click "Browse catalog" → `/browse`

**Pass:** Real tarballs from your tester pool show up. Filters work.

**Fail mode:** NotConfigured panel → Supabase env vars missing on the
buyer-portal Vercel project.

### B2. Inspect a tarball
1. Click any tarball card → `/tarball/<id>`
2. See real metadata: real D5 score, real size, real duration, real SHA-256
3. Click "Download sample JSON →" — opens `/api/tarball/[id]/preview`
4. JSON returned has REAL action_camera records (not fabricated)

**Pass:** Sample JSON contains real recorded mouse/camera deltas, not
the deterministic-but-fake `sampleActionCameraRecords` patterns.

**Fail mode:** 404 with "Preview not yet generated for this tarball" →
the ingest pipeline hasn't materialized
`<bucket>/<tarball_id>/action_camera_preview.jsonl`. Run
`bin/regenerate_action_camera_preview.py` (TODO if it doesn't exist).

### B3. Add to cart + checkout — DEFERRED (Stripe not in this demo)

**Stop the buyer demo at /tarball/[id] tomorrow.** Don't click "Add to
cart" or navigate to /cart or /checkout — they all render
`<NotConfigured>` until Stripe is wired.

If a buyer asks "how do I license this?" the answer is:
> *"You're looking at our early-access catalog — same real tarballs we'll
> charge $25/GB for next week. The Stripe checkout integration is
> built and tested locally, we're just holding the live keys until our
> Connect onboarding completes. If you want first-pick licensing,
> drop your email at gamedata@oyster.example and we'll wire you up
> manually this week."*

When you wire Stripe later: set `STRIPE_SECRET_KEY` +
`STRIPE_PUBLISHABLE_KEY` + `STRIPE_WEBHOOK_SECRET` in Vercel +
redeploy. /cart and /checkout then go live.

### B4. Download tarball + license cert — DEFERRED (Stripe gated)

Same deferral. /downloads requires a real purchase row in the DB,
which can't exist without Stripe Checkout having created one. Skip
in tomorrow's demo.

(Alternatively: pre-seed a buyer + purchase row manually in Supabase
if you want to demo the download UX without Stripe — but this is more
trouble than it's worth for tomorrow.)

---

## Iron-law sanity checks during the demo

If you ever see ANY of these in front of a real user, STOP and fix
before continuing:

- ❌ Any UI text containing "DEV MODE" or "[DEV MODE: ...]"
- ❌ Any tarball with "Diamond mine speedrun — caves & ravines" or
  "Megabuild — castle parapet, sandstone palette" titles (those were
  the fabricated 5)
- ❌ Any D5 score of exactly 0.93, 0.89, 0.85, 0.91, or 0.81 (the
  fabricated fixed values — real scores are continuous floats)
- ❌ Any Stripe session ID starting `dev_session_`
- ❌ Any tester email `sample-tester@example.com`
- ❌ Any account ID starting `acct_mock_`
- ❌ A "downloaded" file that opens as plaintext starting
  `# OysterRecorder placeholder for tester ...`

If any appear: the deploy is running an old commit. Force-redeploy.

---

## Demo script for first impressions (60 sec elevator)

> "This is Oyster GameData. Real Minecraft players record their gameplay,
> we pay them six dollars an hour, and we sell the data to AI labs at
> twenty-five dollars a gigabyte. Every tarball is graded by our D5
> quality model — we surface the score so buyers filter by quality.
>
> Tester signs up here [show /signup], downloads a recorder build with
> their ID baked into the filename [show /download], plays Minecraft,
> and uploads tarballs [show /dashboard with real entry].
>
> Buyer browses the catalog [show /browse], inspects a sample with
> real action_camera traces [show /tarball/<id> + Download sample JSON].
>
> Stripe checkout + automated payouts ship next week — today we're
> proving the recording → upload → quality-grading loop with real
> testers. License-clean, quality-graded, multi-modal. No scraped
> Twitch footage. No DMCA risk."

---

## Voice line

The product works when the data is real.
The data is real when the code refuses to fabricate it.
Welcome to production.
