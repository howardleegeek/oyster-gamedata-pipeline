# Production Launch SOP — Web Portals (tester + buyer)

> Companion to the recorder/data-pipeline SOP at [`SOP.md`](SOP.md). This
> document covers the **Next.js web portals** (`web-tester`, `web-buyer`)
> end-to-end: clone → local-verify → production-deploy → real users →
> incident response → release cadence.
>
> Last verified end-to-end on macOS (mac1) by Howard 2026-05-08 against
> [`v0.1.0-rc11`](https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/tag/v0.1.0-rc11).

---

## Stage 0 — One-time prerequisites (5 min)

```bash
# Install once per machine. All of these are required.
brew install node                              # 18+ — Next.js 14 supports it
brew install supabase/tap/supabase             # 2.98+ for --output env
brew install libpq && brew link --force libpq  # provides psql for migrations
brew install colima                            # lightweight Docker daemon (Mac)
colima start                                   # daemon needs to be running
```

> **Why Colima not Docker Desktop?** Colima is free, GUI-less, no licence
> dance. Both work; the bootstrap flag `--exclude vector` (already wired) is
> required for Colima. Drop the flag when you switch to Docker Desktop if
> you want the Studio analytics tab.

Sanity check before moving on:

```bash
node --version          # v18.x or v20.x
supabase --version      # 2.98+
psql --version          # any
docker info             # prints daemon state
```

If any of those fail → **STOP**. Don't run bootstrap until they all pass.

---

## Stage 1 — Local validation (3 min)

```bash
git clone https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline
git checkout v0.1.0-rc11        # or `main` for tip-of-tree
./bootstrap-local.sh
```

