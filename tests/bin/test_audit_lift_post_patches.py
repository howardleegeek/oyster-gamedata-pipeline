#!/usr/bin/env python3
"""Tests for bin/audit_lift_post_patches.py"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add bin to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))

import audit_lift_post_patches as alpp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_completed_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


def _astats_stderr(rms: str, peak: str) -> str:
    """Build ffmpeg astats stderr in the format the regex expects."""
    return (
        f"[Parsed_astats_0 @ 0x123] Channel 1: RMS level dB: {rms}\n"
        f"[Parsed_astats_0 @ 0x123] Channel 1: Peak level dB: {peak}\n"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_session(tmp_path: Path) -> Path:
    """Return a fresh session dir."""
    session = tmp_path / "session_20260516_120000_xyz"
    session.mkdir()
    return session


@pytest.fixture
def session_with_metadata(tmp_session: Path) -> Path:
    """Session containing a minimal metadata.json with hardware_id but no device_id."""
    meta = {"hardware_id": "abc123", "session": "test"}
    (tmp_session / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp_session


@pytest.fixture
def session_with_game_state(session_with_metadata: Path) -> Path:
    """Session with metadata and a game_state.jsonl containing a timestamp_ms on line 1."""
    line = {"timestamp_ms": 1747400000000, "frame": 0}
    (session_with_metadata / "game_state.jsonl").write_text(
        json.dumps(line) + "\n", encoding="utf-8"
    )
    return session_with_metadata


# ---------------------------------------------------------------------------
# patch_metadata — M2 (device_id)
# ---------------------------------------------------------------------------


class TestPatchMetadataDeviceId:
    """Tests for M2 — device_id derivation from hardware_id MD5."""

    def test_missing_metadata_returns_error(self, tmp_session: Path):
        result = alpp.patch_metadata(tmp_session)
        assert "error" in result
        assert "missing" in result["error"]

    def test_corrupt_metadata_returns_error(self, tmp_session: Path):
        (tmp_session / "metadata.json").write_text("{not valid json", encoding="utf-8")
        result = alpp.patch_metadata(tmp_session)
        assert "error" in result
        assert "corrupt" in result["error"]

    def test_device_id_added_from_hardware_id(self, session_with_metadata: Path):
        result = alpp.patch_metadata(session_with_metadata)
        assert "device_id" in result
        # First 12 hex chars of MD5("abc123")
        import hashlib

        expected = hashlib.md5(b"abc123").hexdigest()[:12]
        assert result["device_id"] == expected
        assert len(result["device_id"]) == 12
        assert re.match(r"^[0-9a-f]{12}$", result["device_id"])

    def test_existing_device_id_not_overwritten(self, tmp_session: Path):
        meta = {"hardware_id": "abc123", "device_id": "preserve_me"}
        (tmp_session / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        result = alpp.patch_metadata(tmp_session)
        # Should NOT add a new device_id
        assert "device_id" not in result

    def test_no_hardware_id_skips_device_id(self, tmp_session: Path):
        meta = {"session": "x"}  # No hardware_id
        (tmp_session / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        result = alpp.patch_metadata(tmp_session)
        # device_id should not be in changes
        assert "device_id" not in result


# ---------------------------------------------------------------------------
# patch_metadata — M3/SS5 (UTC timestamps)
# ---------------------------------------------------------------------------


class TestPatchMetadataTimestamps:
    """Tests for M3 + SS5 — UTC ISO timestamps in metadata.json."""

    def test_timestamp_from_game_state_first_line(self, session_with_game_state: Path):
        result = alpp.patch_metadata(session_with_game_state)
        assert "recording_started_utc" in result
        # 1747400000 seconds UTC = 2025-05-16 16:53:20
        assert "2025-05-16" in result["recording_started_utc"]
        assert result["recording_started_utc"].endswith("+00:00")

    def test_timestamp_from_session_dir_name(self, tmp_session: Path):
        # No game_state.jsonl; rely on session dir name pattern
        meta = {"hardware_id": "x"}
        (tmp_session / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        result = alpp.patch_metadata(tmp_session)
        assert "recording_started_utc" in result
        assert "2026-05-16" in result["recording_started_utc"]

    def test_metadata_written_utc_added(self, session_with_metadata: Path):
        result = alpp.patch_metadata(session_with_metadata)
        assert "metadata_written_utc" in result
        # Should end with +00:00 (UTC tzinfo)
        assert result["metadata_written_utc"].endswith("+00:00")

    def test_existing_recording_started_utc_not_overwritten(
        self, session_with_game_state: Path
    ):
        meta = json.loads(
            (session_with_game_state / "metadata.json").read_text(encoding="utf-8")
        )
        meta["recording_started_utc"] = "2000-01-01T00:00:00+00:00"
        (session_with_game_state / "metadata.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )
        result = alpp.patch_metadata(session_with_game_state)
        assert "recording_started_utc" not in result

    def test_existing_metadata_written_utc_not_overwritten(
        self, session_with_metadata: Path
    ):
        meta = json.loads(
            (session_with_metadata / "metadata.json").read_text(encoding="utf-8")
        )
        meta["metadata_written_utc"] = "2000-01-01T00:00:00+00:00"
        (session_with_metadata / "metadata.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )
        result = alpp.patch_metadata(session_with_metadata)
        assert "metadata_written_utc" not in result

    def test_corrupt_game_state_falls_back_to_session_name(self, tmp_session: Path):
        meta = {"hardware_id": "x"}
        (tmp_session / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        (tmp_session / "game_state.jsonl").write_text("not json at all", encoding="utf-8")
        result = alpp.patch_metadata(tmp_session)
        # Should still derive from session dir name
        assert "recording_started_utc" in result
        assert "2026-05-16" in result["recording_started_utc"]

    def test_dry_run_does_not_write(self, session_with_metadata: Path):
        result = alpp.patch_metadata(session_with_metadata, dry_run=True)
        assert "device_id" in result
        # File should be unchanged
        meta = json.loads(
            (session_with_metadata / "metadata.json").read_text(encoding="utf-8")
        )
        assert "device_id" not in meta

    def test_no_changes_returns_empty_dict(self, tmp_session: Path):
        # Pre-populate with already-patched metadata
        meta = {
            "hardware_id": "x",
            "device_id": "deadbeefdead",
            "recording_started_utc": "2026-05-16T12:00:00+00:00",
            "metadata_written_utc": "2026-05-16T12:00:00+00:00",
        }
        (tmp_session / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        result = alpp.patch_metadata(tmp_session)
        assert result == {}


# ---------------------------------------------------------------------------
# patch_audio_check — error paths
# ---------------------------------------------------------------------------


class TestPatchAudioCheckMissing:
    """Tests for patch_audio_check when input is missing."""

    def test_missing_audio_returns_error(self, tmp_session: Path):
        result = alpp.patch_audio_check(tmp_session)
        assert "error" in result
        assert "audio.flac missing" in result["error"]


# ---------------------------------------------------------------------------
# patch_audio_check — happy path (ffmpeg mocked)
# ---------------------------------------------------------------------------


class TestPatchAudioCheckHappy:
    """Tests for patch_audio_check with ffmpeg mocked."""

    def test_writes_audio_check_json(self, tmp_session: Path):
        # Create a dummy audio.flac
        audio = tmp_session / "audio.flac"
        audio.write_bytes(b"FAKE")
        astats_stderr = _astats_stderr("-23.5", "-3.0")
        sd_stderr = "silence_duration: 0.3\n"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_completed_proc(stdout="", stderr=astats_stderr, returncode=0),
                _fake_completed_proc(stdout="60.0", stderr="", returncode=0),
                _fake_completed_proc(stdout="", stderr=sd_stderr, returncode=0),
            ]
            result = alpp.patch_audio_check(tmp_session)
        assert "error" not in result
        out = json.loads((tmp_session / "audio_check.json").read_text(encoding="utf-8"))
        assert out["rms_db"] == -23.5
        assert out["peak_db"] == -3.0
        assert out["max_silence_gap_s"] == 0.3
        assert out["audio_file"] == "audio.flac"

    def test_dry_run_does_not_write_file(self, tmp_session: Path):
        audio = tmp_session / "audio.flac"
        audio.write_bytes(b"FAKE")
        astats_stderr = _astats_stderr("-20.0", "-2.0")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_completed_proc(stdout="", stderr=astats_stderr),
                _fake_completed_proc(stdout="0.0", stderr=""),
                _fake_completed_proc(stdout="", stderr=""),
            ]
            alpp.patch_audio_check(tmp_session, dry_run=True)
        assert not (tmp_session / "audio_check.json").exists()

    def test_silent_audio_flag(self, tmp_session: Path):
        audio = tmp_session / "audio.flac"
        audio.write_bytes(b"FAKE")
        astats_stderr = _astats_stderr("-90.0", "-80.0")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_completed_proc(stdout="", stderr=astats_stderr),
                _fake_completed_proc(stdout="0.0", stderr=""),
                _fake_completed_proc(stdout="", stderr=""),
            ]
            result = alpp.patch_audio_check(tmp_session)
        out = json.loads((tmp_session / "audio_check.json").read_text(encoding="utf-8"))
        assert out["is_silent"] is True

    def test_nan_in_astats_becomes_none(self, tmp_session: Path):
        audio = tmp_session / "audio.flac"
        audio.write_bytes(b"FAKE")
        astats_stderr = _astats_stderr("nan", "nan")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_completed_proc(stdout="", stderr=astats_stderr),
                _fake_completed_proc(stdout="0.0", stderr=""),
                _fake_completed_proc(stdout="", stderr=""),
            ]
            result = alpp.patch_audio_check(tmp_session)
        # nan doesn't round-trip as JSON null, but in the in-memory dict
        # the parser does float("nan"), so check the dict result directly
        assert result["rms_db"] != result["rms_db"]  # NaN != NaN
        assert result["peak_db"] != result["peak_db"]

    def test_continuous_false_for_short_audio(self, tmp_session: Path):
        audio = tmp_session / "audio.flac"
        audio.write_bytes(b"FAKE")
        astats_stderr = _astats_stderr("-20.0", "-2.0")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_completed_proc(stdout="", stderr=astats_stderr),
                _fake_completed_proc(stdout="0.0", stderr=""),
                _fake_completed_proc(stdout="", stderr=""),
            ]
            result = alpp.patch_audio_check(tmp_session)
        out = json.loads((tmp_session / "audio_check.json").read_text(encoding="utf-8"))
        # dur_sec is 0.0 (no real ffmpeg probe), so continuous = False
        assert out["continuous"] is False

    def test_method_field_present(self, tmp_session: Path):
        audio = tmp_session / "audio.flac"
        audio.write_bytes(b"FAKE")
        astats_stderr = _astats_stderr("-10.0", "-5.0")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_completed_proc(stdout="", stderr=astats_stderr),
                _fake_completed_proc(stdout="0.0", stderr=""),
                _fake_completed_proc(stdout="", stderr=""),
            ]
            alpp.patch_audio_check(tmp_session)
        out = json.loads((tmp_session / "audio_check.json").read_text(encoding="utf-8"))
        assert "ffmpeg" in out["method"]

    def test_silence_detect_timeout_does_not_raise(self, tmp_session: Path):
        audio = tmp_session / "audio.flac"
        audio.write_bytes(b"FAKE")

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("cmd", [])
            cmd_str = " ".join(map(str, cmd))
            if "astats" in cmd_str:
                return _fake_completed_proc(stderr=_astats_stderr("-10.0", "-5.0"))
            if "ffprobe" in cmd_str:
                return _fake_completed_proc(stdout="0.0", stderr="")
            if "silencedetect" in cmd_str:
                raise subprocess.TimeoutExpired(cmd, 60)
            return _fake_completed_proc(stdout="", stderr="")

        with patch("subprocess.run", side_effect=side_effect):
            result = alpp.patch_audio_check(tmp_session)
        # Should not raise; max_silence_gap_s stays 0.0
        assert result["max_silence_gap_s"] == 0.0


# ---------------------------------------------------------------------------
# main — CLI
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the CLI main() entry point."""

    def test_main_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            alpp.main(["--help"])
        assert exc.value.code == 0

    def test_main_nonexistent_dir_returns_2(self, capsys, tmp_path: Path):
        result = alpp.main([str(tmp_path / "does_not_exist")])
        assert result == 2
        captured = capsys.readouterr()
        assert "ERROR" in captured.err

    def test_main_runs_both_patches(self, session_with_game_state: Path, capsys):
        # Mock audio patch to avoid ffmpeg call
        with patch("audit_lift_post_patches.patch_audio_check") as mock_audio:
            mock_audio.return_value = {"error": "audio.flac missing"}
            result = alpp.main([str(session_with_game_state)])
        assert result == 0
        captured = capsys.readouterr()
        assert "Patch 1+2+3" in captured.out
        assert "Patch 4-7" in captured.out

    def test_main_with_dry_run_flag(self, session_with_game_state: Path, capsys):
        with patch("audit_lift_post_patches.patch_audio_check") as mock_audio:
            mock_audio.return_value = {"error": "audio.flac missing"}
            alpp.main([str(session_with_game_state), "--dry-run"])
        # Metadata file should NOT have device_id written
        meta = json.loads(
            (session_with_game_state / "metadata.json").read_text(encoding="utf-8")
        )
        assert "device_id" not in meta


# ---------------------------------------------------------------------------
# _ASTATS_KEY_RE regex
# ---------------------------------------------------------------------------


class TestAstatsRegex:
    """Tests for the astats key parser regex."""

    def test_matches_rms_level(self):
        text = "[Parsed_astats_0 @ 0x1] Channel 1: RMS level dB: -23.5"
        matches = alpp._ASTATS_KEY_RE.findall(text)
        assert ("RMS level dB", "-23.5") in matches

    def test_matches_peak_level(self):
        text = "[Parsed_astats_0 @ 0x1] Channel 1: Peak level dB: -3.0"
        matches = alpp._ASTATS_KEY_RE.findall(text)
        assert ("Peak level dB", "-3.0") in matches

    def test_matches_nan(self):
        text = "[Parsed_astats_0 @ 0x1] Channel 1: RMS level dB: nan"
        matches = alpp._ASTATS_KEY_RE.findall(text)
        assert ("RMS level dB", "nan") in matches

    def test_no_match_for_unrelated_text(self):
        text = "this has nothing to do with astats\n"
        assert alpp._ASTATS_KEY_RE.findall(text) == []
