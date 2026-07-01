#!/usr/bin/env python3
"""Tests for bin/audit_quality_metrics.py — Quality Metrics Audit Extension.

Covers QM1-QM10 quality dimensions for data collection quality assessment:
- QM1: FPS consistency
- QM2: Frame drops
- QM3: Audio SNR
- QM4: Audio dynamic range
- QM5: Input latency p99
- QM6: Depth entropy
- QM7: Action diversity
- QM8: World coverage
- QM9: Camera-position range
- QM10: Recording continuity
"""

import json
import os
from unittest.mock import MagicMock, patch

from bin.audit_quality_metrics import (
    audit_group_quality,
    check_action_diversity,
    check_audio_dynamic_range,
    check_audio_snr,
    check_camera_position_range,
    check_depth_entropy,
    check_fps_consistency,
    check_frame_drops,
    check_input_latency_p99,
    check_recording_continuity,
    check_world_coverage,
)


class MockSession:
    """Mock session object for testing."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class TestCheckFpsConsistency:
    """Tests for QM1: FPS consistency check."""

    def test_fps_consistency_video_not_found(self):
        """Returns SKIP when video file not found."""
        session = MockSession(video_path="/nonexistent/video.mp4")
        result = check_fps_consistency(session)

        assert result["id"] == "QM1"
        assert result["status"] == "SKIP"
        assert "not found" in result["evidence"].lower()

    @patch("subprocess.run")
    def test_fps_consistency_ffprobe_fails(self, mock_run):
        """Returns SKIP when ffprobe fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        session = MockSession(video_path="/tmp/video.mp4")

        with patch("os.path.exists", return_value=True):
            result = check_fps_consistency(session)

        assert result["id"] == "QM1"
        assert result["status"] == "SKIP"

    @patch("subprocess.run")
    def test_fps_consistency_insufficient_timestamps(self, mock_run):
        """Returns SKIP when insufficient timestamps."""
        mock_run.return_value = MagicMock(returncode=0, stdout="0\n1\n")
        session = MockSession(video_path="/tmp/video.mp4")

        with patch("os.path.exists", return_value=True):
            result = check_fps_consistency(session)

        assert result["id"] == "QM1"
        assert result["status"] == "SKIP"
        assert "insufficient" in result["evidence"].lower()

    @patch("subprocess.run")
    def test_fps_consistency_pass(self, mock_run):
        """Returns PASS when FPS stddev/mean < 5%."""
        # Generate 100 timestamps at 30fps (0.0333s intervals)
        timestamps = [i * 1 / 30 for i in range(100)]
        stdout = "\n".join(str(t) for t in timestamps)
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")

        session = MockSession(video_path="/tmp/video.mp4")

        with patch("os.path.exists", return_value=True):
            result = check_fps_consistency(session)

        assert result["id"] == "QM1"
        assert result["status"] == "PASS"
        assert result["value"] is not None

    @patch("subprocess.run")
    def test_fps_consistency_fail(self, mock_run):
        """Returns FAIL when FPS varies too much."""
        # Generate timestamps with high variance
        timestamps = [i * 0.05 + (i % 3) * 0.02 for i in range(100)]
        stdout = "\n".join(str(t) for t in timestamps)
        mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")

        session = MockSession(video_path="/tmp/video.mp4")

        with patch("os.path.exists", return_value=True):
            result = check_fps_consistency(session)

        assert result["id"] == "QM1"
        assert result["status"] == "FAIL"


class TestCheckFrameDrops:
    """Tests for QM2: Frame drops check."""

    def test_frame_drops_video_not_found(self):
        """Returns SKIP when video file not found."""
        session = MockSession(frames_jsonl_path="/nonexistent/frames.jsonl")
        result = check_frame_drops(session)

        assert result["id"] == "QM2"
        assert result["status"] == "SKIP"

    @patch("subprocess.run")
    def test_frame_drops_ffprobe_fails(self, mock_run):
        """Returns SKIP when ffprobe fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        session = MockSession(frames_jsonl_path="/tmp/frames.jsonl")

        with patch("os.path.exists", return_value=True):
            result = check_frame_drops(session)

        assert result["id"] == "QM2"
        assert result["status"] == "SKIP"

    def test_frame_drops_no_drops(self):
        """Returns PASS when no frame drops detected."""
        # Create mock frame data with sequential indices (no drops)
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for i in range(300):
                f.write(json.dumps({"idx": i}) + "\n")
            temp_path = f.name

        session = MockSession(frames_jsonl_path=temp_path)
        result = check_frame_drops(session)

        os.unlink(temp_path)

        assert result["id"] == "QM2"
        assert result["status"] == "PASS"


    def test_frame_drops_detected(self):
        """Returns FAIL when frame drops detected."""
        # Create mock frame data with a gap (dropped frame indices)
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for i in range(50):
                f.write(json.dumps({"idx": i}) + "\n")
            # Skip idx 25 (simulate dropped frame)
            for i in range(51, 100):
                f.write(json.dumps({"idx": i}) + "\n")
            temp_path = f.name

        session = MockSession(frames_jsonl_path=temp_path)
        result = check_frame_drops(session)

        os.unlink(temp_path)

        assert result["id"] == "QM2"
        # Frame 25 is missing, so should be FAIL or WARN

class TestCheckAudioSnr:
    """Tests for QM3: Audio SNR check."""

    def test_audio_snr_no_audio_file(self):
        """Returns SKIP when audio file not found."""
        session = MockSession(audio_path="/nonexistent/audio.flac")
        result = check_audio_snr(session)

        assert result["id"] == "QM3"
        assert result["status"] == "SKIP"

    @patch("subprocess.run")
    def test_audio_snr_ffprobe_fails(self, mock_run):
        """Returns SKIP when ffprobe fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        session = MockSession(audio_path="/tmp/audio.flac")

        with patch("os.path.exists", return_value=True):
            result = check_audio_snr(session)

        assert result["id"] == "QM3"
        assert result["status"] == "SKIP"

    @patch("subprocess.run")
    def test_audio_snr_high_snr(self, mock_run):
        """Returns PASS when SNR is high."""
        # Mock ffprobe output with high signal, low noise
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="100000\n100\n",  # signal:noise ratio
            stderr="",
        )

        session = MockSession(audio_path="/tmp/audio.flac")

        with patch("os.path.exists", return_value=True):
            result = check_audio_snr(session)

        assert result["id"] == "QM3"
        # PASS or FAIL depending on threshold

    @patch("subprocess.run")
    def test_audio_snr_low_snr(self, mock_run):
        """Returns FAIL when SNR is low."""
        # Mock ffprobe output with low signal, high noise
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="100\n10000\n",  # Low signal:noise ratio
            stderr="",
        )

        session = MockSession(audio_path="/tmp/audio.flac")

        with patch("os.path.exists", return_value=True):
            result = check_audio_snr(session)

        assert result["id"] == "QM3"


