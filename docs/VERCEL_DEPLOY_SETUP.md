# Vercel Auto-Deploy Setup — web-tester & web-buyer

This repo ships two GitHub Actions workflows that auto-deploy the two Next.js
portals to Vercel on every push to `main` that touches their respective
directories:

| Workflow | Triggers when files in… | Deploys to Vercel project |
|---|---|---|
| `.github/workflows/deploy-web-tester.yml` | `web-tester/**` | tester portal (e.g. `oyster-tester.vercel.app`) |
| `.github/workflows/deploy-web-buyer.yml`  | `web-buyer/**`  | buyer portal  (e.g. `oyster-buyer.vercel.app`)  |

A push that only touches docs / Python / the recorder will deploy **neither**.
A push that only touches `web-tester/` will deploy **only** the tester portal.

This document walks through the one-time setup so the workflows can actually
hit Vercel.

---

## 0. Prerequisites

* GitHub repo: `howardleegeek/oyster-gamedata-pipeline` (admin access required to
  add secrets).
* Vercel account with the [Hobby](https://vercel.com/pricing) tier or higher.
* Supabase project (or two) — same project can back both portals if they share
  schema, or separate projects for stricter isolation.

---

## 1. Create one Vercel project per portal

You need two distinct Vercel projects: one per portal. Each has its own URL,
its own environment variables, its own deploy history.

### 1a. Tester portal

1. Visit <https://vercel.com/new>.
2. Click **Import Git Repository** and pick `howardleegeek/oyster-gamedata-pipeline`.
3. On the configuration screen:
   * **Project Name:** `oyster-web-tester` (or any unique slug — this becomes
     the `*.vercel.app` URL).
   * **Framework Preset:** Next.js (auto-detected).
   * **Root Directory:** click **Edit** and set it to `web-tester`. This is
     critical — without it, Vercel runs `npm install` at the repo root and
     bails because there's no Next app there.
   * **Build & Output Settings:** leave on defaults (`next build`, `.next`).
   * **Install Command:** leave on default (`npm install`).
4. Skip env vars on this screen — we add them in step 4 below.
5. Click **Deploy**. The first deploy will use placeholder env (no Supabase),
   so the page renders the `[DEV MODE]` banner. That's fine — proves the
   pipeline works end-to-end.
6. Once the deploy lands, copy the production URL it shows you
   (e.g. `https://oyster-web-tester.vercel.app`). Save it.

### 1b. Buyer portal

Repeat 1a but:
* **Project Name:** `oyster-web-buyer`.
* **Root Directory:** `web-buyer`.

If `web-buyer/` does not exist yet, create the directory with a minimal
Next 14 app first (mirror `web-tester/` structure), or skip step 1b until
you have content there. The workflow itself will not fire on `main` until
files appear under `web-buyer/`.

---

## 2. Get `VERCEL_TOKEN`, `VERCEL_ORG_ID`, and the two `VERCEL_PROJECT_ID`s

### 2a. `VERCEL_TOKEN`

A personal access token used by the Vercel CLI from inside GitHub Actions.

1. Go to <https://vercel.com/account/tokens>.
2. Click **Create Token**.
   * **Token Name:** `oyster-gamedata-ci` (or similar — for your own audit).
   * **Scope:** the team / personal account that owns the projects from step 1.
   * **Expiration:** pick whatever your security policy says. 90 days is the
     common default; 1 year is fine if you'll rotate proactively.
3. Copy the token value once (Vercel will not show it again).

### 2b. `VERCEL_ORG_ID`

The internal id of the Vercel team / personal account.

* Go to <https://vercel.com/account> (personal) or your team's settings page.
* Look for **General → Your ID** (personal) or **Team ID** (teams). Copy it.
* Or: with the CLI, `npx vercel whoami` prints it after you log in.

### 2c. `VERCEL_PROJECT_ID_TESTER` and `VERCEL_PROJECT_ID_BUYER`

The internal id for each project.

* Open the project in the Vercel dashboard.
* **Settings → General → Project ID.** Copy it.
* Do this for both `oyster-web-tester` and `oyster-web-buyer`.

> Tip: you can also get all four ids by running
> `npx vercel link` inside `web-tester/` (and again inside `web-buyer/`)
> after `npx vercel login`. The CLI writes them to a local `.vercel/`
> directory that is `.gitignore`d — read the file and copy the values.
> Don't commit `.vercel/`.

---

## 3. Add secrets to the GitHub repo

The workflows read these secrets. Add them at:

  <https://github.com/howardleegeek/oyster-gamedata-pipeline/settings/secrets/actions>

### Required (Vercel CLI path — preferred)

| Secret name | Value |
|---|---|
| `VERCEL_TOKEN` | the token from step 2a |
| `VERCEL_ORG_ID` | the org id from step 2b |
| `VERCEL_PROJECT_ID_TESTER` | the tester project id from step 2c |
| `VERCEL_PROJECT_ID_BUYER` | the buyer project id from step 2c |

### Optional (deploy-hook fallback)

If you'd rather **not** ship a CLI token (smaller blast radius, but loses the
exact deploy URL), you can use Vercel's deploy hooks instead.

1. In each Vercel project: **Settings → Git → Deploy Hooks → Create Hook**.
   * Name: `github-actions-prod`.
   * Branch: `main`.
2. Copy the resulting URL (it includes a one-way secret token).
3. Add to GitHub:

| Secret name | Value |
|---|---|
| `VERCEL_TESTER_DEPLOY_HOOK` | the tester deploy-hook URL |
| `VERCEL_BUYER_DEPLOY_HOOK` | the buyer deploy-hook URL |
| `VERCEL_TESTER_PROD_URL` | (optional) `https://oyster-web-tester.vercel.app` so the workflow can post the URL on the commit |
| `VERCEL_BUYER_PROD_URL` | (optional) `https://oyster-web-buyer.vercel.app` |

