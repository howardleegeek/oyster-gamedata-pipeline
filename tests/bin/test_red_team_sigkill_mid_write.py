#!/usr/bin/env python3
"""Tests for bin/red_team_sigkill_mid_write.py — Red-team: SIGKILL mid-write adapter."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

# Import the module under test
from bin import red_team_sigkill_mid_write as sigkill_mod


class TestBuildChildScript:
    """Tests for _build_child_script()."""

    def test_script_contains_payload_size(self):
        """Script interpolates payload_size correctly."""
        script = sigkill_mod._build_child_script(payload_size=1024, chunk_size=512, sleep_per_chunk=0.01)
        assert "payload_size, chunk_size, sleep_t = 1024, 512, 0.01" in script

    def test_script_contains_chunk_size(self):
        """Script interpolates chunk_size correctly."""
        script = sigkill_mod._build_child_script(payload_size=1024, chunk_size=512, sleep_per_chunk=0.01)
        assert "payload_size, chunk_size, sleep_t = 1024, 512, 0.01" in script

    def test_script_contains_sleep_per_chunk(self):
        """Script interpolates sleep_per_chunk correctly."""
        script = sigkill_mod._build_child_script(payload_size=1024, chunk_size=512, sleep_per_chunk=0.01)
        assert "payload_size, chunk_size, sleep_t = 1024, 512, 0.01" in script

    def test_script_creates_temp_file(self):
        """Script uses tempfile.mkstemp with correct prefix/suffix."""
        script = sigkill_mod._build_child_script(payload_size=1024, chunk_size=512, sleep_per_chunk=0.01)
        assert 'tempfile.mkstemp(dir=target_dir, prefix=".action_camera_", suffix=".tmp")' in script

    def test_script_uses_atomic_rename(self):
        """Script uses os.rename for atomic commit."""
        script = sigkill_mod._build_child_script(payload_size=1024, chunk_size=512, sleep_per_chunk=0.01)
        assert "os.rename(tmp_path, final_path)" in script

    def test_script_writes_zero_payload(self):
        """Script handles zero payload size."""
        script = sigkill_mod._build_child_script(payload_size=0, chunk_size=512, sleep_per_chunk=0.01)
        assert "payload_size, chunk_size, sleep_t = 0, 512, 0.01" in script


class TestRunSigkillTrial:
    """Tests for _run_sigkill_trial()."""

    def test_returns_dict_with_expected_keys(self):
        """Returns dict with chunks_written, tmp_files, final_files, partial_found, final_found."""
        with tempfile.TemporaryDirectory(prefix="sigkill_test_") as tmpdir:
            work_dir = Path(tmpdir)
            result = sigkill_mod._run_sigkill_trial(
                work_dir=work_dir,
                payload_size=1024,
                chunk_size=512,
                sleep_per_chunk=0.001,
                kill_after_chunks=1,
            )
            assert isinstance(result, dict)
            assert "chunks_written" in result
            assert "tmp_files" in result
            assert "final_files" in result
            assert "partial_found" in result
            assert "final_found" in result

    def test_kill_after_zero_chunks(self):
        """Handles kill_after_chunks=0 (kill immediately)."""
        with tempfile.TemporaryDirectory(prefix="sigkill_test_") as tmpdir:
            work_dir = Path(tmpdir)
            result = sigkill_mod._run_sigkill_trial(
                work_dir=work_dir,
                payload_size=1024,
                chunk_size=512,
                sleep_per_chunk=0.001,
                kill_after_chunks=0,
            )
            # Should still return valid result
            assert result["chunks_written"] == 0

    def test_tmp_files_list_is_list(self):
        """tmp_files is a list of strings."""
        with tempfile.TemporaryDirectory(prefix="sigkill_test_") as tmpdir:
            work_dir = Path(tmpdir)
            result = sigkill_mod._run_sigkill_trial(
                work_dir=work_dir,
                payload_size=1024,
                chunk_size=512,
                sleep_per_chunk=0.001,
                kill_after_chunks=1,
            )
            assert isinstance(result["tmp_files"], list)
            assert all(isinstance(f, str) for f in result["tmp_files"])

    def test_final_files_list_is_list(self):
        """final_files is a list of strings."""
        with tempfile.TemporaryDirectory(prefix="sigkill_test_") as tmpdir:
            work_dir = Path(tmpdir)
            result = sigkill_mod._run_sigkill_trial(
                work_dir=work_dir,
                payload_size=1024,
                chunk_size=512,
                sleep_per_chunk=0.001,
                kill_after_chunks=1,
            )
            assert isinstance(result["final_files"], list)
            assert all(isinstance(f, str) for f in result["final_files"])

    def test_partial_found_is_boolean(self):
        """partial_found is a boolean."""
        with tempfile.TemporaryDirectory(prefix="sigkill_test_") as tmpdir:
            work_dir = Path(tmpdir)
            result = sigkill_mod._run_sigkill_trial(
                work_dir=work_dir,
                payload_size=1024,
                chunk_size=512,
                sleep_per_chunk=0.001,
                kill_after_chunks=1,
            )
            assert isinstance(result["partial_found"], bool)

    def test_final_found_is_boolean(self):
        """final_found is a boolean."""
        with tempfile.TemporaryDirectory(prefix="sigkill_test_") as tmpdir:
            work_dir = Path(tmpdir)
            result = sigkill_mod._run_sigkill_trial(
                work_dir=work_dir,
                payload_size=1024,
                chunk_size=512,
                sleep_per_chunk=0.001,
                kill_after_chunks=1,
            )
            assert isinstance(result["final_found"], bool)


class TestMainCLI:
    """Tests for main() CLI entry-point."""

    def test_help_exits_zero(self):
        """--help exits with code 0."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_help_contains_description(self):
        """--help shows description."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--help"],
            capture_output=True,
            text=True,
        )
        assert "SIGKILL" in result.stdout or "sigkill" in result.stdout.lower()

    def test_default_iterations_runs(self):
        """Default --iterations runs without error (quick test)."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--iterations", "1", "--payload-kb", "1", "--chunk-kb", "1"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Either 0 (pass) or 1 (fail - partial files found) is acceptable
        # The key is it runs without crashing
        assert result.returncode in (0, 1)

    def test_delay_ms_argument_accepted(self):
        """--delay-ms argument is accepted."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--delay-ms" in result.stdout

    def test_payload_kb_argument_accepted(self):
        """--payload-kb argument is accepted."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--payload-kb" in result.stdout

    def test_chunk_kb_argument_accepted(self):
        """--chunk-kb argument is accepted."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--chunk-kb" in result.stdout

    def test_unknown_argument_exits_nonzero(self):
        """Unknown argument exits with non-zero code."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--unknown-arg"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_zero_iterations_completes(self):
        """--iterations 0 completes without error."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--iterations", "0"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

class TestIntegrationSubprocess:
    """End-to-end tests via subprocess to verify actual behavior."""

    def test_script_runs_as_main(self):
        """Module runs as __main__ without import errors."""
        result = subprocess.run(
            [sys.executable, "-m", "bin.red_team_sigkill_mid_write", "--iterations", "1", "--payload-kb", "1", "--chunk-kb", "1"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Check that it actually ran (output contains test markers)
        assert "red-team" in result.stdout.lower() or "trial" in result.stdout.lower()
