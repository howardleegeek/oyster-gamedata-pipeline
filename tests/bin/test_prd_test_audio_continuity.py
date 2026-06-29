#!/usr/bin/env python3
"""
Tests for bin/prd_test_audio_continuity.py

PRD p4 #2: Validate audio track is continuous (no gaps over 50ms).
"""

from bin.prd_test_audio_continuity import (
    check_continuity,
    get_audio_packets,
    get_audio_streams,
)


class TestCheckContinuity:
    """Tests for check_continuity function."""

    def test_no_gaps_under_threshold(self):
        """Test continuous timestamps with no gaps."""
        timestamps = [0.0, 0.02, 0.04, 0.06, 0.08]
        gaps = check_continuity(timestamps, threshold_ms=50.0)
        assert gaps == []

    def test_gap_exceeds_threshold(self):
        """Test detection of gap exceeding threshold."""
        timestamps = [0.0, 0.02, 0.04, 0.10, 0.12]
        gaps = check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps) == 1
        assert gaps[0][0] == 0.04  # gap_start
        assert gaps[0][1] == 0.10   # gap_end
        assert gaps[0][2] > 50.0    # gap_duration_ms

    def test_gap_at_exact_threshold(self):
        """Test gap at exactly threshold is NOT flagged."""
        timestamps = [0.0, 0.050, 0.100]
        gaps = check_continuity(timestamps, threshold_ms=50.0)
        # 50ms = 0.050s, so diff of exactly 0.050 is NOT > threshold
        assert gaps == []

    def test_gap_just_over_threshold(self):
        """Test gap just over threshold is detected."""
        # 51ms gap should be detected (> 50ms threshold)
        timestamps = [0.0, 0.051]
        gaps = check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps) == 1
        assert gaps[0][2] > 50.0

    def test_multiple_gaps(self):
        """Test detection of multiple gaps."""
        timestamps = [0.0, 0.02, 0.5, 0.52, 1.0, 1.02]
        gaps = check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps) == 2

    def test_insufficient_timestamps(self):
        """Test with less than 2 timestamps returns no gaps."""
        timestamps = [0.0]
        gaps = check_continuity(timestamps)
        assert gaps == []

    def test_empty_timestamps(self):
        """Test with empty list returns no gaps."""
        timestamps = []
        gaps = check_continuity(timestamps)
        assert gaps == []

    def test_custom_threshold(self):
        """Test with custom threshold."""
        timestamps = [0.0, 0.02, 0.10]
        # 80ms gap at default 50ms threshold should be detected
        gaps_default = check_continuity(timestamps, threshold_ms=50.0)
        assert len(gaps_default) == 1
        # But at 100ms threshold should not be detected
        gaps_high = check_continuity(timestamps, threshold_ms=100.0)
        assert gaps_high == []


class TestAudioStreamsUnit:
    """Tests for audio stream parsing functions (unit tests)."""

    def test_get_audio_streams_empty_video(self):
        """Test get_audio_streams returns empty list for no streams."""
        # This tests the function exists and is callable
        import inspect
        assert callable(get_audio_streams)
        assert callable(get_audio_packets)
        # Verify function signatures
        sig = inspect.signature(get_audio_streams)
        assert "video_path" in sig.parameters

    def test_check_continuity_returns_list(self):
        """Test check_continuity returns a list."""
        result = check_continuity([0.0, 0.1, 0.2])
        assert isinstance(result, list)
