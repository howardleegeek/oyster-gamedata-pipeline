# Buyer SDK Quick Start

> Read time: 8 min. By the end you'll have ingested a tarball, validated it,
> iterated over its frames, and run a batch-quality evaluation.

The Oyster GameData buyer-spec v1 deliverable is a `.tar.gz` containing five
artefacts (PRD §3):

```
<clip_id>/
├── video.mp4              # 5–6 min · 1920×1080 · 30 fps · H.264/H.265
├── systeminfo.json        # game window geometry + map bounds
├── action_camera.json     # per-frame 20-field telemetry (≈ 9 000 frames)
├── gameinfo.xlsx          # operator-curated metadata
└── depth/*.exr            # view-space metric depth · 6 fps · float32 single-channel Z
```

You get **three things** from us so you don't have to re-implement schema logic:

1. **Python SDK** — `pip install oyster-gamedata-sdk`
2. **TypeScript SDK** — `npm install @oysterworld/gamedata-sdk`
3. **Evaluation harness** — `python bin/buyer_eval_harness.py`

All three work offline. No Supabase / Vercel / API keys needed for the lint
and iteration paths; the network is only used by the TS `BuyerClient` when you
actively call `.list()` / `.download()`.

---

## 1. Install (5 min)

### Python (≥ 3.10)

```bash
pip install oyster-gamedata-sdk          # core (stdlib only)
pip install 'oyster-gamedata-sdk[full]'  # +openpyxl +OpenEXR +numpy +opencv
```

Or from the repo:

```bash
cd sdk/python
pip install -e '.[full]'
```

### TypeScript / Node (≥ 18)

```bash
npm install @oysterworld/gamedata-sdk
# or
bun add @oysterworld/gamedata-sdk
```

---

## 2. Validate a delivered tarball (1 min)

You receive `vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz`. Verify it before
running expensive training.

### CLI

```bash
oyster-gamedata validate vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz
```

Output:

```
[PASS] 24/24 criteria passed (100.0%)
```

Or `[FAIL]` with a list of failed criteria.

### Python

```python
from oyster_gamedata_sdk import Tarball

with Tarball.from_path("clip-00042.tar.gz") as tar:
    report = tar.validate()
    print(report.summary())
    if not report.passed:
        for r in report.failed():
            print(f"  FAIL #{r.criterion_id}: {r.name} — {r.message}")
```

### TypeScript

```ts
import { validateLocalTarball } from '@oysterworld/gamedata-sdk';

const report = await validateLocalTarball('./clip-00042.tar.gz');
console.log(report.summary);
if (!report.passed) {
  for (const r of report.results.filter((r) => !r.passed)) {
    console.log(`  FAIL [${r.id}] ${r.name}: ${r.message}`);
  }
}
```

> **Note**: the TS validator runs structural + schema checks (9 criteria). For
> the full 24-criterion content-level lint (depth-pixel ratio, fx==fy, frame
> continuity, …), use the Python SDK or the `oyster-gamedata` CLI.

---

## 3. Iterate over a tarball's content (3 min)

### Python — frames, intrinsics, depth, video

```python
from oyster_gamedata_sdk import Tarball
import cv2

with Tarball.from_path("clip-00042.tar.gz") as tar:
    # systeminfo
    si = tar.systeminfo
    assert si.width == 1920 and si.height == 1080

    # action_camera — 9000 frames, 20 fields each
    for frame in tar.action_camera[:5]:
        print(frame.frame, frame.camera_position, frame.key_code)
        print("  fx == fy:", frame.camera_intrinsics.is_pinhole)

    # gameinfo.xlsx (requires openpyxl)
    gi = tar.gameinfo
    print(gi.fields.get("scene_name"))

    # depth — 1800 EXR float32 single-channel Z
    for frame_idx, depth_array in tar.depth:
        # depth_array is a 2-D numpy.ndarray of float32 in metres.
        assert depth_array.dtype.name == "float32"
        if frame_idx >= 3:
            break

    # video as a path …
    print(tar.video.path)  # /tmp/.../clip-00042/video.mp4

    # … or as a cv2 capture
    cap = tar.video.open_cv2()
    ok, frame_bgr = cap.read()
    cap.release()
```

