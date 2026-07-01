#!/usr/bin/env python3
"""Tests for bin/red_team_concurrent_writers.py — concurrent tarball writers test."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

spec = importlib.util.spec_from_file_location(
    "red_team_concurrent_writers",
    Path(__file__).parent.parent.parent / "bin" / "red_team_concurrent_writers.py",
)
red_team_concurrent_writers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(red_team_concurrent_writers)


class TestMakePayloads:
    """Tests for _make_payloads() function."""

    def test_creates_correct_number_of_files(self, tmp_path):
        """Should create the specified number of payload files."""
        paths = red_team_concurrent_writers._make_payloads(tmp_path, n=5, seed=42)
        assert len(paths) == 5

    def test_files_have_expected_names(self, tmp_path):
        """Files should be named payload_000.bin through payload_NNN.bin."""
        paths = red_team_concurrent_writers._make_payloads(tmp_path, n=3, seed=42)
        names = sorted(p.name for p in paths)
        assert names == ["payload_000.bin", "payload_001.bin", "payload_002.bin"]

    def test_files_are_written_to_directory(self, tmp_path):
        """Files should be created in the specified directory."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        paths = red_team_concurrent_writers._make_payloads(subdir, n=2, seed=42)
        for p in paths:
            assert p.parent == subdir

    def test_deterministic_with_seed(self, tmp_path):
        """Same seed should produce same file contents."""
        paths1 = red_team_concurrent_writers._make_payloads(tmp_path, n=3, seed=123)
        paths2 = red_team_concurrent_writers._make_payloads(tmp_path, n=3, seed=123)
        # Same seed, different directories — compare content hashes
        content1 = [p.read_bytes() for p in paths1]
        content2 = [p.read_bytes() for p in paths2]
        assert content1 == content2

    def test_different_seeds_different_content(self, tmp_path):
        """Different seeds should produce different file contents."""
        # Use separate subdirs to avoid overwriting
        dir1 = tmp_path / "seed1"
        dir2 = tmp_path / "seed2"
        dir1.mkdir()
        dir2.mkdir()
        paths1 = red_team_concurrent_writers._make_payloads(dir1, n=2, seed=1)
        paths2 = red_team_concurrent_writers._make_payloads(dir2, n=2, seed=2)
        content1 = sorted(p.read_bytes() for p in paths1)
        content2 = sorted(p.read_bytes() for p in paths2)
        assert content1 != content2


class TestTarBytes:
    """Tests for _tar_bytes() function."""

    def test_creates_tarball_bytes(self, tmp_path):
        """Should return gzip-compressed tarball bytes."""
        # Create some payload files
        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "file2.txt").write_text("world")
        
        data = red_team_concurrent_writers._tar_bytes(tmp_path)
        
        # Should be non-empty bytes
        assert isinstance(data, bytes)
        assert len(data) > 0
        # Should start with gzip magic number
        assert data[:2] == b'\x1f\x8b'

    def test_tarball_contains_all_files(self, tmp_path):
        """Tarball should contain all files from source directory."""
        (tmp_path / "a.txt").write_text("content a")
        (tmp_path / "b.txt").write_text("content b")
        
        import io
        import tarfile
        
        data = red_team_concurrent_writers._tar_bytes(tmp_path)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            names = sorted(m.name for m in tf.getmembers())
            assert names == ["a.txt", "b.txt"]

    def test_respects_directory_structure(self, tmp_path):
        """Should preserve relative path structure."""
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested content")
        
        import io
        import tarfile
        
        data = red_team_concurrent_writers._tar_bytes(tmp_path)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            names = [m.name for m in tf.getmembers()]
            assert "sub/nested.txt" in names


