# Tomorrow Morning Runbook — Real-User Test Day

**Generated overnight by Claude (Opus 4) — Howard 2026-05-08**

You said: 积极性推进 我睡觉了 自动推进 一定要production级别. 项目due了.
This is what's ready for you, what needs your touch first thing, and the
exact play-by-play for the live demo.

---

## TL;DR — first 10 minutes after coffee

```bash
cd ~/Downloads/oyster-agent-runner

# 1. Push the overnight commits (I couldn't push to main without you)
git log --oneline origin/main..HEAD     # see what's local
git push origin main

# 2. Watch CI go green (all the failing checks I fixed)
gh run watch

# 3. Real-user test sanity: provision env vars in BOTH portals
cp web-tester/.env.example web-tester/.env.local
cp web-buyer/.env.example  web-buyer/.env.local
# Fill in real Supabase + Stripe keys in both files

# 4. Run the playbook (REAL_USER_TEST_PLAYBOOK.md) end-to-end
```

If any step fails, jump to the matching section below.

---

## What's already done (overnight)

### Iron-law audit — landed in `5ae84a3`
40 files. Killed every fabricated-data fallback in **web-buyer** (the
9 KB `lib/sample-data.ts`, the `dev_session_*` fake Stripe minting in
`/api/checkout`, the `sampleActionCameraRecords` preview, fake buyer
emails, the `stubBody` octet-stream download), plus residual web-tester
violations (`OysterRecorder placeholder` text-stub at /api/download/[id]
and the `/tmp-uploads` synthetic-success at /api/upload-tarball). 16
iron-law lint tests now block re-introduction.

### CI break-fix — pending push (uncommitted, ready for your `git push`)
Discovered while running `gh run list`:

| CI lane                           | Was failing | Fix                                                     |
|----------------------------------|-------------|---------------------------------------------------------|
| `python-tests (3.11)`            | `ModuleNotFoundError: yaml` | Added `PyYAML>=6.0` to `[test]` extras in pyproject.toml |
| `test / Lint with ruff`          | 6 × F401    | `ruff check --fix tests/` (test_storage_backend, test_stripe_connect, test_deploy_mod_to_cluster) |
| `Deploy Web Tester (Vercel)`     | npm cache path missing | Generated `web-tester/package-lock.json` via `npm install` |
| `Deploy Web Buyer (Vercel)`      | (same)      | Generated `web-buyer/package-lock.json` via `npm install` |
| web-tester typecheck             | 2 × TS error | Added `mode: 'live'` to `OnboardResponse`; let TS infer SupabaseClient generic |
| web-buyer typecheck              | 2 × TS error | Pinned Stripe API to `2024-06-20`; same Supabase generic fix |
| web-tester lint                  | unescaped `'` | Replaced with `&apos;` in /payouts copy |

### Local verification (all green on Mac, Python 3.9 + node 20)
- ✅ web-tester: `npx tsc --noEmit` clean, `npm run build` succeeds (8 pages, 6 api routes)
- ✅ web-buyer: `npx tsc --noEmit` clean, `npm run build` succeeds (8 pages, 7 api routes)
- ✅ pytest: 79/79 critical tests pass (iron-law 16 + spec-lint 8 + storage 19 + stripe 29 + deploy 11; 4 moto-skip = test infra)
- ✅ env.example files rewritten to describe hard-gate behavior, not "DEV MODE with sample data"

---

## What you have to do (can't be automated overnight)

### 1. Push the commits — needs you
The push-to-main guard refused my generic auto-mode pre-auth. You'll
push after coffee:

```bash
git log --oneline origin/main..HEAD
# expect to see at minimum:
#   5ae84a3 fix(IRON-LAW): full audit ...
#   <pending> fix(ci): green up failing lanes + portal lockfiles
git push origin main
```

### 2. Provision real Supabase + Stripe keys — needs you
The portals NOW hard-gate when keys are missing. Without them, every
page on `/dashboard`, `/payouts`, `/browse`, `/cart`, `/checkout`,
`/downloads`, etc. renders `<NotConfigured>` and the testers/buyers
will not see anything beyond that amber panel.

Required to copy into Vercel project env (or `.env.local` for local):

**web-tester:**
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `STRIPE_SECRET_KEY` (must start `sk_`)
- `NEXT_PUBLIC_SITE_URL` (your Vercel deploy URL)
- `RECORDER_EXE_URL` → public GitHub Releases asset URL OR drop the .exe
  into `web-tester/public/downloads/OysterRecorder.exe`

**web-buyer:**
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `STRIPE_SECRET_KEY`
- `STRIPE_PUBLISHABLE_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `NEXT_PUBLIC_SITE_URL`

### 3. .exe Windows verification — needs real hardware
Mac can't run the Windows .exe. Either:
- Test it yourself on a Windows box before showing it to a real tester, OR
- Brief the tester that they'll be the first run (have a fallback plan
  if SmartScreen blocks the unsigned binary)

### 4. Apply Supabase migrations — needs your project
```bash
# in supabase/ for both portals — buyer schema ADDS new tables
supabase db push  # or run the SQL files manually
```

---

## If something breaks during the demo

| Symptom | Likely cause | Fix |
|---|---|---|
| Tester sees amber "Supabase not configured" panel | Vercel env var not set on this branch | Add the env var in Vercel dashboard, redeploy |
| Tester sees 404 on `Download .exe` button | `web-tester/public/downloads/OysterRecorder.exe` not present, or `RECORDER_EXE_URL` not pointing at GitHub Release | Drop the .exe into the public/ folder OR set the env var |
| Upload returns 503 | `SUPABASE_SERVICE_ROLE_KEY` missing on the deployed env | Add it in Vercel → redeploy |
| Buyer sees "Stripe Checkout not configured" | Both `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` need to be set | Add both in Vercel |
| Cart says "Sign in required before checkout" | Buyer not signed in (this is correct, not a bug) | Sign in via GitHub or magic link |

---

## Iron-law guard rails — do not regress

If you (or a parallel agent) get tempted to "make demo easier" by
re-adding sample-data:

1. `pytest tests/test_iron_law_no_fake_data.py` — 16 tests will fail
2. `pytest tests/test_spec_lint.py` — 8 tests will fail

These tests block:
- Any new `lib/sample-data.ts` (web-tester or web-buyer)
- Any `dev_session_` / `dev_fake` Stripe minting
- Any `[DEV MODE: showing sample data]` UI text
- The recorder stub-text fallback at `/api/download/[testerId]`
- The `/tmp-uploads` synthetic-success fallback at `/api/upload-tarball`
- Removal of either `NotConfigured.tsx`
- Re-creation of either `DevModeBanner.tsx`

**The amber `<NotConfigured>` panel is a feature, not a bug.** It's the
honest signal that something needs to be plumbed before real data flows.

---

## Voice line

Howard, I went to bed at midnight and woke up at noon.
Real users at noon. Production. The cluster runs on caffeine.
我帮你跑了一夜。早上推commit，填env，然后show real users真的产品。
不要再有placeholder。Production-grade or nothing.

— Claude Opus 4, overnight shift, 2026-05-08