The workflow auto-detects which path you've configured: it prefers the CLI if
all three CLI secrets are present, otherwise falls back to the deploy hook.
If neither is set, the workflow fails fast with an actionable error.

---

## 4. Connect to Supabase (env vars in Vercel)

Each Vercel project needs its own copy of the env vars. Do this **per project**
(`oyster-web-tester` and `oyster-web-buyer`) at:

  <project> → **Settings → Environment Variables**

Set the **Production** scope (and **Preview** if you want PR previews to work).
Source values come from your Supabase dashboard at <https://supabase.com/dashboard>.

### Variables every Next portal needs

| Key | Where to get it | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase → Project Settings → API → **Project URL** | safe to ship to browser |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase → Project Settings → API → **anon / public** key | safe to ship to browser |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API → **service_role** key | **server-only** — do not prefix with `NEXT_PUBLIC_` |
| `NEXT_PUBLIC_SITE_URL` | the canonical Vercel URL (`https://oyster-web-tester.vercel.app`) | drives magic-link redirects, OAuth callbacks |
| `NEXT_PUBLIC_DEV_FALLBACK` | `false` once secrets are real | leave as `true` until Supabase is wired so the app keeps booting |

Tester portal also needs (see `web-tester/.env.example` for the full list):

* `SUPABASE_TARBALL_BUCKET=tarballs` (or your bucket name).
* `GAMEDATA_RATE_PER_HOUR_CENTS=600` and `GAMEDATA_MIN_PAYOUT_CENTS=2000`
  (or your real rates).
* `RECORDER_EXE_URL` and `RECORDER_VERSION`.

> **Iron law: never paste the `service_role` key into a `NEXT_PUBLIC_*`
> variable.** The `NEXT_PUBLIC_` prefix bakes it into the client bundle and
> ships it to every browser visiting your site.

After saving env vars, **redeploy** so they take effect. Either:
* push a no-op commit that touches the right portal's directory, or
* in Vercel: **Deployments → ⋯ → Redeploy** on the latest deploy.

---

## 5. First deploy + confirm production URL

Once secrets are wired (step 3) and Vercel env vars are set (step 4):

1. Make a commit that touches the portal you want to test.
   * Tester: edit something inside `web-tester/`, e.g. tweak a copy string.
   * Buyer:  edit something inside `web-buyer/`.
2. Push to `main`.
3. Watch the workflow:
   <https://github.com/howardleegeek/oyster-gamedata-pipeline/actions>.
   * The matching workflow (`Deploy Web Tester` or `Deploy Web Buyer`)
     should fire; the other should stay silent.
   * The deploy step prints the canonical `*.vercel.app` URL.
4. Confirm:
   * Open the URL in a browser → you should see the portal (no `[DEV MODE]`
     banner if env vars are real).
   * Hit `<URL>/api/health` (if the portal exposes a health route) and
     confirm 200.
5. The workflow also posts a comment on the commit with the deploy URL.
   Find it at <https://github.com/howardleegeek/oyster-gamedata-pipeline/commits/main>.

### What "production URL" means here

* Vercel always assigns the project a canonical alias —
  `<project-slug>.vercel.app` — that points at the latest production deploy.
* Each individual deploy also gets an immutable URL like
  `<project-slug>-<hash>.vercel.app` (this is what the CLI prints, and what
  the commit comment links to).
* If you want a custom domain (`tester.oysterworld.com`), wire it under
  **Settings → Domains** in the Vercel project. The workflow doesn't change
  what URL Vercel surfaces — it just deploys; Vercel routes.

---

## 6. Troubleshooting

### Workflow never fires on push

* You probably touched files outside `web-tester/` (or `web-buyer/`). The
  path filter is intentionally strict.
* To force a deploy, use **Actions → Deploy Web Tester → Run workflow**
  (`workflow_dispatch`) on the GitHub UI.

### Workflow fails with "No deploy credentials"

* Neither the CLI secrets nor the deploy-hook secret is set. Re-do step 3.

### Workflow fails at `npm run build`

* Build error in the Next app — fix locally first (`cd web-tester && npm run build`).
* If the build references env vars that aren't set, add them to Vercel
  (step 4) **and** to the workflow's `Build` step `env:` block if the
  variable is needed at build time (most aren't — Next only needs
  `NEXT_PUBLIC_*` vars at build for inlining).

### Wrong Vercel project gets deployed

* Make sure `VERCEL_PROJECT_ID_TESTER` is the tester project id (not the buyer
  one) and vice versa. The contract test
  `tests/test_web_workflows.py::test_workflows_target_distinct_projects`
  asserts the workflows reference the right secret names; double-check the
  *values* match in GitHub secrets settings.

### Deploy returned a URL but the site 500s

* Almost always missing or wrong env vars on Vercel. Check:
  Vercel → project → **Logs** → click the failing request to see the runtime
  error. `SUPABASE_SERVICE_ROLE_KEY` missing is the usual culprit.

---

## 7. Rotating secrets

* Vercel tokens: rotate at least every 90 days. Recreate at
  <https://vercel.com/account/tokens>, update `VERCEL_TOKEN` in GitHub
  secrets, delete the old token in Vercel.
* Deploy hooks: rotate by deleting the hook in Vercel and creating a new one,
  then update the corresponding GitHub secret.
* Supabase service-role key: rotate via Supabase dashboard → **Settings → API
  → Reset service_role key**. Update Vercel env vars in *both* portals (if
  they share the project) and trigger a redeploy.

The workflows themselves carry no long-lived state, so rotating a secret
takes effect on the next push (or `workflow_dispatch`) — no rebuild required.
