# Morning Paste Block — copy-paste ready

**For Howard, 2026-05-08 morning. Each box → one paste.**

---

## 1. Push (DONE — `a4a3367` is now on origin/main)

```bash
# Already pushed. Confirm CI:
gh run list --limit 5 --branch main
gh run watch  # or open https://github.com/howardleegeek/oyster-gamedata-pipeline/actions
```

---

## 2. Vercel env vars — paste these into both projects

Open https://vercel.com/dashboard, find both projects, **Settings → Environment Variables → Production**:

> **Howard 2026-05-08 — Stripe deferred for tomorrow's production launch.** Two
> blocks per project: REQUIRED-FOR-LAUNCH (paste these) and
> POST-LAUNCH (paste these once Stripe is wired).

### web-tester project — REQUIRED FOR LAUNCH
```
NEXT_PUBLIC_SUPABASE_URL=https://YOUR-TESTER-PROJECT.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOi...   # from Supabase dashboard → Project Settings → API
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...        # SAME page, "service_role" — server-only
SUPABASE_TARBALL_BUCKET=tarballs
NEXT_PUBLIC_SITE_URL=https://YOUR-TESTER-DEPLOY.vercel.app
GAMEDATA_RATE_PER_HOUR_CENTS=600
GAMEDATA_MIN_PAYOUT_CENTS=2000
RECORDER_VERSION=0.26.0
# RECORDER_EXE_URL — already defaulted in lib/env.ts to v0.26.0 release; leave unset
```

### web-tester project — LATER (after launch)
```
# Wire Stripe to unlock /payouts. Until then it shows <NotConfigured>.
STRIPE_SECRET_KEY=sk_test_...                  # https://dashboard.stripe.com/test/apikeys
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_CONNECT_CLIENT_ID=ca_...                # https://dashboard.stripe.com/test/connect/applications
```

### web-buyer project — REQUIRED FOR LAUNCH
```
NEXT_PUBLIC_SUPABASE_URL=https://YOUR-BUYER-PROJECT.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOi...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...
SUPABASE_TARBALL_BUCKET=tarballs
NEXT_PUBLIC_SITE_URL=https://YOUR-BUYER-DEPLOY.vercel.app
GAMEDATA_PRICE_PER_GB_CENTS=2500
GAMEDATA_RESEARCH_DISCOUNT_PCT=40
DOWNLOAD_LINK_TTL_SECONDS=86400
```

### web-buyer project — LATER (after launch)
```
# Wire Stripe Checkout to unlock /cart, /checkout, /downloads.
# Until then those pages show <NotConfigured>; buyer launch stops at
# /tarball/[id] which is the "early access catalog" pitch.
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_CHECKOUT_SUCCESS_PATH=/downloads?session_id={CHECKOUT_SESSION_ID}
STRIPE_CHECKOUT_CANCEL_PATH=/cart
```

After setting REQUIRED block → **Redeploy** both projects.

---

## 3. .exe — DONE in code

I just defaulted `RECORDER_EXE_URL` in `web-tester/lib/env.ts` to the
public v0.26.0 GitHub Release asset. The download button works out of
the box on a fresh deploy without you setting anything in Vercel for
this var. (You can still override in Vercel if you want a different
build per environment.)

Asset URL (407 MB): https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/recorder-v0.26.0-real-game-state/OysterRecorder.exe

---

## 4. Supabase migrations — paste into SQL editor

Open https://app.supabase.com → your project → SQL Editor → New query.

### Tester portal project (REQUIRED for launch):
1. `web-tester/supabase/migrations/20260507000000_init.sql`

### Tester portal project (RUN ANYWAY — adds the Stripe Connect columns; harmless without Stripe configured, ready for next week):
2. `web-tester/supabase/migrations/20260507100000_stripe_connect.sql`

### Buyer portal project (REQUIRED for launch):
3. `web-buyer/supabase/migrations/20260507000000_buyer_init.sql`

OR use the CLI if you have it linked:
```bash
cd web-tester && supabase db push --project-ref YOUR-TESTER-REF
cd ../web-buyer && supabase db push --project-ref YOUR-BUYER-REF
```

---

## 5. Windows .exe verification

Mac can't run it. Two options:

**Option A: Test it yourself first**
- Get a Windows box (or VM)
- Download from the GitHub Release URL above
- SmartScreen will block (unsigned binary) → "More info → Run anyway"
- Verify it auto-detects Minecraft + Fabric mod, records ~30s, produces a tarball

**Option B: First tester is the proof**
- Brief the tester that they're the first run — be in the room
- Have a side-by-side Discord/Zoom share so you can see SmartScreen if it appears
- If anything weird happens, fall back to a screen-share launch of `/dashboard` reading existing real tarball uploads from the cluster

**Iron-law sanity:** if you see `# OysterRecorder placeholder for tester` in any downloaded file's contents, the deploy is running an OLD commit pre-`5ae84a3`. Force redeploy.

---

## Quick smoke test (do this AFTER vercel + supabase done, BEFORE real tester)

```bash
# Replace with your actual deploy URLs
TESTER=https://your-tester.vercel.app
BUYER=https://your-buyer.vercel.app

# 1. tester /dashboard renders without NotConfigured?
curl -s "$TESTER/dashboard" | grep -q "Supabase not configured" && echo "❌ tester supabase missing" || echo "✅ tester supabase OK"

# 2. buyer /browse renders without NotConfigured?
curl -s "$BUYER/browse" | grep -q "Supabase not configured" && echo "❌ buyer supabase missing" || echo "✅ buyer supabase OK"

# 3. /api/download redirects to GitHub Release?
curl -sI "$TESTER/api/download/00000000-0000-0000-0000-000000000001" | grep -i "location:.*github.com" && echo "✅ exe URL wired" || echo "❌ exe URL missing"

# 4. Iron-law: no fabricated tarball titles in production?
curl -s "$BUYER/browse" | grep -q "Diamond mine speedrun — caves" && echo "❌ FABRICATED DATA LIVE!" || echo "✅ no fabrication"
```

All 4 must say ✅ before a real human touches it.
