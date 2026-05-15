"""Screen-capture recorder smoke tests.

Howard 2026-05-07: skip on Linux runners (mss requires X11/Quartz) and
also when ffprobe is missing. The actual recording path is exercised
via cluster D4 cycles in production.
"""

import shutil
import subprocess

import pytest

# Skip the entire module if mss isn't available (e.g. headless Linux CI).
pytest.importorskip("mss", reason="mss not installable on headless CI runners")
pytest.importorskip("imageio", reason="imageio not installed")

from bin.screen_capture_recorder import record_screen_region  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffprobe") is None,
    reason="ffprobe not on PATH; install ffmpeg to enable smoke tests",
)


def test_capture_2sec_smoke(tmp_path):
    out = tmp_path / "smoke.mp4"
    info = record_screen_region(out, duration_sec=2.0, fps=30)
    assert out.exists()
    assert out.stat().st_size > 10_000  # non-trivial mp4
    assert info["frames_captured"] >= 50  # 30fps × 2s = 60, allow 50+
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "h264" in res.stdout.lower()


def test_zero_duration_raises(tmp_path):
    with pytest.raises((ValueError, RuntimeError)):
        record_screen_region(tmp_path / "x.mp4", duration_sec=0.0)


def test_invalid_region_raises(tmp_path):
    with pytest.raises((ValueError, RuntimeError)):
        record_screen_region(
            tmp_path / "x.mp4",
            duration_sec=0.5,
            region=(-1, -1, 100, 100),
        )
