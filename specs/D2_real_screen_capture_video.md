# D2 — Real screen-capture video module (atomic, no Paper/OBS dependency)

## Goal
Implement `bin/screen_capture_recorder.py` — record a real region of the
screen to mp4 using cross-platform native screen capture, replacing the
ffmpeg testsrc placeholder used in `bin/buyer_spec_pipeline.sh`.

## Public API

```python
def record_screen_region(
    output_path: Path,
    *,
    duration_sec: float,
    fps: int = 30,
    region: tuple[int, int, int, int] | None = None,  # (x, y, w, h); None=primary monitor
) -> dict:
    """Capture the screen (or a region) to an mp4 file.

    Implementation: use `mss` for screen grabs + `imageio-ffmpeg` for H.264 encode.

    Args:
        output_path: target mp4 file.
        duration_sec: how long to record.
        fps: target framerate (CFR).
        region: (x, y, w, h) in screen pixels. None = full primary monitor.

    Returns:
        dict with keys: frames_captured, actual_fps, width, height, duration_sec_actual.

    Raises:
        RuntimeError: if frame capture rate falls below 50% of requested fps,
                      or if any frame capture fails.
        ImportError: if mss / imageio not installed.
    """
```

## Hard requirements

1. Use `mss` library for screen grab (cross-platform, fast, no permissions
   prompts on macOS once granted).
2. Use `imageio` + `imageio-ffmpeg` for H.264 encode.
3. **Strict CFR**: maintain target fps within ±5% by sleeping between grabs.
   If unable, raise RuntimeError (NEVER silently drop frames or pad with the
   previous frame).
4. **NO synthetic fallback**. NO testsrc. NO color filter. If capture fails,
   raise.
5. Output mp4 must be valid H.264 1920×1080 (or specified resolution) and
   playable in ffprobe.

## Tests (must pass `pytest -q`)

```python
# tests/test_screen_capture_recorder.py
import subprocess
from pathlib import Path
import pytest

from bin.screen_capture_recorder import record_screen_region


def test_capture_2sec_smoke(tmp_path):
    out = tmp_path / "smoke.mp4"
    info = record_screen_region(out, duration_sec=2.0, fps=30)
    assert out.exists()
    assert out.stat().st_size > 10_000  # non-trivial mp4
    assert info["frames_captured"] >= 50  # 30fps × 2s = 60, allow 50+
    # ffprobe sanity
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert "h264" in res.stdout.lower()


def test_zero_duration_raises(tmp_path):
    with pytest.raises((ValueError, RuntimeError)):
        record_screen_region(tmp_path / "x.mp4", duration_sec=0.0)


def test_invalid_region_raises(tmp_path):
    with pytest.raises((ValueError, RuntimeError)):
        record_screen_region(tmp_path / "x.mp4", duration_sec=0.5, region=(-1, -1, 100, 100))
```

## Acceptance

- [ ] `python3 -m py_compile bin/screen_capture_recorder.py`
- [ ] `python3 -m pytest tests/test_screen_capture_recorder.py -q`
- [ ] All 3 tests pass on the dispatch env (minipc WSL — note: WSL has no
  display by default; if `mss.mss()` raises on WSL, the test should
  `pytest.skip` with a clear message that physical display is required).

## Don't

- Don't use ffmpeg testsrc / lavfi as a fallback.
- Don't drop frames silently — raise instead.
- Don't import cv2.
- Don't modify any other file.