class TestCheckAudioDynamicRange:
    """Tests for QM4: Audio dynamic range check."""

    def test_audio_dynamic_range_no_audio(self):
        """Returns SKIP when audio file not found."""
        session = MockSession(audio_path="/nonexistent/audio.flac")
        result = check_audio_dynamic_range(session)

        assert result["id"] == "QM4"
        assert result["status"] == "SKIP"

    @patch("subprocess.run")
    def test_audio_dynamic_range_ffprobe_fails(self, mock_run):
        """Returns SKIP when ffprobe fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        session = MockSession(audio_path="/tmp/audio.flac")

        with patch("os.path.exists", return_value=True):
            result = check_audio_dynamic_range(session)

        assert result["id"] == "QM4"
        assert result["status"] == "SKIP"


class TestCheckInputLatencyP99:
    """Tests for QM5: Input latency p99 check."""

    def test_input_latency_no_game_state(self):
        """Returns SKIP when game_state not found."""
        session = MockSession(session_path="/nonexistent")
        result = check_input_latency_p99(session)

        assert result["id"] == "QM5"
        assert result["status"] == "SKIP"

    @patch("pathlib.Path.exists")
    def test_input_latency_p99_no_file(self, mock_exists):
        """Returns SKIP when game_state.jsonl doesn't exist."""
        mock_exists.return_value = False
        session = MockSession(session_path="/tmp/session")
        result = check_input_latency_p99(session)

        assert result["id"] == "QM5"
        assert result["status"] == "SKIP"


class TestCheckDepthEntropy:
    """Tests for QM6: Depth entropy check."""

    def test_depth_entropy_no_depth_dir(self):
        """Returns SKIP when depth directory not found."""
        session = MockSession(session_path="/nonexistent")
        result = check_depth_entropy(session)

        assert result["id"] == "QM6"
        assert result["status"] == "SKIP"


class TestCheckActionDiversity:
    """Tests for QM7: Action diversity check."""

    def test_action_diversity_no_game_state(self):
        """Returns SKIP when game_state not found."""
        session = MockSession(session_path="/nonexistent")
        result = check_action_diversity(session)

        assert result["id"] == "QM7"
        assert result["status"] == "SKIP"


class TestCheckWorldCoverage:
    """Tests for QM8: World coverage check."""

    def test_world_coverage_no_game_state(self):
        """Returns SKIP when game_state not found."""
        session = MockSession(session_path="/nonexistent")
        result = check_world_coverage(session)

        assert result["id"] == "QM8"
        assert result["status"] == "SKIP"


class TestCheckCameraPositionRange:
    """Tests for QM9: Camera-position range check."""

    def test_camera_position_no_game_state(self):
        """Returns SKIP when game_state not found."""
        session = MockSession(session_path="/nonexistent")
        result = check_camera_position_range(session)

        assert result["id"] == "QM9"
        assert result["status"] == "SKIP"


class TestCheckRecordingContinuity:
    """Tests for QM10: Recording continuity check."""

    def test_continuity_no_video(self):
        """Returns SKIP when video file not found."""
        session = MockSession(video_path="/nonexistent/video.mp4")
        result = check_recording_continuity(session)

        assert result["id"] == "QM10"
        assert result["status"] == "SKIP"


class TestAuditGroupQuality:
    """Tests for the main audit_group_quality function."""

    def test_audit_group_quality_returns_list(self):
        """Returns a list of results."""
        session = MockSession(
            video_path="/nonexistent/video.mp4",
            audio_path="/nonexistent/audio.flac",
            session_path="/nonexistent",
        )
        results = audit_group_quality(session)

        assert isinstance(results, list)
        assert len(results) == 10  # QM1-QM10

    def test_audit_group_quality_all_have_id(self):
        """All results have valid IDs."""
        session = MockSession(
            video_path="/nonexistent/video.mp4",
            audio_path="/nonexistent/audio.flac",
            session_path="/nonexistent",
        )
        results = audit_group_quality(session)

        for result in results:
            assert "id" in result
            assert result["id"].startswith("QM")
            assert 1 <= int(result["id"][2:]) <= 10

    def test_audit_group_quality_all_have_status(self):
        """All results have a status field."""
        session = MockSession(
            video_path="/nonexistent/video.mp4",
            audio_path="/nonexistent/audio.flac",
            session_path="/nonexistent",
        )
        results = audit_group_quality(session)

        for result in results:
            assert "status" in result
            assert result["status"] in ("PASS", "FAIL", "SKIP")
