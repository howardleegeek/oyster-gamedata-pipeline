# D8 — Non-padding action_camera writer

Implement `bin/action_camera_no_pad.py trajectory.jsonl → action_camera.json`.
Output ONLY the real frames captured. No 9000-record forced minimum. No
forward-fill padding.

This breaks buyer-spec's "9000 records minimum" rule on purpose, so the
buyer sees exactly what's real. Pair with TRUTH_REPORT note.

Pure stdlib. Tests: 50-frame trajectory → 50-record output.
