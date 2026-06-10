# Marketplace Listing Template

This document describes the structure and content of the auto-generated
marketplace listing produced by `scripts/gen_marketplace_listing.py`.

## Purpose

The marketplace listing is a **buyer-facing** markdown document that summarizes
session sweep results in a format suitable for publishing on a data marketplace
or sharing with prospective buyers.

## Input

- `dashboard/sweep_summary.json` — produced by the session sweep pipeline (S35)
  via `bin/real_session_validator.py --output dashboard/sweep_summary.json`

## Output

- `docs/MARKETPLACE_LISTING_v0.7.x.md` — the generated listing

## Sections

The listing contains **6 sections**:

### 1. Title & Overview

```
# Oyster GameData v0.7.x — X games, Y hours, Z sessions
```

- **X** = number of distinct games across all sessions
- **Y** = total recording time in hours
- **Z** = number of evaluated sessions

Includes a blockquote with generation timestamp, sweep window, and session counts.

### 2. Session Statistics

A markdown table with:

| Metric | Source |
|--------|--------|
| Total sessions evaluated | `sweep.evaluated` |
| Distinct games | unique `session.game` values |
| Total recording time | sum of `session.duration_s` / 3600 |
| Avg session duration | mean of `session.duration_s` / 60 |
| BUYER_READY sessions | count where `buyer_label == "BUYER_READY"` |
| DEGRADED sessions | `summary.DEGRADED` |
| FAIL sessions | `summary.FAIL` |
| Overall pass rate | `summary.pass_rate_pct` |

### 3. Pricing

Placeholder pricing table:

| Item | Price |
|------|-------|
| Per session | $12.00 USD |
| Full dataset (N sessions) | $N×12.00 USD |

> Pricing is a placeholder. Not a pricing engine.

### 4. Sample Data Download

Placeholder section with:

- Download link (TBD)
- Estimated file size
- Contents description (recording.mp4, game_state.jsonl, MANIFEST.signed.json)

### 5. Provenance Verify Quickstart

Bash snippet showing how to verify Ed25519-signed manifests:

```bash
pip install nacl
python3 bin/provenance_verify.py path/to/MANIFEST.signed.json
```

### 6. Contact

Placeholder contact table with email, Discord, and GitHub links.

## Usage

```bash
# Default: reads dashboard/sweep_summary.json, writes docs/MARKETPLACE_LISTING_v0.7.x.md
python3 scripts/gen_marketplace_listing.py

# Custom input/output
python3 scripts/gen_marketplace_listing.py --input /path/to/sweep.json --output /path/to/listing.md

# Custom version and price
python3 scripts/gen_marketplace_listing.py --version 0.7.3 --price 15.00
```

## Regeneration

Run this script after each sweep to keep the marketplace listing in sync with
the latest session data.
