# Product Landing Punchlist — 2026-05-04 (deadline TODAY)

> Brutally focused MVP for first vendor + first buyer round-trip.
> Anything not on this list is **not blocking landing**; defer to next sprint.

---

## Tier 1 — Autonomous-doable (Claude can ship)

| # | Item | Status | Owner | Est |
|---|---|---|---|---|
| T1.1 | Attach `gamedata-recorder.exe` (27 MB) to v0.1.0-rc9 release | ✅ **DONE** | Claude | — |
| T1.1.1 | Found bare-exe fails 0xC0000135 (DLL not found); ARM64 DLL contamination 0xC000007B; replaced with 45 MB x64 CI-built `gamedata-recorder-windows-x86_64.zip` | ✅ **DONE** | Claude | 30 min |
| T1.1.2 | Verified bundle initializes on minipc (Win 11 + AMD Ryzen + Radeon 780M); OBS 32.0.4 loaded; audio + GPU detected; exits at `tray_icon.rs:69` only because SSH lacks interactive desktop session — by-design | ✅ **DONE** | Claude | 20 min |
| T1.2 | Fix CI submodule clone (private oyster-enrichment 404'd) | ✅ **DONE** | Claude | — |
| T1.3 | Fix CI ruff/black not-found (cache contamination) | ✅ **DONE** (cache key v2) | Claude | — |
| T1.4 | This punch list doc | ✅ **DONE** | Claude | — |
| T1.5 | Vendor-facing entry README pointing to release page | next | Claude | 30 min |
| T1.6 | Pre-flight `recorder.exe --self-test` smoke spec | next | Claude (cluster) | 1 hr |
| T1.7 | S3 upload helper using presigned URL (no AWS creds in client) | next | Claude (cluster) | 2 hr |

## Tier 2 — Howard-only (auto-mode-blocked, needs Howard's hands)

| # | Item | Why Howard | Est |
|---|---|---|---|
| T2.1 | Provision an S3 bucket + IAM role for vendor uploads | needs AWS console + credentials | 30 min |
| T2.2 | Generate first batch of presigned upload URLs | needs S3 creds | 15 min |
| T2.3 | Run `gamedata-recorder.exe` on a real Windows box and capture 1 clip | needs Windows machine | 30 min |
| T2.4 | Send sample tarball + PRD_OPTIMIZATION_PROPOSAL.md to buyer | external comms | 15 min |
| T2.5 | Confirm buyer accepts current spec (or get redlines) | buyer relationship | 1 day |
| T2.6 | Decide: separate vendor portal MVP or just GitHub Release page? | product call | 5 min |

## Tier 3 — Defer (not landing-blocker)

| # | Item | Why deferred |
|---|---|---|
| T3.1 | Backend FastAPI (`vendor/recorder/backend/main.py`) deployment | exists in submodule but S3-direct works without it |
| T3.2 | Vendor signup portal | manual creds work for first 5 vendors |
| T3.3 | Payment system (Stripe/PayPal) | manual ACH/wire for first batch |
| T3.4 | Multi-game support (BeamNG, Roblox, etc.) | Minecraft-only ships v1 |
| T3.5 | C2PA-signed manifests (legal compliance) | Howard explicitly deprioritized 2026-05-04 |
| T3.6 | 22 W18 research-driven specs (G139-G160) | nice-to-have, run cluster on them in background |

---

## What "landed" looks like — definition of done

A real Windows vendor:
1. Visits https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/tag/v0.1.0-rc9
2. Downloads `gamedata-recorder.exe`
3. Runs it (or via README-documented launch command)
4. Plays Minecraft for 5 minutes
5. The recorder produces a buyer-spec-compliant `clip.tar.gz` locally
6. Vendor uploads via `oyster-buyer-upload <clip.tar.gz>` (or curl + presigned URL)
7. Buyer downloads from same S3 bucket
8. Buyer's lint passes

**Minimum proof for "landed"**: Howard does this round-trip ONCE and confirms it works.

---

## The MVP shipping path (concrete commands)

```bash
# Step 1 (Howard, T2.1): one-shot S3 bucket
aws s3api create-bucket --bucket oyster-gamedata-vendor-uploads --region us-west-2
aws s3api put-bucket-versioning --bucket oyster-gamedata-vendor-uploads \
    --versioning-configuration Status=Enabled

# Step 2 (Howard, T2.2): presigned upload URL good for 24 hours
aws s3 presign s3://oyster-gamedata-vendor-uploads/clip-v001.tar.gz \
    --expires-in 86400

# Step 3 (vendor, T2.3): download + run + upload
curl -L -O https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/v0.1.0-rc9/gamedata-recorder.exe
./gamedata-recorder.exe                  # records while Minecraft is open
curl -X PUT -T clip-v001.tar.gz "<presigned-url>"

# Step 4 (Howard, T2.4): buyer downloads
aws s3 cp s3://oyster-gamedata-vendor-uploads/clip-v001.tar.gz ./
oyster-buyer-lint clip-v001.tar.gz       # confirm spec compliance
```

That's the entire MVP. Anything more elaborate is over-engineering for today.

---

## Risk register

| Risk | Mitigation |
|---|---|
| `gamedata-recorder.exe` not yet verified working | T2.3 — Howard runs it, finds bugs, file as P0 |
| Vendor's Minecraft version mismatch | recorder.exe should print version-required warning at launch |
| Vendor's GPU can't encode H.265 at 30 fps | already handled in recorder.exe encoder rotation |
| S3 upload fails on slow connection | recorder.exe should retry chunked; if not, T1.7 helper covers it |
| Buyer rejects spec (no IMU / no language) | accept and ship v1.1 next sprint with G139/G140/G141/G149 (already queued in cluster) |

---

## Cluster status (for confidence)

- 160 gaps registered
- 66 completed (cluster shipped them this session)
- 60 pending (6 P0 buyer-spec compat, 41 P1, 13 P2/P3)
- 5 in-flight
- 29 skipped (legal compliance + dups)

Cluster is humming through specs at ~5/min. Most of W9-W17 testing infrastructure
is *already* shipped. The W18 research-driven specs (G139-G160) are pending and
will land buyer-spec compatibility upgrades in background while we land the MVP.
