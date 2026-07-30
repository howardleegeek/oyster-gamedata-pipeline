# Oyster GameData SDK

Buyer-facing SDKs for the **Oyster GameData buyer-spec v1** tarball format.

This directory contains:

| Subdir | Package | What |
|---|---|---|
| `python/` | `oyster-gamedata-sdk` | Full-fat reader: typed schemas, lazy parsing, OpenEXR depth iterator, 24-criterion lint integration, CLI |
| `typescript/` | `@oysterworld/gamedata-sdk` | Thinner: typed schemas + HTTP `BuyerClient` + local structural validator |
| `python/oyster_buyer_sdk/` | (legacy scaffold) | The earlier REST/presigned-URL client. Kept for back-compat; new buyers should use `oyster_gamedata_sdk` |
| `javascript/oyster-buyer/` | (legacy scaffold) | Same — kept for the existing buyer portal |

**Companion tool**: `bin/buyer_eval_harness.py` — runs the data-quality
evaluation suite (trajectory diversity, action entropy, route distribution)
against a downloaded batch and emits a JSON + HTML report.

## Buyer flow

```
            ┌────────────────────────────────────────────────────┐
            │  1. Browse / download tarballs                     │
            │                                                    │
   TypeScript│   import { BuyerClient } from "@oysterworld/gamedata-sdk"
            │   const c = new BuyerClient({ baseUrl, apiKey })   │
            │   await c.download(clip_id, { outputDir })         │
            └──────────────────┬─────────────────────────────────┘
                               │
                               ▼
            ┌────────────────────────────────────────────────────┐
            │  2. Validate (local, offline)                      │
            │                                                    │
   Python   │   from oyster_gamedata_sdk import Tarball          │
            │   tar = Tarball.from_path(p)                       │
            │   r = tar.validate(); assert r.passed              │
            │                                                    │
            │  …or just CLI:                                     │
            │   oyster-gamedata validate clip.tar.gz --json      │
            └──────────────────┬─────────────────────────────────┘
                               │
                               ▼
            ┌────────────────────────────────────────────────────┐
            │  3. Iterate over a delivered batch                 │
            │                                                    │
            │   for fi, depth in tar.depth: ...                  │
            │   for f in tar.action_camera: ...                  │
            │   cap = tar.video.open_cv2()                       │
            └──────────────────┬─────────────────────────────────┘
                               │
                               ▼
            ┌────────────────────────────────────────────────────┐
            │  4. Evaluate batch quality                         │
            │                                                    │
            │   python bin/buyer_eval_harness.py \               │
            │     --batch-dir ./downloads/batch-A \              │
            │     -o ./eval                                      │
            │   open ./eval/eval_report.html                     │
            └────────────────────────────────────────────────────┘
```

See [`docs/BUYER_SDK_QUICKSTART.md`](../docs/BUYER_SDK_QUICKSTART.md) for the
full guide.
