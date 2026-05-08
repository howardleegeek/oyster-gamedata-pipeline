# Oyster GameData — Tester Web Portal

Tester-facing web flow for the Oyster GameData program. Testers sign up, download a Windows recorder (`OysterRecorder.exe`) with their tester ID baked into the filename, run it while playing Minecraft, and get paid for the gameplay data they upload.

This Next.js 14 app is the **public-facing** half of the system. The other half lives in `mc-mod/`, `bin/`, and `src/` (the recorder client + ingestion pipeline).

---

## Stack

- **Next.js 14 App Router** + **TypeScript** + **Tailwind CSS**
- **Supabase** — auth (email magic link + GitHub OAuth), Postgres DB, file storage
- **Stripe Connect** — wired in as placeholder UI; live integration is a follow-up
- **Zod** — request validation on API routes
- Deployable to **Vercel** in one click

---

## Quick start (local dev, no Supabase needed)

```bash
cd web-tester
npm install
cp .env.example .env.local        # optional — app boots without it
npm run dev
```

Open http://localhost:3000. You'll see a yellow `[DEV MODE]` banner — every page renders with realistic sample data so you can iterate on UI without a database.

> **The two commands you need:** `npm install` then `npm run dev`. That's it.

---

## Quick start (with real Supabase)

### Option A — local Supabase (recommended for development)

```bash
# install the Supabase CLI once: https://supabase.com/docs/guides/cli
supabase start                         # spins up Postgres + Auth + Storage in Docker
supabase db reset                      # applies supabase/migrations/*.sql
```

`supabase start` prints your local API URL + anon key. Copy them into `.env.local`:

```bash
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOi...     # printed by `supabase start`
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...         # also printed
SUPABASE_TARBALL_BUCKET=tarballs
NEXT_PUBLIC_SITE_URL=http://localhost:3000
```

Then `npm run dev` — the DEV banner disappears and the app talks to your local stack.

### Option B — hosted Supabase project

1. Create a project at https://supabase.com.
2. In the SQL editor, run the contents of `supabase/migrations/20260507000000_init.sql`.
3. Copy the project URL + anon key + service role key from **Settings → API** into `.env.local`.
4. (Optional) Enable GitHub OAuth under **Authentication → Providers**, then add your GitHub OAuth app's Client ID + Secret. The redirect URL should be `https://<your-project>.supabase.co/auth/v1/callback`.

---

## Deploy to Vercel

```bash
# from web-tester/
npx vercel link
npx vercel --prod
```

Then in the Vercel dashboard, **Settings → Environment Variables**, paste in:

| Variable                          | Where it comes from                                      |
| --------------------------------- | -------------------------------------------------------- |
| `NEXT_PUBLIC_SUPABASE_URL`        | Supabase → Settings → API                                |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY`   | Supabase → Settings → API                                |
| `SUPABASE_SERVICE_ROLE_KEY`       | Supabase → Settings → API (service_role, **server only**) |
| `SUPABASE_TARBALL_BUCKET`         | `tarballs` (or whatever you named the bucket)            |
| `NEXT_PUBLIC_SITE_URL`            | `https://<your-deploy>.vercel.app`                       |
| `RECORDER_EXE_URL`                | URL to the .exe (e.g. a GitHub Releases asset)            |
| `RECORDER_VERSION`                | semver of the recorder build                             |
| `GAMEDATA_RATE_PER_HOUR_CENTS`    | `600` (= $6/hr)                                          |
| `GAMEDATA_MIN_PAYOUT_CENTS`       | `2000` (= $20)                                           |
| `NEXT_PUBLIC_DEV_FALLBACK`        | `false` in prod                                          |

Add `https://<your-deploy>.vercel.app/auth/callback` to the **Authentication → URL Configuration → Redirect URLs** list in Supabase.

---

## Routes

| Path                                  | Purpose                                                     |
| ------------------------------------- | ----------------------------------------------------------- |
| `/`                                   | Landing page — pitch + FAQ + sign-up CTA                    |
| `/signup`                             | Email magic link + GitHub OAuth sign-up                     |
| `/login`                              | Same flow, sign-in mode                                     |
| `/auth/callback`                      | OAuth / magic-link redirect handler (route handler)         |
| `/dashboard`                          | Tester stats — hours, earnings, recent tarballs              |
| `/download`                           | Per-tester `.exe` download page                             |
| `/payouts`                            | Pending + history; Stripe Connect link (placeholder)        |
| `/docs`                               | Onboarding, billable-time rules, payout terms, privacy      |
| `POST /api/upload-tarball`            | Recorder-client uploads — validates, stores, inserts row    |
| `GET /api/stats/[testerId]`           | Aggregated stats for a tester (auth-gated to that tester)   |
| `GET /api/download/[testerId]`        | Streams `.exe` with attribution-friendly filename           |

