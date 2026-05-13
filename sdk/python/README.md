# oyster-gamedata-sdk (Python)

Buyer-facing Python SDK for the **Oyster GameData buyer-spec v1** tarball
format. Lets a training-pipeline customer ingest, validate, and iterate
over delivered `.tar.gz` clips without re-implementing schema logic.

## Install

```bash
pip install oyster-gamedata-sdk            # core (stdlib only)
pip install 'oyster-gamedata-sdk[full]'    # +xlsx +EXR +numpy +opencv
```

## Quick start

```python
from oyster_gamedata_sdk import Tarball

with Tarball.from_path("vendor-001_batch-A_clip-00042_v1.tar.gz") as tar:
    si = tar.systeminfo
    print(f"{si.game_process_name} @ {si.width}x{si.height}")

    for frame in tar.action_camera[:5]:
        print(frame.frame, frame.camera_position, frame.key_code)

    report = tar.validate()
    print(report.summary())
    if not report.passed:
        for r in report.failed():
            print(f"  FAIL: {r.name} — {r.message}")
```

## CLI

```bash
oyster-gamedata inspect clip-00042.tar.gz
oyster-gamedata validate clip-00042.tar.gz --json -o report.json
oyster-gamedata summary clip-00042.tar.gz --json
```

## Public API

| Symbol | Purpose |
|---|---|
| `Tarball.from_path(p)` | Open tarball or extracted dir |
| `.video` | `Video` object — `.path`, `.open_cv2()` |
| `.systeminfo` | typed `Systeminfo` |
| `.action_camera` | `List[ActionCameraFrame]` |
| `.gameinfo` | `Gameinfo` (xlsx-parsed) |
| `.depth` | `DepthSequence` (iterator + indexer over EXR float32) |
| `.validate()` | `LintReport` — re-uses bin/lint_v3_prd_grounded.py |
| `.metadata_summary()` | quick stats |

See `docs/BUYER_SDK_QUICKSTART.md` for the full guide.
