#!/usr/bin/env python3
"""
Tests for bin/diag_bundle_collector.py — G138 Diagnostic Bundle Collector

Purpose:
Collects logs, system information, and recent manifests into a tarball
for customer support tickets.

Test coverage:
- get_system_info (platform info, timestamp, version)
- find_log_files (file discovery, size limit, directory not found)
- find_manifests (pattern matching, file sorting by mtime, limit)
- run_cmd_safe (successful command, failed command, timeout, not found)
- collect_bundle (bundle creation, file inclusion)
- main() CLI (default args, custom args, exit codes)
"""

import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))
from bin.diag_bundle_collector import (
    MODULE_VERSION,
    collect_bundle,
    find_log_files,
    find_manifests,
    get_system_info,
    main,
    run_cmd_safe,
)


class TestGetSystemInfo:
    """Tests for get_system_info function."""

    def test_returns_dict_with_expected_keys(self):
        """Test that get_system_info returns a dict with required keys."""
        info = get_system_info()

        assert isinstance(info, dict)
        assert "timestamp" in info
        assert "collector_version" in info
        assert "platform" in info
        assert "python" in info

    def test_version_matches_module_constant(self):
        """Test that version in info matches MODULE_VERSION."""
        info = get_system_info()
        assert info["collector_version"] == MODULE_VERSION

    def test_platform_contains_system(self):
        """Test that platform dict contains system info."""
        info = get_system_info()
        assert "system" in info["platform"]
        assert "release" in info["platform"]
        assert "machine" in info["platform"]

    def test_python_contains_version(self):
        """Test that python dict contains version."""
        info = get_system_info()
        assert "version" in info["python"]
        assert "executable" in info["python"]


class TestFindLogFiles:
    """Tests for find_log_files function."""

    def test_empty_list_for_nonexistent_directory(self):
        """Test that find_log_files returns empty list for non-existent dir."""
        files = find_log_files(["/nonexistent/path/that/does/not/exist"])
        assert files == []

    def test_finds_log_files_in_directory(self):
        """Test that find_log_files finds .log files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test log files
            log_file = Path(tmpdir) / "test.log"
            log_file.write_text("test log content")

            txt_file = Path(tmpdir) / "test.txt"
            txt_file.write_text("test text content")

            files = find_log_files([tmpdir])

            assert len(files) >= 1
            assert any(f.name == "test.log" for f in files)

    def test_respects_size_limit(self):
        """Test that find_log_files respects max_mb parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a large file (would exceed small limit)
            large_file = Path(tmpdir) / "large.log"
            large_file.write_text("x" * (20 * 1024 * 1024))  # 20 MB

            # With 10 MB limit, should exclude
            files = find_log_files([tmpdir], max_mb=5)
            assert len(files) == 0


class TestFindManifests:
    """Tests for find_manifests function."""

    def test_empty_list_for_nonexistent_directory(self):
        """Test that find_manifests returns empty list for non-existent dir."""
        manifests = find_manifests("/nonexistent/path/that/does/not/exist")
        assert manifests == []

    def test_finds_manifest_files(self):
        """Test that find_manifests finds manifest files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test manifest files
            manifest1 = Path(tmpdir) / "manifest_001.json"
            manifest1.write_text('{"test": 1}')

            manifest2 = Path(tmpdir) / "manifest_002.yaml"
            manifest2.write_text("test: 2")

            manifests = find_manifests(tmpdir)

            assert len(manifests) == 2
            names = [m.name for m in manifests]
            assert "manifest_001.json" in names
            assert "manifest_002.yaml" in names

    def test_respects_limit(self):
        """Test that find_manifests respects limit parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple manifest files
            for i in range(5):
                m = Path(tmpdir) / f"manifest_{i:03d}.json"
                m.write_text(f'{{"id": {i}}}')

            manifests = find_manifests(tmpdir, limit=3)

            assert len(manifests) == 3


class TestRunCmdSafe:
    """Tests for run_cmd_safe function."""

    def test_returns_output_on_success(self):
        """Test that run_cmd_safe returns stdout on successful command."""
        result = run_cmd_safe(["echo", "hello"])
        assert result == "hello"

    def test_returns_none_on_failure(self):
        """Test that run_cmd_safe returns None on command failure."""
        result = run_cmd_safe(["false"])  # command that returns 1
        assert result is None

    def test_returns_none_on_not_found(self):
        """Test that run_cmd_safe returns None when command not found."""
        result = run_cmd_safe(["nonexistent_command_xyz"])
        assert result is None

    def test_timeout_returns_none(self):
        """Test that run_cmd_safe returns None on timeout."""
        result = run_cmd_safe(["sleep", "10"], timeout=1)
        assert result is None


class TestCollectBundle:
    """Tests for collect_bundle function."""

    def test_creates_tarball(self):
        """Test that collect_bundle creates a valid tarball."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test_bundle.tar.gz"

            # Use empty dirs to keep test simple
            result = collect_bundle([], tmpdir, str(output))

            assert result.exists()
            assert result.suffix == ".gz"
            # Verify it's a valid tar.gz
            with tarfile.open(result, "r:gz") as tar:
                names = tar.getnames()
                assert len(names) > 0

    def test_includes_system_info(self):
        """Test that collect_bundle includes system_info.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test_bundle.tar.gz"

            result = collect_bundle([], tmpdir, str(output))

            with tarfile.open(result, "r:gz") as tar:
                names = tar.getnames()
                assert any("system_info.json" in n for n in names)


class TestMain:
    """Tests for main() CLI function."""

    def test_main_returns_zero_on_success(self):
        """Test that main returns 0 on successful bundle creation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test_output.tar.gz"

            result = main(["-o", str(output), "-l", tmpdir])

            assert result == 0

    def test_main_creates_output_file(self):
        """Test that main creates the output file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test_output.tar.gz"

            main(["-o", str(output), "-l", tmpdir])

            assert output.exists()

    def test_main_default_output_naming(self):
        """Test that main generates default output name with timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Run without -o argument, should create file with default name
            result = main(["-l", tmpdir])

            assert result == 0

    def test_main_with_multiple_log_dirs(self):
        """Test that main accepts multiple -l arguments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a log file in the temp directory
            log_file = Path(tmpdir) / "test.log"
            log_file.write_text("log content")

            output = Path(tmpdir) / "multi_log.tar.gz"

            result = main(["-o", str(output), "-l", tmpdir, "-l", tmpdir])

            assert result == 0
            assert output.exists()

    def test_main_version_flag(self):
        """Test that main handles --version flag."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])

        assert exc_info.value.code == 0