### TypeScript — schema-only

```ts
import * as fs from 'node:fs';
import { parseSysteminfo, parseActionCamera } from '@oysterworld/gamedata-sdk';

// Open an already-extracted clip dir
const si = parseSysteminfo(JSON.parse(fs.readFileSync('clip/systeminfo.json', 'utf8')));
const frames = parseActionCamera(JSON.parse(fs.readFileSync('clip/action_camera.json', 'utf8')));
console.log(si.width, frames.length);
```

The TS SDK accepts BOTH the PRD `{x,y,z}` form and the released-sample array
`[x,y,z]` form. Same for quaternions (`[x,y,z,w]`).

---

## 4. Download a batch via the buyer API (2 min)

If your account has API credentials, the TS SDK can list/download:

```ts
import { BuyerClient } from '@oysterworld/gamedata-sdk';

const client = new BuyerClient({
  baseUrl: 'https://api.oysterworld.dev/buyer/v1',
  apiKey: process.env.OYSTER_API_KEY,
});

const { items } = await client.list({ batch_id: 'vendor-001_batch-2026-05-A' });
for (const m of items) {
  await client.download(m.clip_id, { outputDir: './downloads' });
  const report = await client.validate(`./downloads/${m.filename}`);
  if (!report.passed) {
    console.warn('rejecting', m.clip_id, report.summary);
  }
}
```

`BuyerClient` uses presigned-URL download and verifies SHA-256 automatically.

---

## 5. Evaluate a batch (3 min)

Once you have a directory full of validated tarballs, run the eval harness to
get diversity + coverage statistics:

```bash
python bin/buyer_eval_harness.py \
    --batch-dir ./downloads/batch-A \
    --output    ./eval
open ./eval/eval_report.html
```

The harness reports:

- `route_type` distribution (target: 50% type 1, 25% type 2, 25% type 3)
- `scene_name` and `operator_id` distributions (operator diversity)
- per-clip trajectory **path length**, **bbox span**, **mean speed**
- per-clip **stationary fraction** (spec gate: ≤ 10%)
- per-clip Shannon entropy of **keyCode** (action diversity)
- per-clip FPS mean / σ / range (spec gate: 30 fps stable)
- median batch statistics for at-a-glance triage

JSON output (`eval_report.json`) is machine-readable for CI gating; HTML
(`eval_report.html`) is a JS-free standalone page with inline SVG charts you
can open offline.

Skip the lint pass with `--no-lint` if you've already validated:

```bash
python bin/buyer_eval_harness.py --batch-dir ./downloads/batch-A -o ./eval --no-lint
```

---

## 6. CI integration (1 min)

A typical buyer CI gate:

```yaml
# .github/workflows/buyer-acceptance.yml
- name: Validate every clip
  run: |
    pip install 'oyster-gamedata-sdk[full]'
    for clip in downloads/*.tar.gz; do
      oyster-gamedata validate "$clip" --json -o "reports/$(basename "$clip" .tar.gz).json"
    done
- name: Quality report
  run: python bin/buyer_eval_harness.py --batch-dir downloads -o eval
- uses: actions/upload-artifact@v4
  with: { name: eval-report, path: eval/ }
```

Exit code `0` = batch ready to ingest. Exit `1` = lint failures present.

---

## API reference

- **Python SDK**: `sdk/python/README.md`
- **TypeScript SDK**: `sdk/typescript/README.md`
- **Buyer-spec v1 fields**: `docs/BUYER_SPEC_V1.md`
- **PRD (Chinese)**: `docs/PRD.md`

For questions: **Howard Li** · howard.linra@gmail.com · WhatsApp +1 (341) 250-6526.
