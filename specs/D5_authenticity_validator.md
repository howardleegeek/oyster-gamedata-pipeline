# D5 — Tarball authenticity validator (atomic, no pipeline coupling)

## Goal
Implement `bin/tarball_authenticity_check.py` — given a buyer-spec tarball,
scan every file and return a JSON report classifying each as
**REAL / PLACEHOLDER / UNKNOWN** with concrete evidence.

This is the **proof-of-truth gate** that runs before any tarball ships.
If any file is classified PLACEHOLDER, the script exits non-zero so a CI
guard can block the release.

## Public API (CLI)

```bash
python3 bin/tarball_authenticity_check.py /path/to/bundle.tar.gz [--json]
```

Exits:
- 0 if all files REAL
- 1 if any file PLACEHOLDER or UNKNOWN
- 2 on usage error / tarball missing

## Per-file checks (use these heuristics, not LLM judgment)

| File | REAL test | PLACEHOLDER markers |
|------|-----------|---------------------|
| `video.mp4` | ffprobe metadata: encoder is NOT `Lavfi`/`testsrc`; tags do not contain `lavfi`/`testsrc`; mean frame brightness varies frame-to-frame > 5% (real footage has motion) | TAG:encoder=Lavf*+lavfi, all frames identical |
| `depth/*.exr` | content-hash all EXR files; if **<5% unique hashes**, it's a placeholder farm (1801 hardlinks of one gradient = 1 unique). REAL inference gives ~one-unique-per-frame. | unique_ratio < 0.05 |
| `action_camera.json` | parse JSON, count distinct `camera_position` triples; if **<5% distinct values**, it's pad-fill | distinct_pos / records < 0.05 |
| `gameinfo.xlsx` | open with openpyxl; check no cell contains the literal string `placeholder`/`stub`/`stop-gap` (case-insensitive) | match found |
| `systeminfo.json` | parse JSON; check `recordedAt` is parseable ISO datetime, `recorderVersion` matches `lite-vN.M.Z` | missing keys, garbage values |
| `README.md` | inspect for "placeholder if absent" wording or "stop-gap" mentions | match found |
| `*.placeholder` | any file with this extension is automatically PLACEHOLDER | any |

## Output JSON schema

```json
{
  "tarball": "/path/to/bundle.tar.gz",
  "verdict": "REAL" | "PLACEHOLDER" | "MIXED",
  "files": [
    {
      "name": "video.mp4",
      "status": "REAL" | "PLACEHOLDER" | "UNKNOWN",
      "evidence": "string — what the check found"
    },
    ...
  ],
  "summary": {
    "real_count": N,
    "placeholder_count": M,
    "unknown_count": K
  }
}
```

## Hard requirements

1. **Use ffprobe** for video.mp4 metadata (subprocess call, parse stdout JSON
   via `-of json`).
2. Use `openpyxl` for xlsx.
3. Use `OpenEXR` + content hash for depth.
4. Pure stdlib for JSON / tar.
5. NO LLM call. NO network call. Pure file inspection.
6. NO false PASS — when in doubt, mark UNKNOWN. The exit-1 gate covers
   UNKNOWN too.

## Test (must pass `pytest -q`)

```python
# tests/test_tarball_authenticity_check.py
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest


def _build_dummy_tarball(tmp_path, *, video_synth=True, depth_unique=False):
    """Build a fake bundle to test the validator's classification."""
    bundle = tmp_path / "buyer-spec-demo"
    bundle.mkdir()

    # video — synthesized testsrc OR copy of a real-ish file
    import imageio.v2 as iio
    import numpy as np
    w = iio.get_writer(str(bundle / "video.mp4"), fps=30, codec="libx264",
                       pixelformat="yuv420p", macro_block_size=1)
    if video_synth:
        # constant frames = synthetic
        for _ in range(30):
            w.append_data(np.zeros((128, 128, 3), dtype=np.uint8))
    else:
        for i in range(30):
            w.append_data(np.full((128, 128, 3), (i * 8) % 256, dtype=np.uint8))
    w.close()

    # depth — same file copied or per-frame unique
    depth = bundle / "depth"
    depth.mkdir()
    seed = depth / "frame_seed.exr"
    seed.write_bytes(b"\x00" * 256)  # tiny dummy "EXR"
    for i in range(20):
        target = depth / f"frame_{i:06d}.exr"
        if depth_unique:
            target.write_bytes((b"\x00" * 200) + bytes([i]) * 56)
        else:
            target.write_bytes(seed.read_bytes())  # placeholder copy

    # other files
    (bundle / "systeminfo.json").write_text('{"recordedAt": "2026-05-06T21:00:00Z", "recorderVersion": "lite-v0.24.0"}')
    (bundle / "action_camera.json").write_text(
        json.dumps([{"frame": i, "camera_position": [i, 0, 0]} for i in range(20 if depth_unique else 1)])
    )

    out = tmp_path / "bundle.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(bundle, arcname=bundle.name)
    return out


def test_synthetic_tarball_flagged(tmp_path):
    tarball = _build_dummy_tarball(tmp_path, video_synth=True, depth_unique=False)
    res = subprocess.run(
        ["python3", "bin/tarball_authenticity_check.py", str(tarball), "--json"],
        capture_output=True, text=True,
    )
    assert res.returncode != 0  # fail because depth is hardlinks
    report = json.loads(res.stdout)
    assert report["verdict"] in ("PLACEHOLDER", "MIXED")
    statuses = {f["name"]: f["status"] for f in report["files"]}
    assert statuses.get("depth/") == "PLACEHOLDER"


def test_real_tarball_passes(tmp_path):
    tarball = _build_dummy_tarball(tmp_path, video_synth=False, depth_unique=True)
    res = subprocess.run(
        ["python3", "bin/tarball_authenticity_check.py", str(tarball), "--json"],
        capture_output=True, text=True,
    )
    # Permissive — at least depth + action_camera should be REAL
    report = json.loads(res.stdout)
    statuses = {f["name"]: f["status"] for f in report["files"]}
    assert statuses.get("depth/") == "REAL"
```

## Acceptance

- [ ] `bin/tarball_authenticity_check.py` exists, executable
- [ ] `tests/test_tarball_authenticity_check.py` runs
- [ ] Both tests pass

## Don't

- Don't fall back to LLM judgment.
- Don't silently classify UNKNOWN as REAL.
- Don't modify any other file.
