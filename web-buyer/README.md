# Oyster GameData — Buyer Web Portal

Buyer-facing marketplace for the Oyster GameData program. AI/ML companies and academic researchers browse a catalog of real Minecraft gameplay tarballs, sample data and previews for free, then purchase per-tarball licenses through Stripe Checkout. Signed download URLs (24h TTL) deliver the .tar.gz blobs.

This Next.js 14 app is the **buyer side** of the marketplace. Its sibling `web-tester/` (one directory up) is where testers sign up, run the recorder, and earn per uploaded tarball.

---

## Stack

- **Next.js 14 App Router** + **TypeScript** + **Tailwind CSS**
- **Supabase** — auth (email magic link + GitHub OAuth), Postgres, signed Storage URLs
- **Stripe Checkout** — fully wired for live keys; falls through to a fake "DEV mode" session when no Stripe keys are set
- **Zod** — request validation on every API route
- Deployable to **Vercel** in one click

---

## Quick start (local dev, no Supabase, no Stripe)

```bash
cd web-buyer
npm install
cp .env.example .env.local        # optional — app boots without it
npm run dev
```

Open http://localhost:3001. You'll see a yellow `[DEV MODE]` banner and the catalog will be populated with **5 sample tarballs** spanning mining, building, redstone, exploration, and PvP. Every page renders with realistic sample data so you can iterate on UI without any cloud setup.

> **The two commands you need:** `npm install` then `npm run dev`. That's it. The buyer portal binds to **port 3001** so you can run it side-by-side with `web-tester` (port 3000).

---

## Quick start (with Supabase, no Stripe yet)

The app uses two extra tables on top of the tester schema (`buyers`, `purchases`, `licenses`, `cart_items`, plus a `catalog_metadata` curation layer). For development, point the buyer portal at the **same** Supabase project as the tester portal — the buyer migration is purely additive.

### Option A — local Supabase (shared with web-tester)

```bash
# from web-tester/
supabase start
supabase db reset

# from web-buyer/
supabase db push   # applies supabase/migrations/20260507000000_buyer_init.sql
```

`supabase start` already printed the API URL + anon key + service role key. Copy them into `web-buyer/.env.local`:

```bash
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOi...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...
SUPABASE_TARBALL_BUCKET=tarballs
NEXT_PUBLIC_SITE_URL=http://localhost:3001
```

Run `npm run dev` and the DEV banner disappears. The `/browse` page now reads `tarballs` rows your tester tests have uploaded; if no tarballs have been uploaded yet, the catalog is simply empty (the seed query at the bottom of the migration only seeds metadata for tarballs that already exist).

### Option B — separate Supabase project for production

In production we recommend **separate** Supabase projects for testers and buyers (data isolation). Make sure the `tarballs` table shape matches the tester portal's migration before applying the buyer migration.

---

## Quick start (with Stripe Checkout)

The buyer portal supports two Stripe modes:

| Mode | Trigger | Behaviour |
| ---- | ------- | --------- |
| `dev_fake` | `STRIPE_SECRET_KEY` not set | `/api/checkout` mints a `dev_session_<uuid>` and writes `purchases` + `licenses` directly. The user lands on `/downloads?session_id=dev_session_…` exactly as they would after a real Stripe redirect. |
| `live`     | `STRIPE_SECRET_KEY` + `STRIPE_PUBLISHABLE_KEY` set | `/api/checkout` calls `stripe.checkout.sessions.create()`, returns the hosted URL. Stripe POSTs `checkout.session.completed` to `/api/checkout/webhook`, which writes the rows. |

To wire live Stripe locally:

```bash
# 1. Sign up at https://dashboard.stripe.com — test mode is fine.
# 2. Copy your test keys:
STRIPE_SECRET_KEY=sk_test_xxx
STRIPE_PUBLISHABLE_KEY=pk_test_xxx

# 3. Forward webhooks to your dev server:
stripe listen --forward-to http://localhost:3001/api/checkout/webhook
# → prints "Ready! Your webhook signing secret is whsec_xxxxxxxxxxxx"
STRIPE_WEBHOOK_SECRET=whsec_xxx
```

Restart `npm run dev` and the checkout flow now redirects to a real Stripe-hosted page. Use Stripe's test card `4242 4242 4242 4242` (any expiry, any CVC).

---

## Deploy to Vercel

```bash
# from web-buyer/
npx vercel link
npx vercel --prod
```

Then in **Settings → Environment Variables**, paste in:

