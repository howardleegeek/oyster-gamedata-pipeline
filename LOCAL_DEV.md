# Local Development Guide

Run the **web-tester** (`:3000`) and **web-buyer** (`:3001`) portals locally with a single shared Supabase stack.

---

## Prerequisites

| Tool | Minimum | Install |
|------|---------|---------|
| Node.js | 18+ | `brew install node` or [nodejs.org](https://nodejs.org) |
| npm | (ships with Node) | — |
| Docker Desktop | running | [docker.com](https://www.docker.com/products/docker-desktop) |
| Supabase CLI | latest | `brew install supabase/tap/supabase` |

> **Docker must be running** before you start. `docker info` should print without errors.

---

## Bootstrap (one command)

```bash
chmod +x bootstrap-local.sh
./bootstrap-local.sh
```

This script:
1. Verifies prerequisites (node, npm, supabase, docker).
2. Starts a local Supabase stack (API `:54321`, DB `:54322`, Studio `:54323`).
3. Applies **both** tester and buyer migrations to the same database (tables don't overlap).
4. Parses `supabase status` to extract real keys.
5. Generates `web-tester/.env.local` and `web-buyer/.env.local` with the same URL + keys.
6. Runs `npm install` in both portals.

---

## Start the portals (two terminals)

**Terminal 1 — Tester portal**
```bash
cd web-tester
npm run dev          # → http://localhost:3000
```

**Terminal 2 — Buyer portal**
```bash
cd web-buyer
npm run dev          # → http://localhost:3001
```

---

## Smoke test

With both portals running:

```bash
chmod +x local-smoke.sh
./local-smoke.sh
```

Expected output (all green):
```
[200] tester portal  :3000
[200] buyer portal   :3001
[200] buyer /api/catalog  (valid JSON)
[200] supabase REST  :54321
```

Exit code `0` = all probes passed, `1` = at least one failed.

---

## First walkthrough

1. Open http://localhost:3000 — you should see the **"Real Minecraft gameplay data..."** landing page.
2. Click **Sign up** (email magic link — Supabase Inbucket at http://localhost:54324 catches the email).
3. Confirm the magic link → redirected to `/dashboard` showing **"No uploads yet"**.
4. Open http://localhost:3001 — you should see the **"Real Minecraft gameplay data for AI training"** buyer landing.
5. Supabase Studio at http://localhost:54323 lets you inspect tables directly.

---

## Troubleshooting

### Port conflict (54321, 3000, or 3001 already in use)

```bash
lsof -i :54321    # find the process
kill <PID>         # free the port
```

Or stop any existing Supabase stack: `cd web-tester && supabase stop`.

### Docker not running

```
[FAIL] Docker daemon is not running. Start Docker Desktop first.
```

Open Docker Desktop and wait for it to fully start, then re-run `./bootstrap-local.sh`.

### Supabase CLI not installed

```
[FAIL] supabase CLI not found.
```

Install it:
```bash
brew install supabase/tap/supabase
```

### psql not found (buyer migrations)

The bootstrap script uses `psql` to apply buyer migrations to the shared DB. If `psql` is not installed:
```bash
brew install libpq && brew link --force libpq
```

### npm install fails

Delete lock files and retry:
```bash
cd web-tester && rm -rf node_modules package-lock.json && npm install
cd web-buyer  && rm -rf node_modules package-lock.json && npm install
```
