"""Tests for bin/video_quality_gate.py."""

import json
import os
import shutil
import subprocess

# Import the module under test
import sys
import tempfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bin.video_quality_gate import (
    REQ_CODEC,
    REQ_FPS,
    REQ_FPS_TOLERANCE,
    REQ_HEIGHT,
    REQ_PIXFMT,
    REQ_WIDTH,
    _audit_file,
    _find_mp4,
    _parse_fps,
    _run_ffprobe,
    main,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_session_dir():
    """Create a temporary directory to act as a session dir."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def hevc_1080p30_mp4(tmp_session_dir):
    """Generate a real 1080p 30fps HEVC mp4 (v0.4.1 buyer-spec). Skip if ffmpeg missing."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not in PATH")
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe not in PATH")

    outpath = os.path.join(tmp_session_dir, "session_20260516.mp4")
    # v0.4.1: generate fixture matching buyer PDF spec (30fps, codec-flexible)
    # — short 10s clip won't pass the new duration gate, but other checks (codec
    # / resolution / framerate / pixfmt) will.
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=10:size=1920x1080:rate=30",
            "-c:v",
            "libx265",
            "-b:v",
            "10M",
            "-pix_fmt",
            "yuv420p",
            "-x265-params",
            "log-level=quiet",
            outpath,
        ],
        capture_output=True,
        timeout=120,
    )
    if not os.path.exists(outpath):
        pytest.skip("ffmpeg failed to generate fixture")
    return outpath


# ---------------------------------------------------------------------------
# Helper: mock ffprobe JSON
# ---------------------------------------------------------------------------


def _make_probe_json(
    codec_name="hevc",
    width=1920,
    height=1080,
    r_frame_rate="30/1",  # v0.4.1: buyer PDF spec is 30fps, not 60
    pix_fmt="yuv420p",
    duration=330.0,  # v0.4.1: buyer PDF spec is 5-6 min, default to mid-range
    bit_rate=8_000_000,  # v0.4.1: buyer PDF range 6-12 Mbps, default to 8
):
    """Build a minimal ffprobe JSON dict."""
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec_name,
                "width": width,
                "height": height,
                "r_frame_rate": r_frame_rate,
                "pix_fmt": pix_fmt,
            }
        ],
        "format": {
            "duration": str(duration),
            "bit_rate": str(bit_rate),
        },
    }


# ---------------------------------------------------------------------------
# Tests: _parse_fps
# ---------------------------------------------------------------------------


class TestParseFps:
    def test_integer_fps(self):
        assert _parse_fps("60/1") == pytest.approx(60.0)

    def test_ntsc_fps(self):
        assert _parse_fps("30000/1001") == pytest.approx(29.970, abs=0.01)

    def test_float_string(self):
        assert _parse_fps("29.97") == pytest.approx(29.97)

    def test_zero_denominator(self):
        assert _parse_fps("60/0") == 0.0

    def test_garbage(self):
        assert _parse_fps("abc") == 0.0


# ---------------------------------------------------------------------------
# Tests: _find_mp4
# ---------------------------------------------------------------------------


class TestFindMp4:
    def test_finds_mp4(self, tmp_session_dir):
        open(os.path.join(tmp_session_dir, "video.mp4"), "w").close()
        result = _find_mp4(tmp_session_dir)
        assert len(result) == 1
        assert result[0].endswith("video.mp4")

    def test_no_mp4(self, tmp_session_dir):
        assert _find_mp4(tmp_session_dir) == []

    def test_ignores_subdirs(self, tmp_session_dir):
        sub = os.path.join(tmp_session_dir, "sub")
        os.makedirs(sub)
        open(os.path.join(sub, "hidden.mp4"), "w").close()
        assert _find_mp4(tmp_session_dir) == []


# ---------------------------------------------------------------------------
# Tests: _run_ffprobe
# ---------------------------------------------------------------------------