The `/dashboard`, `/payouts`, and `/download` routes require auth (enforced by `middleware.ts`); they redirect to `/login?next=...` when the user has no session. In DEV MODE (no Supabase configured) the middleware short-circuits and lets everything through.

---

## Database schema

See `supabase/migrations/20260507000000_init.sql`.

```
testers   (id uuid PK, email, github_handle, github_id, created_at,
           total_hours, total_earnings_cents, stripe_account_id)

tarballs  (id uuid PK, tester_id FK, uploaded_at, size_bytes, sha256 unique,
           duration_seconds, d5_verdict, d5_score, storage_path, paid)

payouts   (id uuid PK, tester_id FK, amount_cents, status,
           paid_at, stripe_payout_id, created_at)
```

Plus:
- A `recompute_tester_aggregates(uuid)` PL/pgSQL fn + triggers on `tarballs` and `payouts` keep `testers.total_hours` / `total_earnings_cents` in sync automatically.
- A `handle_new_user()` trigger on `auth.users` provisions the matching `testers` row when someone signs up.
- Row-Level Security policies so each tester can only read their own rows (the service role bypasses RLS for trusted server-side writes).
- A private storage bucket `tarballs` keyed by `<tester_id>/<sha256>.tar.gz`.

---

## Stripe Connect setup

The `/payouts` page is wired to real Stripe Connect Express. The integration ships in two halves:

1. **The Next.js app** (`web-tester/`) — onboarding, return webhook, dashboard login link, live payout history.
2. **The cron script** (`bin/payout_cron.py`) — runs daily, transfers unpaid balances to onboarded testers.

Both halves fall back to an in-process mock when `STRIPE_SECRET_KEY` is missing, so you can iterate end-to-end locally without a Stripe account. **In a production deploy you must set the real key** — see "IRON-LAW" below.

### 1. Get test-mode keys

1. Sign up at https://dashboard.stripe.com (free).
2. Go to **Developers -> API keys**: copy the **Secret key** (`sk_test_...`).
3. Go to **Connect -> Settings**: enable **Express** as an account type. Copy the **Connect client ID** (`ca_...`) — optional but useful.
4. Go to **Developers -> Webhooks**: add an endpoint pointing at `https://<your-app>/api/stripe/webhook` (TODO — not implemented yet). Copy the signing secret (`whsec_...`).

### 2. Add to `.env.local`

```bash
STRIPE_SECRET_KEY=sk_test_xxxxx        # required for live mode
STRIPE_WEBHOOK_SECRET=whsec_xxxxx      # optional today
STRIPE_CONNECT_CLIENT_ID=ca_xxxxx      # optional today
PAYOUT_CRON_SLACK_WEBHOOK=             # optional — alerts on failed transfers
```

### 3. Apply the Stripe migration

The base migration only carries `testers.stripe_account_id`. The full Connect schema (capability flags + `payouts.idempotency_key` + the `tester_unpaid_balance` view) lives in `supabase/migrations/20260507100000_stripe_connect.sql`. Apply with:

```bash
supabase db reset                                         # local
# OR paste the SQL into the Supabase dashboard SQL editor (production)
```

### 4. Run the app

```bash
npm run dev
```

Sign in, visit `/payouts`. You'll see one of three states:

| State | UI | Trigger |
|-------|----|---------|
| `none` (no `stripe_account_id`) | "Connect bank account" CTA | First visit |
| `incomplete` (has account, charges_enabled=false) | "Finish setup" CTA | Started but didn't complete onboarding |
| `ready` (charges & payouts enabled) | "Manage on Stripe" + live payout history | Onboarded |

### 5. Walk through onboarding (Stripe test mode)

Click "Connect bank account" → you'll be redirected to Stripe's hosted Express onboarding. Use these test credentials:

| Field | Value |
|-------|-------|
| Email | any email |
| Phone | any 10-digit US number |
| SSN last 4 | `0000` |
| DOB | any date (must be 18+) |
| Address | any US address |
| Bank routing | `110000000` |
| Bank account | `000123456789` |

Stripe accepts test data instantly. You'll be redirected back to `/api/stripe/connect/return`, which calls `accounts.retrieve()` to read the live capability flags, then bounces back to `/payouts` showing the green "ready" state.

### 6. Run the payout cron locally

```bash
python3 bin/payout_cron.py --verbose             # real run
python3 bin/payout_cron.py --dry-run --verbose   # log only, no transfers
```

In production, install as a daily cron:

```cron
0 3 * * *  /usr/local/bin/python3 /path/to/bin/payout_cron.py >> /tmp/payout_cron.log 2>&1
```

The cron is **idempotent**: re-running on the same day creates zero duplicate transfers (idempotency key = `payout-<tester_id>-<date>` at both the Stripe Idempotency-Key header AND the `payouts.idempotency_key` unique index).