| Variable                          | Where it comes from                                                |
| --------------------------------- | ------------------------------------------------------------------ |
| `NEXT_PUBLIC_SUPABASE_URL`        | Supabase → Settings → API                                          |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY`   | Supabase → Settings → API                                          |
| `SUPABASE_SERVICE_ROLE_KEY`       | Supabase → Settings → API (`service_role`, **server only**)        |
| `SUPABASE_TARBALL_BUCKET`         | `tarballs`                                                          |
| `NEXT_PUBLIC_SITE_URL`            | `https://<your-deploy>.vercel.app`                                  |
| `STRIPE_SECRET_KEY`               | Stripe → Developers → API keys (live or test)                      |
| `STRIPE_PUBLISHABLE_KEY`          | same                                                                |
| `STRIPE_WEBHOOK_SECRET`           | Stripe → Webhooks → add endpoint pointing at `/api/checkout/webhook`, copy the signing secret |
| `GAMEDATA_PRICE_PER_GB_CENTS`     | `2500` (= $25 / GB)                                                 |
| `GAMEDATA_RESEARCH_DISCOUNT_PCT`  | `40`                                                                |
| `DOWNLOAD_LINK_TTL_SECONDS`       | `86400`                                                             |
| `NEXT_PUBLIC_DEV_FALLBACK`        | `false` in prod                                                     |

Add `https://<your-deploy>.vercel.app/auth/callback` to the **Authentication → URL Configuration → Redirect URLs** list in Supabase.

In the Stripe dashboard, point the webhook at `https://<your-deploy>.vercel.app/api/checkout/webhook` and subscribe to `checkout.session.completed`.

---

## Routes

| Path                                    | Purpose                                                          |
| --------------------------------------- | ---------------------------------------------------------------- |
| `/`                                     | Landing — pitch + featured tarballs + FAQ + CTA                  |
| `/signup`                               | Email magic link + GitHub OAuth sign-up (collects company name)  |
| `/login`                                | Same flow, sign-in mode                                          |
| `/auth/callback`                        | OAuth / magic-link redirect handler; merges anon-cart cookie     |
| `/browse`                               | Catalog with verdict / task-type / size / price filters          |
| `/tarball/[id]`                         | Detail view + 30s preview + first-100 records of action_camera.json |
| `/cart`                                 | Cart contents, license-type picker, "proceed" button             |
| `/checkout`                             | Pre-Checkout summary (commercial + research price)               |
| `/downloads`                            | Per-purchase signed S3 URLs (24h)                                |
| `/licenses`                             | Full license terms + per-purchase certificates                   |
| `GET /api/catalog`                      | List tarballs with filters                                        |
| `GET /api/tarball/[id]/preview`         | First 100 records of action_camera.json + poster URL              |
| `POST /api/cart/add`                    | Add a tarball to the cart (DB or cookie)                         |
| `POST /api/cart/remove`                 | Remove (form-redirect or JSON)                                    |
| `POST /api/checkout`                    | Create Stripe Checkout session (or dev fake session)              |
| `POST /api/checkout/webhook`            | Stripe webhook → writes `purchases` + `licenses`                  |
| `GET /api/downloads/[purchaseId]`       | 302 to signed tarball URL (or `?license_only=1` for cert)        |

The `/downloads` and `/checkout` routes require auth (enforced by `middleware.ts`); they redirect to `/login?next=...` when the user has no session. In DEV MODE the middleware short-circuits and lets everything through. The Stripe webhook is excluded from the middleware matcher because it ships with its own signature verification.

---

## Database schema

See `supabase/migrations/20260507000000_buyer_init.sql`.

```
buyers          (id uuid PK = auth.users.id, email, github_handle, github_id,
                 company_name, created_at, total_spent_cents)

catalog_metadata(tarball_id PK FK -> tarballs, title, description,
                 mc_task_type, price_cents, poster_url, video_preview_url,
                 curated_at)

purchases       (id uuid PK, buyer_id FK, tarball_id FK,
                 amount_cents, stripe_session_id, purchased_at, license_id)

licenses        (id uuid PK, purchase_id FK, type, terms_url, issued_at)

cart_items      (id uuid PK, buyer_id FK, tarball_id FK, added_at)
```

