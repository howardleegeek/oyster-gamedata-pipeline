---
task_id: S113-marketplace-listing-gen
priority: 2
estimated_minutes: 30
modifies:
  - scripts/gen_marketplace_listing.py
  - docs/MARKETPLACE_LISTING_TEMPLATE.md
  - tests/test_gen_marketplace_listing.py
executor: qwen3.6-plus
---

## 目标

`scripts/gen_marketplace_listing.py` — generate buyer-facing marketplace listing from session sweep results.

input: `dashboard/sweep_summary.json` (from S35)
output: `docs/MARKETPLACE_LISTING_v0.7.x.md` with:
- Title: "Oyster GameData v0.7.x — X games, Y hours, Z sessions"
- Stats table (sessions count, avg duration, BUYER_READY %)
- Pricing (\$X per session)
- Sample data download link (placeholder)
- Provenance verify quickstart
- Contact

## 验收

- [ ] reads sweep_summary.json
- [ ] outputs markdown with 4 sections
- [ ] `pytest tests/test_gen_marketplace_listing.py` 全绿
- [ ] Black + ruff

## 不要做

- 不连真 marketplace
- 不写 pricing engine
- 直接 commit `feat/S113-marketplace-listing`