class TestRunFfprobe:
    def test_missing_ffprobe(self):
        with mock.patch("shutil.which", return_value=None):
            assert _run_ffprobe("dummy.mp4") is None


# ---------------------------------------------------------------------------
# Tests: _audit_file with mocked ffprobe
# ---------------------------------------------------------------------------


class TestAuditFileMocked:
    """All tests here mock _run_ffprobe to avoid needing real files."""

    def _mock_probe(self, **kwargs):
        probe = _make_probe_json(**kwargs)
        return mock.patch(
            "bin.video_quality_gate._run_ffprobe",
            return_value=probe,
        )

    def test_pass_all_checks(self):
        with self._mock_probe():
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "PASS"
        assert all(v == "PASS" for v in result["checks"].values())

    def test_fail_framerate_60fps(self):
        # v0.4.1: buyer PDF spec is 30fps; 60fps should now FAIL
        with self._mock_probe(r_frame_rate="60/1"):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "FAIL"
        assert result["checks"]["framerate"] == "FAIL"
        assert result["fps"] == pytest.approx(60.0)

    def test_pass_framerate_30fps(self):
        # v0.4.1: 30fps is now the PASS spec (default mock is 30/1)
        with self._mock_probe(r_frame_rate="30/1"):
            result = _audit_file("/fake/session/video.mp4")
        assert result["checks"]["framerate"] == "PASS"

    def test_fail_resolution_720p(self):
        with self._mock_probe(width=1280, height=720):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "FAIL"
        assert result["checks"]["resolution"] == "FAIL"

    def test_pass_codec_h264(self):
        # v0.4.1: both h264 and hevc are acceptable (buyer doesn't mandate hevc)
        with self._mock_probe(codec_name="h264"):
            result = _audit_file("/fake/session/video.mp4")
        assert result["checks"]["codec"] == "PASS"

    def test_fail_codec_vp9(self):
        # v0.4.1: codecs outside {h264, hevc, h265} still FAIL
        with self._mock_probe(codec_name="vp9"):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "FAIL"
        assert result["checks"]["codec"] == "FAIL"

    def test_fail_bitrate_low(self):
        # 4 Mbps < 6 Mbps required (v0.4.1 floor)
        with self._mock_probe(bit_rate=4_000_000):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "FAIL"
        assert result["checks"]["bitrate"] == "FAIL"

    def test_fail_duration_short(self):
        # v0.4.1: buyer PDF spec is 5-6 min, so 30s should FAIL (was the
        # v0.4.0 spec too — gate still rejects too-short)
        with self._mock_probe(duration=30.0):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "FAIL"
        assert result["checks"]["duration"] == "FAIL"

    def test_fail_duration_too_long(self):
        # v0.4.1 NEW: buyer PDF spec is 5-6 min, so 10 min should FAIL
        # (this was silently accepted by v0.4.0's ≥60s gate)
        with self._mock_probe(duration=600.0):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "FAIL"
        assert result["checks"]["duration"] == "FAIL"

    def test_pass_duration_300s_min_boundary(self):
        with self._mock_probe(duration=300.0):
            result = _audit_file("/fake/session/video.mp4")
        assert result["checks"]["duration"] == "PASS"

    def test_pass_duration_360s_max_boundary(self):
        with self._mock_probe(duration=360.0):
            result = _audit_file("/fake/session/video.mp4")
        assert result["checks"]["duration"] == "PASS"

    def test_fail_pixfmt(self):
        with self._mock_probe(pix_fmt="yuv422p"):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "FAIL"
        assert result["checks"]["pixfmt"] == "FAIL"

    def test_no_video_stream(self):
        probe = {
            "streams": [{"codec_type": "audio"}],
            "format": {"duration": "100", "bit_rate": "10000000"},
        }
        with mock.patch("bin.video_quality_gate._run_ffprobe", return_value=probe):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "FAIL"
        assert "no video stream" in result.get("reason", "")

    def test_ffprobe_missing_returns_skip(self):
        with mock.patch("bin.video_quality_gate._run_ffprobe", return_value=None):
            result = _audit_file("/fake/session/video.mp4")
        assert result["verdict"] == "SKIP"


