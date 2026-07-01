#!/usr/bin/env python3
"""Tests for bin/audio_track_extractor.py"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the module is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import bin.audio_track_extractor as atx


class TestConstants:
    """Test module-level constants."""

    def test_max_gap_ms(self):
        assert atx.MAX_GAP_MS == 50.0

    def test_default_sample_rate(self):
        assert atx.DEFAULT_SAMPLE_RATE == 44100

    def test_default_channels(self):
        assert atx.DEFAULT_CHANNELS == 2

    def test_silence_threshold_db(self):
        assert atx.SILENCE_THRESHOLD_DB == -50.0

    def test_distortion_clip_ratio(self):
        assert atx.DISTORTION_CLIP_RATIO == 0.01


class TestGetNumpy:
    """Test _get_numpy function."""

    def test_returns_numpy_when_available(self):
        """When numpy is available, should return the module."""
        result = atx._get_numpy()
        # numpy should be available in test environment
        assert result is not None
        assert hasattr(result, 'array')


class TestEnsureFfmpeg:
    """Test _ensure_ffmpeg function."""

    def test_returns_true_when_ffmpeg_available(self):
        """When ffmpeg and ffprobe are available, returns True."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.MagicMock()
            result = atx._ensure_ffmpeg()
            assert result is True
            assert mock_run.call_count == 2  # ffmpeg and ffprobe

    def test_returns_false_when_ffmpeg_missing(self):
        """When ffmpeg is missing, returns False."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("ffmpeg")
            result = atx._ensure_ffmpeg()
            assert result is False

    def test_returns_false_when_ffprobe_missing(self):
        """When ffprobe is missing, returns False."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if 'ffprobe' in args[0]:
                raise FileNotFoundError("ffprobe")
            return mock.MagicMock()

        with mock.patch('subprocess.run', side_effect=side_effect):
            result = atx._ensure_ffmpeg()
            assert result is False

    def test_returns_false_when_ffmpeg_check_fails(self):
        """When ffmpeg --version fails, returns False."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "ffmpeg")
            result = atx._ensure_ffmpeg()
            assert result is False


class TestRunFfprobe:
    """Test _run_ffprobe function."""

    def test_runs_ffprobe_and_returns_stdout(self):
        """Should run ffprobe with args and return stdout."""
        mock_result = mock.MagicMock()
        mock_result.stdout = '{"streams": [{"duration": "10.5"}]}'

        with mock.patch('subprocess.run', return_value=mock_result) as mock_run:
            result = atx._run_ffprobe(['-show_format', 'input.mp4'])
            assert result == '{"streams": [{"duration": "10.5"}]}'
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == 'ffprobe'
            assert '-v' in args
            assert 'error' in args

    def test_raises_on_ffprobe_error(self):
        """Should raise CalledProcessError on ffprobe failure."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, 'ffprobe')
            with pytest.raises(subprocess.CalledProcessError):
                atx._run_ffprobe(['input.mp4'])


class TestGetAudioInfo:
    """Test _get_audio_info function."""

    def test_parses_audio_stream_info(self):
        """Should parse ffprobe output into audio info dict."""
        mock_ffprobe_output = {
            "streams": [{
                "duration": "120.5",
                "sample_rate": "48000",
                "channels": "6",
                "codec_name": "aac"
            }]
        }

        with mock.patch('bin.audio_track_extractor._run_ffprobe') as mock_run:
            mock_run.return_value = json.dumps(mock_ffprobe_output)

            result = atx._get_audio_info(Path("test.mp4"))

            assert result["duration_seconds"] == 120.5
            assert result["sample_rate"] == 48000
            assert result["channels"] == 6
            assert result["codec_name"] == "aac"

    def test_uses_defaults_for_missing_fields(self):
        """Should use defaults when fields are missing."""
        mock_ffprobe_output = {"streams": [{}]}

        with mock.patch('bin.audio_track_extractor._run_ffprobe') as mock_run:
            mock_run.return_value = json.dumps(mock_ffprobe_output)

            result = atx._get_audio_info(Path("test.mp4"))

            assert result["duration_seconds"] == 0.0
            assert result["sample_rate"] == 44100
            assert result["channels"] == 2
            assert result["codec_name"] == "unknown"