**What this does**, in order:
1. Pre-flight checks (node, npm, supabase CLI, docker daemon).
2. `supabase start --exclude vector` (10 healthy containers — DB, auth, kong, storage, REST, realtime, studio, pg-meta, inbucket, analytics).
3. Applies buyer migrations (`web-buyer/supabase/migrations/*.sql`) onto the same DB the tester uses (tables don't overlap; comment in `.env.example` confirms shared-stack pattern).
4. `supabase status --output env` → eval into shell → real `API_URL`, `ANON_KEY`, `SERVICE_ROLE_KEY`.
5. Generates `web-tester/.env.local` + `web-buyer/.env.local` with those real keys.
6. `npm install` in both portals (idempotent).

**Expected end-state:**

```
=== Bootstrap complete ===
Next steps — open TWO terminals:
  Terminal 1:  cd web-tester && npm run dev
  Terminal 2:  cd web-buyer  && npm run dev
```

**Start both portals** (two terminals):

```bash
# Terminal 1
cd web-tester && npm run dev    # → http://localhost:3000
```
```bash
# Terminal 2
cd web-buyer && npm run dev     # → http://localhost:3001
```

**Verify** (third terminal):

```bash
./local-smoke.sh
```

**Pass criterion:** `4 passed, 0 failed`. If anything red, see Stage 5 (incident).

**Browser walkthrough** (real-user flow, no fakes):

| URL | What you should see |
|---|---|
| http://localhost:3000 | Tester landing → click **Sign up** |
| http://localhost:54324 | Mailpit/Inbucket — magic-link emails land here |
| (click magic link) | → redirected to `/dashboard` showing **"No uploads yet — download the recorder"** |
| http://localhost:3000/download | 302 redirects to GitHub Release `OysterRecorder.exe` v0.26.0 (real, 426 MB) |
| http://localhost:3001 | Buyer landing — **"Real Minecraft gameplay data for AI training"** + amber "Catalog seeding live" panel (iron-law-honest empty state for fresh DB) |
| http://localhost:3001/browse | Catalog list — "No tarballs match." until first tester upload |
| http://localhost:54323 | Supabase Studio — inspect tables directly |

**Iron-law amber panels** (intentional, not bugs):
- Tester `/payouts` → "Stripe Connect not configured"
- Buyer `/cart`, `/checkout`, `/downloads` → "Stripe Checkout not configured"

Those flip to real flows once Stage 2 item #4 is done.

---

## Stage 2 — Production-readiness gates (Howard's hands)

Source of truth: [`PRODUCTION_GAPS.md`](PRODUCTION_GAPS.md). Five items
require credentials or decisions only Howard can supply.

| # | Item | Time | Cost | Blocking? |
|---|---|---|---|---|
| 1 | Vercel deploy secrets (`VERCEL_TOKEN` + org/project IDs) | 5 min | $0 | **YES** — deploys cannot land without |
| 2 | Apply Supabase migrations to prod project (`supabase db push` × 2) | 5 min | $0 | **YES** — schema doesn't exist on prod |
| 3 | Code-signing cert for the recorder `.exe` | 24-72h provisioning | $80-$200/yr | NO (testers can click through SmartScreen) |
| 4 | Stripe Connect strategy decision | 30 min decision + 1d wire | varies | NO for buyer-browse-only launch; YES for buyer purchases or tester payouts |
| 6 + 8 | Recorder v0.27.0 with HMAC token + direct-to-Supabase upload | ~5h | $0 | NO at launch (current upload route works for <4.5 MB tarballs); YES for real-sized recordings on Vercel |

**Order of operations** (recommended):

1. **#1 + #2 (do both today, 10 min total)** — gets live URLs + working DB.
   - Item #1: see [`PRODUCTION_GAPS.md`](PRODUCTION_GAPS.md) §1 for the four secrets to add.
   - Item #2: see [`MORNING_PASTE_BLOCK.md`](MORNING_PASTE_BLOCK.md) for env-var paste blocks per portal.
2. **Smoke against live URLs** — `TESTER_URL=https://... BUYER_URL=https://... ./watch.sh` for 30 min, verify steady-state green.
3. **Optional #3 (recorder code-signing)** — only if your first wave of testers is sensitive to SmartScreen warnings. Otherwise brief them ("Click 'More info → Run anyway' — it's expected for new releases").
4. **#4 (Stripe Connect)** — gate this on a decision: ship a buyer-browse-only launch first to validate catalog appeal, OR wire Stripe before opening doors. Either is iron-law-honest as long as the amber panels stay honest.
5. **#6 + #8 (recorder v0.27.0)** — schedule for week 2 of production. Server-side server-side groundwork is ready (HMAC verify route + signed-URL split paths designed in PRODUCTION_GAPS.md gap #6 + #8); recorder bump is the missing half.

---

## Stage 3 — Production deploy sequence

Once Stage 2 items #1 + #2 are done:

```bash
# 1. Verify CI green on the commit you want to ship.
gh run list --limit 3 --json name,conclusion --jq '.[] | "\(.conclusion // "running")  \(.name)"'

# 2. Push triggers Vercel deploy automatically (deploy-web-tester.yml +
#    deploy-web-buyer.yml workflows pick up the secrets you added in #1).

# 3. Watch the deploy lanes:
gh run watch

# 4. Once both Vercel lanes succeed, capture the deployed URLs:
#    Look at "Deploy Web Tester" workflow output → preview URL.
#    Same for buyer.

# 5. Apply Supabase migrations to prod project (Stage 2 item #2 if not done yet).

# 6. Smoke against live URLs:
TESTER_URL=https://<tester-deploy>.vercel.app \
BUYER_URL=https://<buyer-deploy>.vercel.app \
  ./watch.sh
```

**Pass criterion:** all 5 columns green for 30 minutes uninterrupted.
If `catalog=N` increments after the first tester upload, the full chain is
verified end-to-end.

---

## Stage 4 — Real-user onboarding

### Tester wave 1 (3-5 testers)

1. Send each tester:
   - Production tester URL (e.g. `https://tester.oystergamedata.com`)
   - 1-paragraph briefing: "Sign up → download the .exe → record 30 min of Minecraft → upload from the recorder. You'll see your hours accumulate on the dashboard. Stripe payouts open week 2; in the meantime [insert payout bridge — Venmo / Wise]."
2. After their first upload, verify on **your** end:
   - Supabase Studio → `tarballs` table → row exists with their `tester_id`
   - `watch.sh` → `catalog=1+`
   - Buyer `/browse` → tarball card visible
3. If row is missing or `d5_verdict='pending'` for >24h, escalate to Stage 5.

### Buyer wave 1 (only after Stage 2 item #4 is done)

1. Pre-launch invite to first 5-10 ML researchers / AI labs.
2. Post a tarball, share the `/tarball/[id]` URL → real preview JSON renders.
3. They sign up → buy → download.
4. Verify on **your** end:
   - Supabase `purchases` row exists
   - Stripe dashboard shows the charge
   - `/downloads` page issues a 24h signed URL successfully

---

## Stage 5 — Incident response

### Symptom: tester `/dashboard` shows "Service unavailable" (503)

1. `gh run list --workflow=deploy-web-tester.yml --limit 1` → was the latest deploy a success?
2. Check Vercel env vars — `NEXT_PUBLIC_SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` set?
3. From your laptop: `curl https://<tester>/api/stats/<known-tester-id>` → 503 with `envVars` array → matches your missing var.
4. Add the missing var → redeploy.

### Symptom: tester gets `429 Too Many Requests` on upload

1. Expected if they hit `>30 uploads/hour` or their IP hits `>12/min` (rate-limit by design).
2. `Retry-After` header tells them when to retry.
3. If a tester legitimately needs higher throughput, raise the constants in `web-tester/lib/rate-limit.ts` and redeploy.

### Symptom: tester upload returns 413

This means the tarball is >4.5 MB on Vercel (platform body-size cap, gap
#8). **This is expected once recordings get larger than test-size.** Two
remedies:
- **Short term:** ask tester to record shorter sessions (under ~5 MB).
- **Permanent:** complete gap #8 — server-side direct-to-Supabase signed-URL split (designed in PRODUCTION_GAPS.md, ships with recorder v0.27.0).

### Symptom: real CVE / security advisory on a dependency

1. `cd web-tester && npm audit --omit=dev` → see production-only vulns.
2. If `next` has a new patch, bump within the 14.2.x line: `npm install next@<latest-14.2>`.
3. Re-run iron-law lint + both builds → if green, commit + push + tag a new rc.

### Rollback

```bash
# Identify last known-good commit on origin/main
git log --oneline | head -10

# Revert the bad commit (creates a new commit, doesn't rewrite history)
git revert <bad-sha>
git push origin main

# Vercel auto-deploys the revert. ~2 min back to known-good state.
```

> **Never `git push --force` to main during incident response.** The
> revert pattern is reversible; force-push is not.

---

## Stage 6 — Release cadence

Two parallel release lines on this repo:

| Line | Tag prefix | Cadence | What it ships |
|---|---|---|---|
| Web portals | `v0.1.0-rcN` → `v0.1.0` → `v0.2.0` | Per substantive code/security change | `web-tester` + `web-buyer` Next.js apps + supporting libs |
| Recorder | `recorder-vX.Y.Z[-modifier]` | Per recorder feature/Windows build | `OysterRecorder.exe` Windows binary + `OysterRecorder-onedir.zip` |

Latest in each line:
- Web portals: [`v0.1.0-rc11`](https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/tag/v0.1.0-rc11)
- Recorder: [`recorder-v0.26.0-real-game-state`](https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/tag/recorder-v0.26.0-real-game-state)

**Cut a new web-portal RC when:**
- A security CVE is closed
- A user-visible feature lands (new page, new flow)
- A migration applies (schema change)
- An iron-law-affecting change ships

**Graduate `v0.1.0-rcN` → `v0.1.0`** (drop the rc suffix) only when:
- All five Stage 2 items are done
- 7 consecutive days of green `watch.sh`
- At least 3 testers and 1 buyer have completed full flow without intervention

---

## Quick reference card

```
LOCAL DEV
  brew install node supabase/tap/supabase libpq colima
  brew link --force libpq
  colima start
  ./bootstrap-local.sh
  cd web-tester && npm run dev    # Terminal 1
  cd web-buyer  && npm run dev    # Terminal 2
  ./local-smoke.sh                # 4/4 green = pass

PRODUCTION SMOKE
  TESTER_URL=https://... BUYER_URL=https://... ./watch.sh

INCIDENT TRIAGE
  gh run list --limit 5
  curl -sI https://<portal>/api/healthz   # status
  watch.sh                                 # live signal

ROLLBACK
  git revert <bad-sha> && git push origin main
```

---

## Companion docs (cross-links)

- [`LOCAL_DEV.md`](LOCAL_DEV.md) — local-dev quick-start (subset of Stage 1)
- [`PRODUCTION_GAPS.md`](PRODUCTION_GAPS.md) — exhaustive gap audit
- [`TOMORROW_RUNBOOK.md`](TOMORROW_RUNBOOK.md) — first-10-minutes-after-coffee guide for launch day
- [`REAL_USER_TEST_PLAYBOOK.md`](REAL_USER_TEST_PLAYBOOK.md) — user-flow play-by-play
- [`MORNING_PASTE_BLOCK.md`](MORNING_PASTE_BLOCK.md) — Vercel env-var paste blocks
- [`SOP.md`](SOP.md) — recorder/data-pipeline SOP (different domain)
- [`watch.sh`](watch.sh) — 24/7 production health monitor
- [`docs/AUTO_HEAL_LOOP.md`](docs/AUTO_HEAL_LOOP.md) — closed-loop diagnostics auto-heal (recorder-side)
- [`docs/AUTO_DETECTION.md`](docs/AUTO_DETECTION.md) — unified 6-layer detection orchestrator (host / stack / daemons / cluster / CI / backlog)

---

## Iron-law commitments this SOP enforces

- Every step references real verified evidence (not fabricated).
- "Pass criterion" is a real probe (HTTP 200, real curl output, real DB row).
- Amber `<NotConfigured>` panels are honest signals, not bugs to hide.
- Rate limit / 413 / 503 are real platform constraints, not embarrassment.
- No fake data, no synthetic-success, no mock fallback paths.

— Howard Li, Oysterworld Inc, 2026-05-08