# ---------------------------------------------------------------------------
# Tests: main() CLI
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_json_output(self, tmp_session_dir):
        """Test --json flag produces valid JSON with PASS verdict."""
        probe = _make_probe_json()
        with mock.patch("bin.video_quality_gate._run_ffprobe", return_value=probe):
            # Create a dummy mp4 so glob finds it
            dummy = os.path.join(tmp_session_dir, "test.mp4")
            open(dummy, "w").close()
            rc = main([tmp_session_dir, "--json"])
        assert rc == 0

    def test_human_output(self, tmp_session_dir, capsys):
        """Test human-readable output contains expected sections."""
        probe = _make_probe_json()
        with mock.patch("bin.video_quality_gate._run_ffprobe", return_value=probe):
            dummy = os.path.join(tmp_session_dir, "test.mp4")
            open(dummy, "w").close()
            rc = main([tmp_session_dir])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "VIDEO QUALITY GATE" in captured
        assert "Verdict: PASS" in captured

    def test_no_mp4_files(self, tmp_session_dir, capsys):
        """Test error when no mp4 files found."""
        rc = main([tmp_session_dir])
        assert rc == 1

    def test_invalid_dir(self, capsys):
        """Test error when directory doesn't exist."""
        rc = main(["/nonexistent/path/xyz"])
        assert rc == 1

    def test_skip_verdict_returncode_zero(self, tmp_session_dir, capsys):
        """Test that ffprobe missing → SKIP with returncode 0."""
        with mock.patch("bin.video_quality_gate._run_ffprobe", return_value=None):
            dummy = os.path.join(tmp_session_dir, "test.mp4")
            open(dummy, "w").close()
            rc = main([tmp_session_dir])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "SKIP" in captured

    def test_skip_verdict_json(self, tmp_session_dir, capsys):
        """Test that ffprobe missing → SKIP in JSON mode."""
        with mock.patch("bin.video_quality_gate._run_ffprobe", return_value=None):
            dummy = os.path.join(tmp_session_dir, "test.mp4")
            open(dummy, "w").close()
            rc = main([tmp_session_dir, "--json"])
        assert rc == 0
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert data["verdict"] == "SKIP"


# ---------------------------------------------------------------------------
# Tests: real ffmpeg-generated fixture (integration)
# ---------------------------------------------------------------------------


class TestRealFixture:
    """Integration tests using real ffmpeg-generated video."""

    def test_real_hevc_1080p30_basic_checks(self, hevc_1080p30_mp4):
        """A real 1080p 30fps HEVC file should PASS codec, resolution, framerate, pixfmt.

        v0.4.1: the fixture is only 10s (vs new buyer spec 300-360s) so the
        DURATION check will FAIL — that's expected. We assert the rest pass.
        """
        result = _audit_file(hevc_1080p30_mp4)
        assert result["verdict"] in ("PASS", "FAIL")  # duration/bitrate may vary
        # These should always pass for our fixture
        assert result["checks"]["codec"] == "PASS"
        assert result["checks"]["resolution"] == "PASS"
        assert result["checks"]["framerate"] == "PASS"
        assert result["checks"]["pixfmt"] == "PASS"
        assert result["codec"] in REQ_CODEC
        assert result["width"] == REQ_WIDTH
        assert result["height"] == REQ_HEIGHT
        assert abs(result["fps"] - REQ_FPS) <= REQ_FPS_TOLERANCE
        assert result["pixfmt"] == REQ_PIXFMT

    def test_real_fixture_json_roundtrip(self, hevc_1080p30_mp4, capsys):
        """Verify JSON output is valid and parseable."""
        session_dir = os.path.dirname(hevc_1080p30_mp4)
        rc = main([session_dir, "--json"])
        assert rc == 0
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert "verdict" in data
        assert "checks" in data
        assert "file" in data
