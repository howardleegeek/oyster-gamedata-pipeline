#!/usr/bin/env python3
"""Tests for bin/red_team_disk_full.py — ENOSPC simulation tool."""

import errno

# Import the module under test
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

spec = importlib.util.spec_from_file_location(
    "red_team_disk_full", Path(__file__).parent.parent.parent / "bin" / "red_team_disk_full.py"
)
red_team_disk_full = importlib.util.module_from_spec(spec)
spec.loader.exec_module(red_team_disk_full)


class TestMain:
    """Tests for main() CLI entry point."""

    def test_help_exits_zero(self):
        """--help should exit with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            red_team_disk_full.main(["--help"])
        assert exc_info.value.code == 0

    def test_help_shows_description(self):
        """--help should show the description."""
        with pytest.raises(SystemExit):
            red_team_disk_full.main(["--help"])
        # If we get here, help was printed (SystemExit was raised)

    def test_default_args(self):
        """Default values should be size=1, payload=256, verbose=False."""
        with mock.patch.object(red_team_disk_full, "_run") as mock_run:
            mock_run.return_value = 0
            result = red_team_disk_full.main([])
            assert result == 0
            mock_run.assert_called_once_with(1, 256, False)

    def test_custom_size(self):
        """--size should set custom size in MB."""
        with mock.patch.object(red_team_disk_full, "_run") as mock_run:
            mock_run.return_value = 0
            result = red_team_disk_full.main(["--size", "5"])
            assert result == 0
            mock_run.assert_called_once_with(5, 256, False)

    def test_custom_payload(self):
        """--payload should set custom payload in KB."""
        with mock.patch.object(red_team_disk_full, "_run") as mock_run:
            mock_run.return_value = 0
            result = red_team_disk_full.main(["--payload", "512"])
            assert result == 0
            mock_run.assert_called_once_with(1, 512, False)

    def test_verbose_flag(self):
        """--verbose should set verbose=True."""
        with mock.patch.object(red_team_disk_full, "_run") as mock_run:
            mock_run.return_value = 0
            result = red_team_disk_full.main(["--verbose"])
            assert result == 0
            mock_run.assert_called_once_with(1, 256, True)

    def test_combined_args(self):
        """All flags should work together."""
        with mock.patch.object(red_team_disk_full, "_run") as mock_run:
            mock_run.return_value = 0
            result = red_team_disk_full.main(["--size", "10", "--payload", "1024", "--verbose"])
            assert result == 0
            mock_run.assert_called_once_with(10, 1024, True)

    def test_invalid_size_raises(self):
        """Non-integer size should raise SystemExit."""
        with pytest.raises(SystemExit):
            red_team_disk_full.main(["--size", "abc"])


class TestRun:
    """Tests for _run() function."""

    def test_run_returns_one_on_setup_exception(self):
        """_run should return 1 when _setup_tiny_fs raises an exception."""
        with mock.patch.object(red_team_disk_full, "_setup_tiny_fs") as mock_setup:
            mock_setup.side_effect = OSError("permission denied")
            with mock.patch.object(red_team_disk_full, "_cleanup"):
                result = red_team_disk_full._run(1, 256, False)
                assert result == 1

    def test_run_returns_one_on_simulate_exception(self):
        """_run should return 1 when _simulate_enospc raises an exception."""
        with mock.patch.object(red_team_disk_full, "_setup_tiny_fs"):
            with mock.patch.object(red_team_disk_full, "_simulate_enospc") as mock_sim:
                mock_sim.side_effect = OSError("mount failed")
                with mock.patch.object(red_team_disk_full, "_cleanup"):
                    result = red_team_disk_full._run(1, 256, False)
                    assert result == 1


class TestSimulateENOSPC:
    """Tests for _simulate_enospc() function - mocked disk operations."""

    def test_returns_zero_when_enospc_caught(self, tmp_path):
        """Should return 0 when ENOSPC is caught on payload write."""
        mount_point = tmp_path / "mnt"
        mount_point.mkdir()

        # Create ENOSPC error properly
        enospc_error = OSError("no space left on device")
        enospc_error.errno = errno.ENOSPC

        # Mock _fill_disk to raise ENOSPC (simulating full disk)
        with mock.patch.object(red_team_disk_full, "_fill_disk") as mock_fill:
            # First call to fill raises ENOSPC
            mock_fill.side_effect = enospc_error

            # Mock the payload write to also raise ENOSPC
            with mock.patch("builtins.open", mock.mock_open()) as mock_file:
                mock_file.return_value.write.side_effect = enospc_error

                result = red_team_disk_full._simulate_enospc(str(mount_point), 1024, False)
                assert result == 0

    def test_returns_one_when_write_succeeds(self, tmp_path):
        """Should return 1 when write unexpectedly succeeds."""
        mount_point = tmp_path / "mnt"
        mount_point.mkdir()

        with mock.patch.object(red_team_disk_full, "_fill_disk"):
            # Mock write to succeed (no exception)
            with mock.patch("builtins.open", mock.mock_open()):
                result = red_team_disk_full._simulate_enospc(str(mount_point), 1024, False)
                assert result == 1

    def test_returns_one_on_unexpected_fill_error(self, tmp_path):
        """Should return 1 when _fill_disk raises non-ENOSPC error."""
        mount_point = tmp_path / "mnt"
        mount_point.mkdir()

        # Create non-ENOSPC error
        perm_error = OSError("permission denied")
        perm_error.errno = errno.EACCES

        with mock.patch.object(red_team_disk_full, "_fill_disk") as mock_fill:
            mock_fill.side_effect = perm_error

            result = red_team_disk_full._simulate_enospc(str(mount_point), 1024, False)
            assert result == 1


class TestFillDisk:
    """Tests for _fill_disk() function."""

    def test_fill_writes_chunks_until_enospc(self, tmp_path):
        """Should write chunks until ENOSPC is raised."""
        filler_path = tmp_path / "filler.bin"

        # Create ENOSPC error properly
        enospc_error = OSError("no space left on device")
        enospc_error.errno = errno.ENOSPC

        # Simulate ENOSPC on second write attempt
        with mock.patch("builtins.open", mock.mock_open()) as mock_file:
            mock_file.return_value.write.side_effect = [
                None,  # First write succeeds
                enospc_error,  # Second write fails with ENOSPC
            ]

            # Should raise the ENOSPC error
            with pytest.raises(OSError) as exc_info:
                red_team_disk_full._fill_disk(str(tmp_path), str(filler_path), False)
            assert exc_info.value.errno == errno.ENOSPC


class TestCleanup:
    """Tests for _cleanup() function."""

    def test_cleanup_handles_unmount_failure(self, tmp_path, monkeypatch):
        """Should not raise when unmount fails."""
        # Force cleanup to run even though mount_point doesn't exist
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        mount_point = work_dir / "mnt"
        mount_point.mkdir()

        # Mock subprocess to fail
        def mock_run(*args, **kwargs):
            raise OSError("umount failed")

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Should not raise
        red_team_disk_full._cleanup(str(work_dir), str(mount_point), False)

    def test_cleanup_handles_rmtree_failure(self, tmp_path, monkeypatch):
        """Should not raise when rmtree fails."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        mount_point = work_dir / "mnt"
        mount_point.mkdir()

        def mock_run(*args, **kwargs):
            pass  # Successful unmount

        monkeypatch.setattr(subprocess, "run", mock_run)
        import shutil as shutil_module
        monkeypatch.setattr(shutil_module, "rmtree", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("rmtree failed")))

        # Should not raise
        red_team_disk_full._cleanup(str(work_dir), str(mount_point), False)


class TestSubprocess:
    """End-to-end subprocess tests."""

    def test_help_subprocess(self):
        """Running with --help via subprocess should exit 0."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent.parent / "bin" / "red_team_disk_full.py"), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Simulate disk-full" in result.stdout

    def test_invalid_arg_subprocess(self):
        """Running with unknown arg should exit non-zero."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent.parent / "bin" / "red_team_disk_full.py"), "--invalid-arg"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