### 7. IRON-LAW: production must use real Stripe

The DEV MODE fallback exists for local development convenience. In a deployed environment:

- `STRIPE_SECRET_KEY` MUST be set to a real `sk_test_...` (test-mode rollout) or `sk_live_...` (production).
- If it's missing in production, every Stripe call falls back to the mock, the `/payouts` page shows the "[DEV MODE]" banner, and `payout_cron.py` writes "stripe=mock" into its log header. Operators should treat this as a deployment failure and fix the env var immediately.
- The fallback is silent (no crash) by design so a single missing env var doesn't take down the entire portal — but it is loud (banner + log line) so operators notice.

### Stripe API surface used

| Endpoint | Where | Why |
|----------|-------|-----|
| `POST /v1/accounts` | `/api/stripe/connect/onboard` | Create Express account on first onboarding |
| `POST /v1/account_links` | `/api/stripe/connect/onboard` | Generate one-shot onboarding URL |
| `GET /v1/accounts/{id}` | `/api/stripe/connect/return` | Read `charges_enabled` after onboarding |
| `POST /v1/accounts/{id}/login_links` | `/api/stripe/connect/dashboard` | One-shot Express dashboard login |
| `GET /v1/payouts` (Stripe-Account header) | `/payouts` page | Show live payout history |
| `POST /v1/transfers` (Idempotency-Key header) | `bin/payout_cron.py` | Move money on payout day |

### Tests

```bash
pytest tests/test_stripe_connect.py -v
```

29 tests cover: idempotency, threshold filtering, balance arithmetic, Stripe HTTP error parsing, wire-format verification (real form-encoded payload sent to `api.stripe.com`), Slack webhook fan-out, the IRON-LAW (live-vs-mock client selection by env), and the route file contracts.

---

## How testers get paid (end-to-end)

1. Tester signs up → row in `testers`.
2. They download `OysterRecorder-<short>-<full>.exe` from `/download`.
3. They play Minecraft → recorder packages a tarball every ~30 min.
4. Recorder POSTs to `/api/upload-tarball` with the tester ID + sha256 + duration.
5. Server stores the tarball in Supabase Storage and inserts a `tarballs` row (`d5_verdict='pending'`).
6. Out-of-band: the D5 quality model reviews the tarball and updates `d5_verdict` to `accepted` or `rejected`.
7. Triggers recompute `testers.total_hours` from accepted tarballs.
8. Weekly job creates `payouts` rows for each tester whose accumulated earnings ≥ `GAMEDATA_MIN_PAYOUT_CENTS`.
9. Stripe Connect transfer is initiated (TODO — see above).
10. Webhook flips `payouts.status` → `paid` and the dashboard reflects it.

---

## File layout

```
web-tester/
├── app/
│   ├── layout.tsx, page.tsx, globals.css, not-found.tsx
│   ├── login/page.tsx, signup/page.tsx
│   ├── auth/callback/route.ts
│   ├── dashboard/page.tsx, payouts/page.tsx, download/page.tsx, docs/page.tsx
│   └── api/
│       ├── upload-tarball/route.ts
│       ├── stats/[testerId]/route.ts
│       └── download/[testerId]/route.ts
│       ├── download/[testerId]/route.ts
│       └── stripe/connect/{onboard,return,dashboard}/route.ts
├── components/        — SiteHeader, DevModeBanner, StatCard, StripeConnectButton
├── lib/               — env, supabase clients, formatters, sample-data, stripe
├── types/database.ts  — row + view interfaces
├── supabase/
│   ├── config.toml
│   └── migrations/
│       ├── 20260507000000_init.sql
│       └── 20260507100000_stripe_connect.sql
├── public/downloads/  — drop OysterRecorder.exe here
├── middleware.ts
├── next.config.mjs, tailwind.config.ts, postcss.config.mjs, tsconfig.json
├── package.json, .env.example, .gitignore, .eslintrc.json
└── README.md
```

The Python cron lives one level up:

```
bin/payout_cron.py   — daily Stripe transfers; idempotent re-runs
```

---

## What still needs doing

- [ ] Build OysterRecorder.exe and either drop it into `public/downloads/` or set `RECORDER_EXE_URL` to a hosted asset.
- [ ] Add `app/api/stripe/webhook/route.ts` for `payout.paid` / `payout.failed` to update `payouts.status` in real time (cron currently writes status=paid optimistically and trusts Stripe's idempotency).
- [ ] D5 quality model worker that flips `d5_verdict` from `pending` to `accepted`/`rejected`.
- [ ] Real GitHub OAuth app + Supabase configuration.
- [ ] Email templates (magic link, payout confirmation, dispute resolution).

The web app itself is production-shaped today — these are downstream integrations.