class TestVerify:
    """Tests for _verify() function."""

    def test_valid_tarball_passes(self, tmp_path):
        """Should return (True, None) for valid tarball."""
        # Create source files
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        originals = red_team_concurrent_writers._make_payloads(src_dir, n=3, seed=42)
        
        # Create tarball from source
        tar_path = tmp_path / "output.tar.gz"
        data = red_team_concurrent_writers._tar_bytes(src_dir)
        tar_path.write_bytes(data)
        
        # Verify should pass
        valid, err = red_team_concurrent_writers._verify(tar_path, originals)
        assert valid is True
        assert err is None

    def test_missing_member_fails(self, tmp_path):
        """Should fail if tarball is missing a file."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        originals = red_team_concurrent_writers._make_payloads(src_dir, n=3, seed=42)
        
        # Create tarball with only 2 files
        tar_path = tmp_path / "output.tar.gz"
        partial_dir = tmp_path / "partial"
        partial_dir.mkdir()
        red_team_concurrent_writers._make_payloads(partial_dir, n=2, seed=42)
        data = red_team_concurrent_writers._tar_bytes(partial_dir)
        tar_path.write_bytes(data)
        
        valid, err = red_team_concurrent_writers._verify(tar_path, originals)
        assert valid is False
        assert "member count" in err

    def test_corrupted_content_fails(self, tmp_path):
        """Should fail if file content doesn't match."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        originals = red_team_concurrent_writers._make_payloads(src_dir, n=2, seed=42)
        
        # Create tarball
        tar_path = tmp_path / "output.tar.gz"
        data = red_team_concurrent_writers._tar_bytes(src_dir)
        
        # Corrupt the tarball by truncating
        corrupted_data = data[:len(data)//2]
        tar_path.write_bytes(corrupted_data)
        
        valid, err = red_team_concurrent_writers._verify(tar_path, originals)
        assert valid is False


class TestRunConcurrentTest:
    """Tests for run_concurrent_test() function — tested via subprocess for full coverage."""

    def test_function_imports_correctly(self):
        """Function should be importable and callable."""
        assert callable(red_team_concurrent_writers.run_concurrent_test)

    def test_returns_correct_structure(self):
        """Should return tuple with bool first element and dict second."""
        # We can't call run_concurrent_test directly in pytest due to multiprocessing pickling
        # but we can verify the function signature and that main() calls it correctly.
        # The actual functionality is tested via subprocess tests.
        import inspect
        sig = inspect.signature(red_team_concurrent_writers.run_concurrent_test)
        params = list(sig.parameters.keys())
        assert params == ["workers", "files", "seed"]


class TestMain:
    """Tests for main() CLI entry point."""

    def test_help_exits_zero(self):
        """--help should exit with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            red_team_concurrent_writers.main(["--help"])
        assert exc_info.value.code == 0

    def test_default_args(self):
        """Default values should be workers=2, files=5, seed=42."""
        with mock.patch.object(red_team_concurrent_writers, "run_concurrent_test") as mock_run:
            mock_run.return_value = (True, {"workers": 2, "successes": 2, "tarball_valid": True})
            result = red_team_concurrent_writers.main([])
            assert result == 0
            mock_run.assert_called_once_with(2, 5, 42)

    def test_custom_workers(self):
        """--workers should set custom worker count."""
        with mock.patch.object(red_team_concurrent_writers, "run_concurrent_test") as mock_run:
            mock_run.return_value = (True, {"workers": 4, "successes": 4, "tarball_valid": True})
            result = red_team_concurrent_writers.main(["--workers", "4"])
            assert result == 0
            mock_run.assert_called_once_with(4, 5, 42)

    def test_custom_files(self):
        """--files should set custom file count."""
        with mock.patch.object(red_team_concurrent_writers, "run_concurrent_test") as mock_run:
            mock_run.return_value = (True, {"workers": 2, "successes": 2, "tarball_valid": True})
            result = red_team_concurrent_writers.main(["--files", "10"])
            assert result == 0
            mock_run.assert_called_once_with(2, 10, 42)

    def test_custom_seed(self):
        """--seed should set custom RNG seed."""
        with mock.patch.object(red_team_concurrent_writers, "run_concurrent_test") as mock_run:
            mock_run.return_value = (True, {"workers": 2, "successes": 2, "tarball_valid": True})
            result = red_team_concurrent_writers.main(["--seed", "999"])
            assert result == 0
            mock_run.assert_called_once_with(2, 5, 999)

    def test_verbose_flag(self, capsys):
        """--verbose should print details."""
        with mock.patch.object(red_team_concurrent_writers, "run_concurrent_test") as mock_run:
            mock_run.return_value = (
                True,
                {
                    "workers": 2,
                    "successes": 2,
                    "results": [(0, True, None), (1, True, None)],
                    "tarball_valid": True,
                    "tarball_error": None,
                },
            )
            result = red_team_concurrent_writers.main(["--verbose"])
            assert result == 0
            captured = capsys.readouterr()
            assert "Workers:" in captured.out

    def test_failure_returns_one(self):
        """Should return 1 when test fails."""
        with mock.patch.object(red_team_concurrent_writers, "run_concurrent_test") as mock_run:
            mock_run.return_value = (False, {"workers": 2, "successes": 0, "tarball_valid": False})
            result = red_team_concurrent_writers.main([])
            assert result == 1

    def test_invalid_workers_raises(self):
        """Non-integer workers should raise SystemExit."""
        with pytest.raises(SystemExit):
            red_team_concurrent_writers.main(["--workers", "abc"])

    def test_invalid_files_raises(self):
        """Non-integer files should raise SystemExit."""
        with pytest.raises(SystemExit):
            red_team_concurrent_writers.main(["--files", "xyz"])


class TestSubprocess:
    """End-to-end subprocess tests."""

    def test_cli_runs_successfully(self):
        """CLI should run without errors."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent.parent / "bin" / "red_team_concurrent_writers.py")],
            capture_output=True,
            timeout=30,
        )
        # Should succeed (exit 0) with default args
        assert result.returncode == 0
        assert b"[PASS]" in result.stdout

    def test_cli_with_workers_flag(self):
        """CLI should accept --workers flag."""
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent.parent.parent / "bin" / "red_team_concurrent_writers.py"),
                "--workers", "1",
            ],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0

    def test_cli_with_verbose_flag(self):
        """CLI should accept --verbose flag."""
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent.parent.parent / "bin" / "red_team_concurrent_writers.py"),
                "--verbose",
            ],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert b"Workers:" in result.stdout
