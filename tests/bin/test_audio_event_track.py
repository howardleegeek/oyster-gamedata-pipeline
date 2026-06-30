#!/usr/bin/env python3
"""Tests for bin/audio_event_track.py — per-frame audio peak + event-classifier.

Covers:
  * load_wav (mono, stereo, 16-bit PCM, path validation)
  * load_with_numpy (numpy import skip gracefully handled)
  * compute_peak (empty, single value, known max)
  * compute_rms (empty, single value, known values)
  * compute_zcr (empty, single value, known values)
  * compute_spectral_centroid (empty, single value, known spectrum)
  * segment_frames (empty, single frame, multi-frame)
  * classify_frame (all event types)
  * process_audio (full pipeline)
  * build_parser (defaults, custom args)
  * main() (missing input exits 1, valid input exits 0, JSON output)
"""

from __future__ import annotations

import json
import math
import struct
import sys
import wave
from io import BytesIO
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.audio_event_track import (  # noqa: E402
    EVENT_CLASSES,
    build_parser,
    classify_frame,
    compute_peak,
    compute_rms,
    compute_spectral_centroid,
    compute_zcr,
    load_wav,
    load_with_numpy,
    main,
    process_audio,
    segment_frames,
)

# ---------------------------------------------------------------------------
# Helper: Create a minimal WAV file in memory (16-bit PCM only)
# ---------------------------------------------------------------------------


def _make_wav_mono_16bit(num_samples: int = 4410, sample_rate: int = 44100) -> bytes:
    """Create a minimal valid 16-bit mono WAV file for testing."""
    raw = b""
    for i in range(num_samples):
        # Signed 16-bit: -32768 to 32767
        val = int(16000 * math.sin(2 * math.pi * 440 * i / sample_rate))
        val = max(-32768, min(32767, val))
        raw += struct.pack("<h", val)
    with BytesIO() as buf:
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(raw)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# load_wav
# ---------------------------------------------------------------------------


