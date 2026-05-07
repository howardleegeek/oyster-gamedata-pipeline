# Real action_camera.json — wire OBS WebSocket into recorder_consumer_lite

## Goal
Replace the placeholder `action_camera.json` writer in
`bin/recorder_consumer_lite.py` with **real** per-frame action+camera data
sourced from OBS WebSocket v5 (which mac-2 commit `bb05275` shipped as
`bin/obs_capture.py`).

After this lands, `clip-*.tar.gz` no longer contains the line:
```
action_camera.json   (placeholder; full impl in Rust app)
```

It contains real values:
- per-frame `mouse_x` / `mouse_y` / `mouse_dx` / `mouse_dy` (from Raw
  Input or OS cursor capture during recording window)
- per-frame `keyCode` (real Windows VK codes pressed during recording)
- per-frame `camera_position`, `camera_rotation_oula`, `camera_quat`,
  `camera_intrinsics` (from OBS WebSocket scene-source telemetry)
- per-frame `speed` (finite-differenced from `camera_position`)

## Hard requirements

1. NO placeholders. NO synthetic data. NO stubs. If a field can't be
   computed, the recording **must abort** with a clear error to the
   tester ("OBS WebSocket not reachable — install OBS Studio 30+ and
   enable WebSocket server in Tools → WebSocket Server Settings").
2. The placeholder block at `recorder_consumer_lite.py:~1665-1695` must
   be REMOVED, not commented out.
3. The action_camera writer must conform to PRD 20-field schema (see
   `docs/BUYER_SPEC_V1.md` "action_camera.json — 20 fields per frame").
4. Use `bin/obs_capture.py` (already in repo) as the live data source.
5. Add `requirements.txt` entry for `obsws-python>=1.7` (OBS WebSocket v5
   client) — recorder must vendor it via PyInstaller.

## Constraints

- Pure Python. No new C extensions.
- OBS WebSocket connection is established BEFORE ffmpeg recording starts.
  If OBS connection fails, **fail loud** — show a Tk error dialog with
  the specific reason and abort the recording cleanly.
- Each frame's action_camera record must be timestamped to match a
  ffmpeg frame timestamp (CFR 30fps: frame_n at n/30 sec from start).
- All floats serialized with `f"{x:.6f}"` precision (matches PRD).
- The 20 fields are fixed-order; missing data = recording abort, NEVER
  default zeros or nulls.

## Acceptance

- [ ] `bin/recorder_consumer_lite.py` no longer contains the literal
  string "placeholder" referring to action_camera fields.
- [ ] `python3 -m py_compile bin/recorder_consumer_lite.py` clean.
- [ ] Unit test `tests/test_recorder_action_camera_real.py` covers:
  - happy path (mock OBS responses → 20-field record)
  - OBS unreachable → recording aborts with specific error
  - mid-recording OBS disconnect → recording aborts, clip discarded
- [ ] All tests pass on `pytest -q`.

## Don't do

- Don't keep the placeholder code commented out. Delete it.
- Don't add a "fallback to placeholder" path. If OBS is down, abort.
- Don't synthesize `mouse_dx`/`mouse_dy` from `mouse_x` deltas if Raw
  Input is unavailable. Abort instead.
- Don't pretend to have depth data — that's a separate spec (Track C).