class TestDecodeToWav:
    """Test _decode_to_wav function."""

    def test_returns_true_on_success(self):
        """Should return True when ffmpeg succeeds and output file exists."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            with mock.patch('pathlib.Path.exists', return_value=True):
                with mock.patch('pathlib.Path.stat') as mock_stat:
                    mock_stat.return_value = mock.MagicMock(st_size=100)

                    result = atx._decode_to_wav(Path("input.mp4"), Path("output.wav"))

                    assert result is True
                    mock_run.assert_called_once()
                    args = mock_run.call_args[0][0]
                    assert 'ffmpeg' in args[0]
                    assert str(Path("input.mp4")) in args

    def test_returns_false_on_failure(self):
        """Should return False when ffmpeg fails."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=1)

            result = atx._decode_to_wav(Path("input.mp4"), Path("output.wav"))

            assert result is False

    def test_returns_false_when_output_file_missing(self):
        """Should return False when output file doesn't exist."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            with mock.patch('pathlib.Path.exists', return_value=False):
                result = atx._decode_to_wav(Path("input.mp4"), Path("output.wav"))
                assert result is False

    def test_returns_false_when_output_file_empty(self):
        """Should return False when output file is too small."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            with mock.patch('pathlib.Path.exists', return_value=True):
                with mock.patch('pathlib.Path.stat') as mock_stat:
                    mock_stat.return_value = mock.MagicMock(st_size=10)  # too small
                    result = atx._decode_to_wav(Path("input.mp4"), Path("output.wav"))
                    assert result is False


class TestDetectGaps:
    """Test _detect_gaps function."""

    def test_function_signature(self):
        """Function should accept samples and sample_rate."""
        import inspect
        sig = inspect.signature(atx._detect_gaps)
        params = list(sig.parameters.keys())
        assert 'samples' in params
        assert 'sample_rate' in params

    def test_returns_list_when_numpy_unavailable(self):
        """Should return empty list when numpy unavailable (skip check)."""
        with mock.patch('bin.audio_track_extractor._get_numpy', return_value=None):
            result = atx._detect_gaps(None, sample_rate=44100)
            assert result == []


class TestAnalyzeVolumeAndDistortion:
    """Test _analyze_volume_and_distortion function."""

    def test_function_signature(self):
        """Function should accept samples array."""
        import inspect
        sig = inspect.signature(atx._analyze_volume_and_distortion)
        params = list(sig.parameters.keys())
        assert 'samples' in params

    def test_returns_default_when_numpy_unavailable(self):
        """Should return safe default when numpy unavailable."""
        with mock.patch('bin.audio_track_extractor._get_numpy', return_value=None):
            result = atx._analyze_volume_and_distortion(None)
            # Should return a dict with safe defaults
            assert isinstance(result, dict)
            # Code returns clip_ratio, distortion_flag, peak_dbfs, rms_dbfs (not "clipping_ratio" or "status")
            assert "clip_ratio" in result
            assert "distortion_flag" in result
            assert result["clip_ratio"] is None
            assert result["distortion_flag"] is False


class TestReadPcmSamples:
    """Test _read_pcm_samples function."""

    def test_returns_numpy_array_when_available(self):
        """Should return numpy array when numpy is available."""
        # Create a valid WAV header (44 bytes) + some sample data
        wav_header = (
            b'RIFF' + b'\x24\x00\x00\x00' +  # File size - 8
            b'WAVE' +
            b'fmt ' + b'\x10\x00\x00\x00' +    # Chunk size (16)
            b'\x01\x00' +                        # Audio format (1 = PCM)
            b'\x02\x00' +                        # Num channels (2)
            b'\x44\xac\x00\x00' +              # Sample rate (44100)
            b'\x88\x58\x01\x00' +              # Byte rate
            b'\x04\x00' +                      # Block align
            b'\x10\x00' +                      # Bits per sample (16)
            b'data' + b'\x00\x00\x00\x00'      # Data chunk
        )
        # Add some PCM sample data (16-bit stereo = 4 bytes per sample)
        sample_data = b'\x00\x00\x10\x00\x00\x00\x20\x00'

        # Create mock that handles the entire chain:
        # np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
        np_mock = mock.MagicMock()
        mock_array = mock.MagicMock()
        mock_array.astype.return_value = mock.MagicMock()
        mock_array.astype.return_value.__truediv__ = mock.MagicMock(return_value=[0.1, 0.2])
        np_mock.frombuffer.return_value = mock_array
        np_mock.float64 = type(np_mock)  # mock dtype

        with mock.patch('bin.audio_track_extractor._get_numpy', return_value=np_mock):
            with mock.patch('builtins.open', mock.mock_open(read_data=wav_header + sample_data)):
                result = atx._read_pcm_samples(Path("test.wav"))
                # Should return numpy array (not None since header is valid)
                assert result is not None