class TestLoadWav:
    """WAV file loading."""

    def test_mono_16bit(self):
        wav_bytes = _make_wav_mono_16bit(num_samples=4410)
        with Path("/tmp/test_mono.wav").open("wb") as f:
            f.write(wav_bytes)
        samples, sr = load_wav("/tmp/test_mono.wav")
        assert sr == 44100
        assert len(samples) == 4410
        assert -1.0 <= samples[0] <= 1.0

    def test_stereo_16bit(self):
        # Create stereo by writing two channels
        sample_rate = 44100
        num_samples = 1000
        raw = b""
        for i in range(num_samples):
            val = int(16000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            val = max(-32768, min(32767, val))
            # Write same sample for both channels
            raw += struct.pack("<hh", val, val)
        with BytesIO() as buf:
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(raw)
            wav_bytes = buf.getvalue()
        with Path("/tmp/test_stereo.wav").open("wb") as f:
            f.write(wav_bytes)
        samples, sr = load_wav("/tmp/test_stereo.wav")
        # Stereo gets mixed to mono
        assert len(samples) == num_samples
        assert -1.0 <= samples[0] <= 1.0

    def test_24bit_packed(self):
        # 24-bit packed: 3 bytes per sample, little-endian
        sample_rate = 44100
        num_samples = 100
        raw = b""
        for i in range(num_samples):
            val = int(8388607 * math.sin(2 * math.pi * 440 * i / sample_rate))
            raw += struct.pack("<i", val)[:3]  # 3 bytes only
        with BytesIO() as buf:
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(3)
                wf.setframerate(sample_rate)
                wf.writeframes(raw)
            wav_bytes = buf.getvalue()
        with Path("/tmp/test_24bit.wav").open("wb") as f:
            f.write(wav_bytes)
        samples, sr = load_wav("/tmp/test_24bit.wav")
        assert len(samples) == num_samples
        assert -1.0 <= samples[0] <= 1.0

    def test_nonexistent_path_raises(self):
        with pytest.raises(FileNotFoundError):
            load_wav("/nonexistent/file.wav")


# ---------------------------------------------------------------------------
# load_with_numpy
# ---------------------------------------------------------------------------


class TestLoadWithNumpy:
    """NumPy loading with graceful fallback."""

    def test_numpy_or_fallback(self):
        # Test that the function handles missing numpy
        # When numpy is not available, it falls back to load_wav
        # Since the file doesn't exist, it raises FileNotFoundError
        with pytest.raises(FileNotFoundError):
            load_with_numpy("/nonexistent.wav")

    def test_get_numpy_caching(self):
        # Test that _get_numpy returns consistent results
        from bin.audio_event_track import _get_numpy

        np1 = _get_numpy()
        np2 = _get_numpy()
        # Either both are None or both are the same module
        assert (np1 is None) or (np1 is np2)


# ---------------------------------------------------------------------------
# compute_peak
# ---------------------------------------------------------------------------


class TestComputePeak:
    """Peak amplitude computation."""

    def test_empty_list(self):
        assert compute_peak([]) == 0.0

    def test_single_value(self):
        assert compute_peak([0.5]) == 0.5

    def test_negative_values(self):
        assert compute_peak([-0.8, 0.3, -0.9]) == 0.9

    def test_known_max(self):
        assert compute_peak([0.1, 0.2, 0.9, 0.3]) == 0.9


# ---------------------------------------------------------------------------
# compute_rms
# ---------------------------------------------------------------------------


class TestComputeRms:
    """Root-mean-square computation."""

    def test_empty_list(self):
        assert compute_rms([]) == 0.0

    def test_single_value(self):
        assert compute_rms([0.5]) == 0.5

    def test_zeros(self):
        assert compute_rms([0.0, 0.0, 0.0]) == 0.0

    def test_known_rms(self):
        # RMS of [3, 4] = sqrt((9+16)/2) = sqrt(12.5) ≈ 3.536
        assert compute_rms([3.0, 4.0]) == pytest.approx(3.5355, abs=0.01)


# ---------------------------------------------------------------------------
# compute_zcr
# ---------------------------------------------------------------------------


class TestComputeZcr:
    """Zero-crossing rate computation."""

    def test_empty_list(self):
        assert compute_zcr([]) == 0.0

    def test_single_value(self):
        assert compute_zcr([0.5]) == 0.0

    def test_alternating_sign(self):
        # [-1, 1, -1, 1] has 3 zero crossings in 4 samples = 3/3 = 1.0
        assert compute_zcr([-1.0, 1.0, -1.0, 1.0]) == 1.0

    def test_no_sign_changes(self):
        # [0.1, 0.2, 0.3, 0.4] has 0 crossings
        assert compute_zcr([0.1, 0.2, 0.3, 0.4]) == 0.0


# ---------------------------------------------------------------------------
# compute_spectral_centroid
# ---------------------------------------------------------------------------


class TestComputeSpectralCentroid:
    """Spectral centroid computation."""

    def test_empty_list(self):
        assert compute_spectral_centroid([]) == 0.0

    def test_single_bin(self):
        # Test with a single frequency sine wave - spectral centroid should be
        # at that frequency (normalized to 0-0.5 range for rfftfreq with n=20)
        # Using 4 complete cycles in 20 samples = normalized freq 0.2
        import numpy as np
        samples = list(np.sin(2 * np.pi * 4 * np.arange(20) / 20))
        # For a pure sine wave, centroid equals the frequency
        result = compute_spectral_centroid(samples)
        assert 0.15 <= result <= 0.25  # centered around 0.2

    def test_short_samples(self):
        # Function returns 0.0 for samples < 4
        assert compute_spectral_centroid([1.0, 2.0, 3.0]) == 0.0

    def test_low_frequency_sine(self):
        # Low frequency sine wave (2 cycles in 64 samples = 0.03125 normalized)
        import numpy as np
        samples = list(np.sin(2 * np.pi * 2 * np.arange(64) / 64))
        result = compute_spectral_centroid(samples)
        assert 0.01 <= result <= 0.1  # Low normalized frequency

    def test_high_frequency_sine(self):
        # High frequency sine wave (30 cycles in 64 samples = ~0.47 normalized)
        import numpy as np
        samples = list(np.sin(2 * np.pi * 30 * np.arange(64) / 64))
        result = compute_spectral_centroid(samples)
        assert 0.3 <= result <= 0.5  # High normalized frequency


# ---------------------------------------------------------------------------
# segment_frames
# ---------------------------------------------------------------------------


class TestSegmentFrames:
    """Audio segmentation into frames."""

    def test_empty_samples(self):
        frames = segment_frames([], sample_rate=44100, frame_ms=50, hop_ms=25)
        assert frames == []

    def test_single_frame(self):
        # 2205 samples at 44100 Hz = 50ms, should produce 1 frame
        samples = [0.0] * 2205
        frames = segment_frames(samples, sample_rate=44100, frame_ms=50, hop_ms=25)
        assert len(frames) >= 1

    def test_multiple_frames(self):
        # 11025 samples at 44100 Hz = 250ms with default hop (frame_ms//2 = 25ms)
        # frame_len = 2205, hop = 1102
        # samples=11025: start positions at 0, 1102, 2204, 3306, 4408, 5510, 6612, 7714, 8816, 9918
        # 11025/1102 ≈ 10 frames
        samples = list(range(11025))
        frames = segment_frames(samples, sample_rate=44100, frame_ms=50)
        assert len(frames) >= 4  # At least 4 frames

    def test_frame_length(self):
        # 50ms at 44100 Hz = 2205 samples
        samples = list(range(4410))
        frames = segment_frames(samples, sample_rate=44100, frame_ms=50, hop_ms=25)
        # First frame should be ~2205 samples
        assert len(frames[0]) == 2205


# ---------------------------------------------------------------------------
# classify_frame
# ---------------------------------------------------------------------------


class TestClassifyFrame:
    """Frame event classification."""

    def test_silence(self):
        # Very low amplitude = silence (low peak, low zcr, low rms)
        event = classify_frame(peak=0.001, zcr=0.0, rms=0.001)
        assert event == "silence"

    def test_footstep(self):
        # Moderate amplitude, low ZCR
        event = classify_frame(peak=0.3, zcr=0.02, rms=0.15)
        assert event in EVENT_CLASSES

    def test_impact(self):
        # High amplitude
        event = classify_frame(peak=0.9, zcr=0.15, rms=0.7)
        assert event in EVENT_CLASSES

    def test_returns_valid_event(self):
        # Test with various random inputs
        import random

        random.seed(42)
        for _ in range(10):
            peak = random.uniform(0, 1)
            zcr = random.uniform(0, 0.5)
            rms = random.uniform(0, 1)
            event = classify_frame(peak=peak, zcr=zcr, rms=rms)
            assert event in EVENT_CLASSES


# ---------------------------------------------------------------------------
# process_audio
# ---------------------------------------------------------------------------


class TestProcessAudio:
    """Full audio processing pipeline."""

    def test_process_empty_file(self, tmp_path):
        # Empty WAV file should return dict with empty frames
        wav_path = tmp_path / "empty.wav"
        wav_bytes = _make_wav_mono_16bit(num_samples=0)
        wav_path.write_bytes(wav_bytes)
        result = process_audio(str(wav_path))
        assert isinstance(result, dict)
        assert result.get("frames") == []

    def test_process_short_file(self, tmp_path):
        # Short file should still process
        wav_path = tmp_path / "short.wav"
        wav_bytes = _make_wav_mono_16bit(num_samples=1000)
        wav_path.write_bytes(wav_bytes)
        result = process_audio(str(wav_path))
        assert isinstance(result, dict)
        assert "frames" in result

    def test_returns_dict_with_frames(self, tmp_path):
        # Generate 1 second of audio (440 Hz sine)
        wav_path = tmp_path / "tone.wav"
        wav_bytes = _make_wav_mono_16bit(num_samples=44100)
        wav_path.write_bytes(wav_bytes)
        result = process_audio(str(wav_path))
        assert isinstance(result, dict)
        assert "frames" in result
        frames = result["frames"]
        assert isinstance(frames, list)
        if frames:
            assert isinstance(frames[0], dict)


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    """Argument parser construction."""

    def test_default_values(self):
        parser = build_parser()
        args = parser.parse_args(["audio.wav"])
        assert args.audio == "audio.wav"
        assert args.frame_ms == 50
        assert args.out is None

    def test_custom_frame_ms(self):
        parser = build_parser()
        args = parser.parse_args(["audio.wav", "--frame-ms", "100"])
        assert args.frame_ms == 100

    def test_output_file(self):
        parser = build_parser()
        args = parser.parse_args(["audio.wav", "--out", "output.json"])
        assert args.out == "output.json"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """CLI entry point."""

    def test_missing_input_exits_nonzero(self):
        # main() returns int, not sys.exit()
        result = main(["nonexistent.wav"])
        assert result == 1

    def test_valid_input_exits_zero(self, tmp_path):
        # Create a valid WAV file
        wav_file = tmp_path / "test.wav"
        wav_bytes = _make_wav_mono_16bit(num_samples=1000)
        wav_file.write_bytes(wav_bytes)

        # Run main with the valid file
        result = main([str(wav_file)])
        assert result == 0

    def test_json_output_file(self, tmp_path):
        # Create a valid WAV file
        wav_file = tmp_path / "test.wav"
        wav_bytes = _make_wav_mono_16bit(num_samples=1000)
        wav_file.write_bytes(wav_bytes)

        output_file = tmp_path / "output.json"

        # Run main with output file
        result = main([str(wav_file), "--out", str(output_file)])
        assert result == 0

        # Verify output file exists and is valid JSON
        assert output_file.exists()
        with output_file.open() as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "event_summary" in data