Plus:
- `recompute_buyer_aggregates(uuid)` keeps `buyers.total_spent_cents` in sync after every purchase change.
- `licenses_after_insert` populates `purchases.license_id` automatically.
- `handle_new_buyer()` provisions a `buyers` row on `auth.users` insert (fires alongside the tester portal's `handle_new_user` if both are installed in the same project).
- Row-Level Security so each buyer can only read their own purchases / licenses / cart. `catalog_metadata` is publicly readable.
- `(buyer_id, tarball_id, stripe_session_id)` is unique, making the Stripe webhook idempotent against retries.

---

## DEV MODE — what's faked vs. what's real

| Concern               | DEV MODE (no env)                              | LIVE MODE (env set)                       |
| --------------------- | ---------------------------------------------- | ----------------------------------------- |
| Supabase auth         | Bypassed; pretend buyer = `sample-buyer@…`     | Real magic-link + GitHub OAuth            |
| Catalog               | 5 hand-tuned tarballs in `lib/sample-data.ts`  | `tarballs` JOIN `catalog_metadata`        |
| Cart                  | Cookie (`oyster_cart`)                         | DB (`cart_items`); cookie merged on login |
| Stripe Checkout       | Fake session id, redirect to `/downloads`      | Real Checkout URL                         |
| Webhook               | n/a — purchase rows are written by `/api/checkout` directly | Stripe `checkout.session.completed` writes purchases + licenses |
| Signed download URL   | Returns a stub text file                       | Supabase Storage signed URL (24h TTL)     |
| Sample preview JSON   | Deterministic synthetic records (seeded by tarball id) | Same shape — future iteration replaces with materialized previews |

The DEV path is a **graceful fallback**, not a placeholder. Every page renders a real React tree with real data shapes; only the data source flips between Supabase ↔ the sample-data module. This is the iron-law your spec calls out — production-grade scaffold with no stub pages.

---

## End-to-end flow (LIVE)

1. Tester uploads a tarball via the tester portal → `tarballs` row.
2. (out of band) Curator adds a `catalog_metadata` row with title, description, task type, price.
3. Buyer signs up at `/signup`, browses `/browse`, adds 1+ tarballs to cart.
4. Buyer clicks **Proceed to checkout** → `/api/checkout` creates Stripe Checkout session.
5. Buyer pays on Stripe → Stripe POSTs `checkout.session.completed` to `/api/checkout/webhook`.
6. Webhook writes `purchases` + `licenses`, clears the buyer's cart.
7. Buyer lands on `/downloads?session_id=cs_test_…` → list of signed tarball URLs (24h TTL).
8. Buyer clicks **Download .tar.gz** → 302 to Supabase-signed Storage URL → starts download.
9. Buyer can re-issue links from `/downloads` indefinitely after purchase.

---

## File layout

```
web-buyer/
├── app/
│   ├── layout.tsx, page.tsx, globals.css, not-found.tsx
│   ├── login/page.tsx, signup/page.tsx
│   ├── auth/callback/route.ts
│   ├── browse/page.tsx
│   ├── tarball/[id]/page.tsx
│   ├── cart/page.tsx + CheckoutButton.tsx (client)
│   ├── checkout/page.tsx
│   ├── downloads/page.tsx
│   ├── licenses/page.tsx
│   └── api/
│       ├── catalog/route.ts
│       ├── tarball/[id]/preview/route.ts
│       ├── cart/add/route.ts
│       ├── cart/remove/route.ts
│       ├── checkout/route.ts
│       ├── checkout/webhook/route.ts
│       └── downloads/[purchaseId]/route.ts
├── components/   — SiteHeader, DevModeBanner, StatCard, TarballCard,
│                   CatalogFilters, AddToCartButton
├── lib/          — env, format, supabase clients, stripe, catalog,
│                   sample-data, cart-cookie
├── types/database.ts
├── supabase/
│   ├── config.toml
│   └── migrations/20260507000000_buyer_init.sql
├── public/
│   ├── samples/poster-{1..5}.svg, poster-default.svg
│   └── robots.txt
├── middleware.ts
├── next.config.mjs, tailwind.config.ts, postcss.config.mjs, tsconfig.json
├── package.json, .env.example, .gitignore, .eslintrc.json
└── README.md
```

---

## What still needs doing (downstream integrations)

- [ ] Curation admin UI to write `catalog_metadata` for newly uploaded tarballs (today curators use `psql`).
- [ ] Materialized `action_camera_preview` blobs at ingest time so `/api/tarball/[id]/preview` reads from Storage instead of synthesizing.
- [ ] Real video posters / 30-second preview clips (today `/samples/poster-*.svg` are placeholders).
- [ ] Stripe Tax / VAT collection in Checkout — `automatic_tax: { enabled: true }` once we have a Stripe Tax account configured.
- [ ] Email receipts (Stripe sends one for the payment; we should send our own with the signed download links + license cert PDF attached).
- [ ] Academic-affiliation verification step before unlocking the research discount (today: honour system).
- [ ] Bulk-purchase / volume-discount tiers ($X off when buying ≥ N GB at once).

The marketplace itself is production-shaped today — these are downstream content / billing / fulfilment polish.