class TestExtractAndValidate:
    """Test extract_and_validate function."""

    def test_returns_error_when_video_missing(self):
        """Should return error when video file doesn't exist."""
        with mock.patch('pathlib.Path.is_file', return_value=False):
            result = atx.extract_and_validate(Path("nonexistent.mp4"), Path("."))
            exit_code, qc = result
            assert exit_code == 1
            assert "error" in qc["status"].lower() or "not found" in qc["status"].lower()

    def test_returns_error_when_ffmpeg_missing(self):
        """Should return error when ffmpeg is not available."""
        with mock.patch('pathlib.Path.is_file', return_value=True):
            with mock.patch('bin.audio_track_extractor._ensure_ffmpeg', return_value=False):
                result = atx.extract_and_validate(Path("test.mp4"), Path("."))
                exit_code, qc = result
                assert exit_code == 1
                assert "ffmpeg" in qc["status"].lower() or "missing" in qc["status"].lower()

    def test_returns_tuple_with_qc_report(self):
        """Should return tuple of (exit_code, qc_report)."""
        with mock.patch('pathlib.Path.is_file', return_value=True):
            with mock.patch('bin.audio_track_extractor._ensure_ffmpeg', return_value=True):
                with mock.patch('subprocess.run') as mock_run:
                    mock_run.return_value = mock.MagicMock(returncode=0)
                    with mock.patch('pathlib.Path.exists', return_value=True):
                        with mock.patch('pathlib.Path.stat') as mock_stat:
                            mock_stat.return_value = mock.MagicMock(st_size=100)
                            # Mock flac output
                            with mock.patch('bin.audio_track_extractor._get_audio_info') as mock_info:
                                mock_info.return_value = {"duration_seconds": 10.0}
                                # Mock _decode_to_wav to succeed and _read_pcm_samples to return None (numpy unavailable)
                                with mock.patch('bin.audio_track_extractor._decode_to_wav', return_value=True):
                                    with mock.patch('bin.audio_track_extractor._read_pcm_samples', return_value=None):
                                        result = atx.extract_and_validate(Path("test.mp4"), Path("."))
                                        assert isinstance(result, tuple)
                                        assert len(result) == 2
                                        exit_code, qc = result
                                        assert isinstance(exit_code, int)
                                        assert isinstance(qc, dict)


class TestMain:
    """Test main CLI function."""

    def test_missing_arguments_shows_error(self):
        """Should show error when required arguments missing."""
        with mock.patch('sys.argv', ['audio_track_extractor.py']):
            with pytest.raises(SystemExit) as exc_info:
                atx.main()
            # argparse should exit with error
            assert exc_info.value.code != 0

    def test_ffmpeg_not_available(self):
        """Should return exit code 1 when ffmpeg not available."""
        # When ffmpeg is not available, main() returns exit code 1 (not raises SystemExit)
        with mock.patch('sys.argv', ['audio_track_extractor.py', 'input.mp4']):
            with mock.patch('bin.audio_track_extractor._ensure_ffmpeg', return_value=False):
                exit_code = atx.main()
                assert exit_code == 1


class TestModuleImports:
    """Test that module imports correctly."""

    def test_module_has_expected_attributes(self):
        """Module should have all expected public functions and constants."""
        assert hasattr(atx, 'MAX_GAP_MS')
        assert hasattr(atx, 'DEFAULT_SAMPLE_RATE')
        assert hasattr(atx, 'DEFAULT_CHANNELS')
        assert hasattr(atx, 'SILENCE_THRESHOLD_DB')
        assert hasattr(atx, 'DISTORTION_CLIP_RATIO')
        assert hasattr(atx, '_get_numpy')
        assert hasattr(atx, '_ensure_ffmpeg')
        assert hasattr(atx, '_run_ffprobe')
        assert hasattr(atx, '_get_audio_info')
        assert hasattr(atx, '_decode_to_wav')
        assert hasattr(atx, '_detect_gaps')
        assert hasattr(atx, '_analyze_volume_and_distortion')
        assert hasattr(atx, '_read_pcm_samples')
        assert hasattr(atx, 'extract_and_validate')
        assert hasattr(atx, 'main')

    def test_logger_is_configured(self):
        """Logger should be configured."""
        assert atx.logger is not None
        assert hasattr(atx.logger, 'info')
        assert hasattr(atx.logger, 'error')
        assert hasattr(atx.logger, 'debug')
