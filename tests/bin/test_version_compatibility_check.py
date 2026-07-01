#!/usr/bin/env python3
"""Tests for bin/version_compatibility_check.py — G251 version compatibility checker."""

import subprocess
import sys
from pathlib import Path

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from bin.version_compatibility_check import (
    SUPPORTED_MAX,
    SUPPORTED_MIN,
    build_parser,
    detect_version_from_env,
    detect_version_from_file,
    is_version_supported,
    main,
    parse_version,
    resolve_version,
)

# =============================================================================
# Tests for parse_version
# =============================================================================


class TestParseVersion:
    """Tests for parse_version function."""

    def test_parse_version_with_patch(self):
        """Parse version string with patch number."""
        assert parse_version("1.20.4") == (1, 20, 4)
        assert parse_version("1.21.3") == (1, 21, 3)

    def test_parse_version_without_patch(self):
        """Parse version string without patch number (defaults to 0)."""
        assert parse_version("1.20") == (1, 20, 0)
        assert parse_version("1.21") == (1, 21, 0)

    def test_parse_version_edge_cases(self):
        """Parse edge case version strings."""
        assert parse_version("1.0.0") == (1, 0, 0)
        assert parse_version("0.0.1") == (0, 0, 1)

    def test_parse_version_whitespace(self):
        """Parse version string with surrounding whitespace."""
        assert parse_version("  1.20.4  ") == (1, 20, 4)
        assert parse_version("\t1.21\n") == (1, 21, 0)

    def test_parse_version_invalid(self):
        """Return None for invalid version strings."""
        assert parse_version("") is None
        assert parse_version("invalid") is None
        assert parse_version("1.x.4") is None
        assert parse_version("abc") is None

    def test_parse_version_partial(self):
        """Return None for partial version strings."""
        assert parse_version("1") is None
        assert parse_version("1.") is None
        assert parse_version(".20.4") is None


# =============================================================================
# Tests for is_version_supported
# =============================================================================


class TestIsVersionSupported:
    """Tests for is_version_supported function."""

    def test_supported_min_version(self):
        """Version at minimum supported threshold is supported."""
        assert is_version_supported(SUPPORTED_MIN) is True

    def test_supported_max_version(self):
        """Version at maximum supported threshold is supported."""
        assert is_version_supported(SUPPORTED_MAX) is True

    def test_supported_middle_versions(self):
        """Versions within supported range are supported."""
        assert is_version_supported((1, 20, 4)) is True
        assert is_version_supported((1, 20, 5)) is True
        assert is_version_supported((1, 21, 0)) is True
        assert is_version_supported((1, 21, 99)) is True

    def test_supported_1_21_x(self):
        """All 1.21.x versions are supported."""
        assert is_version_supported((1, 21, 0)) is True
        assert is_version_supported((1, 21, 1)) is True
        assert is_version_supported((1, 21, 50)) is True

    def test_unsupported_below_min(self):
        """Versions below minimum are unsupported."""
        assert is_version_supported((1, 20, 3)) is False
        assert is_version_supported((1, 19, 0)) is False
        assert is_version_supported((1, 19, 99)) is False
        assert is_version_supported((1, 0, 0)) is False
        assert is_version_supported((0, 0, 0)) is False

    def test_unsupported_above_max(self):
        """Versions above maximum are unsupported."""
        assert is_version_supported((2, 0, 0)) is False
        assert is_version_supported((1, 22, 0)) is False
        assert is_version_supported((1, 21, 100)) is False


# =============================================================================
# Tests for detect_version_from_file
# =============================================================================


class TestDetectVersionFromFile:
    """Tests for detect_version_from_file function."""

    def test_read_valid_version_file(self, tmp_path):
        """Read version from a valid file."""
        version_file = tmp_path / "version.txt"
        version_file.write_text("1.20.4\n")
        assert detect_version_from_file(str(version_file)) == "1.20.4"

    def test_read_version_with_extra_lines(self, tmp_path):
        """Read first non-empty line from file with extra content."""
        version_file = tmp_path / "version.txt"
        version_file.write_text("\n\n1.21.0\nsome other content\n")
        assert detect_version_from_file(str(version_file)) == "1.21.0"

    def test_read_empty_file(self, tmp_path):
        """Return None for empty file."""
        version_file = tmp_path / "empty.txt"
        version_file.write_text("")
        assert detect_version_from_file(str(version_file)) is None

    def test_read_nonexistent_file(self):
        """Return None for nonexistent file."""
        assert detect_version_from_file("/nonexistent/file.txt") is None


# =============================================================================
# Tests for detect_version_from_env
# =============================================================================


class TestDetectVersionFromEnv:
    """Tests for detect_version_from_env function."""

    def test_read_from_env_var(self, monkeypatch):
        """Read version from G251_GAME_VERSION env var."""
        monkeypatch.setenv("G251_GAME_VERSION", "1.20.4")
        assert detect_version_from_env() == "1.20.4"

    def test_env_var_not_set(self, monkeypatch):
        """Return None when env var is not set."""
        monkeypatch.delenv("G251_GAME_VERSION", raising=False)
        assert detect_version_from_env() is None

    def test_env_var_empty(self, monkeypatch):
        """Return empty string when env var is empty (actual behavior)."""
        monkeypatch.setenv("G251_GAME_VERSION", "")
        assert detect_version_from_env() == ""


# =============================================================================
# Tests for build_parser
# =============================================================================


class TestBuildParser:
    """Tests for build_parser function."""

    def test_parser_has_version_flag(self):
        """Parser has --version flag."""
        parser = build_parser()
        args = parser.parse_args(["--version", "1.20.4"])
        assert args.version == "1.20.4"

    def test_parser_has_version_file_flag(self, tmp_path):
        """Parser has --version-file flag."""
        version_file = tmp_path / "version.txt"
        version_file.write_text("1.20.4")
        parser = build_parser()
        args = parser.parse_args(["--version-file", str(version_file)])
        assert args.version_file == str(version_file)

    def test_parser_has_auto_flag(self):
        """Parser has --auto flag."""
        parser = build_parser()
        args = parser.parse_args(["--auto"])
        assert args.auto is True

    def test_parser_has_quiet_flag(self):
        """Parser has --quiet flag."""
        parser = build_parser()
        args = parser.parse_args(["--quiet"])
        assert args.quiet is True

    def test_parser_default_quiet_false(self):
        """Parser defaults --quiet to False."""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.quiet is False


# =============================================================================
# Tests for resolve_version
# =============================================================================


class TestResolveVersion:
    """Tests for resolve_version function."""

    def test_resolve_from_version_arg(self):
        """Resolve version from --version argument."""
        parser = build_parser()
        parsed = parser.parse_args(["--version", "1.20.4"])
        assert resolve_version(parsed) == "1.20.4"

    def test_resolve_from_version_file(self, tmp_path):
        """Resolve version from --version-file argument."""
        version_file = tmp_path / "version.txt"
        version_file.write_text("1.21.0")
        parser = build_parser()
        parsed = parser.parse_args(["--version-file", str(version_file)])
        assert resolve_version(parsed) == "1.21.0"

    def test_resolve_from_auto_env(self, monkeypatch):
        """Resolve version from --auto using env var."""
        monkeypatch.setenv("G251_GAME_VERSION", "1.20.4")
        parser = build_parser()
        parsed = parser.parse_args(["--auto"])
        assert resolve_version(parsed) == "1.20.4"

    def test_resolve_returns_none_when_no_input(self):
        """Return None when no version input provided."""
        parser = build_parser()
        parsed = parser.parse_args([])
        assert resolve_version(parsed) is None


# =============================================================================
# Tests for main CLI
# =============================================================================


class TestMain:
    """Tests for main function."""

    def test_main_supported_version(self):
        """Main returns 0 for supported version."""
        result = main(["--version", "1.20.4", "--quiet"])
        assert result == 0

    def test_main_supported_1_21_x(self):
        """Main returns 0 for 1.21.x versions."""
        result = main(["--version", "1.21.0", "--quiet"])
        assert result == 0
        result = main(["--version", "1.21.99", "--quiet"])
        assert result == 0

    def test_main_unsupported_version(self):
        """Main returns 1 for unsupported version."""
        result = main(["--version", "1.19.0", "--quiet"])
        assert result == 1
        result = main(["--version", "2.0.0", "--quiet"])
        assert result == 1

    def test_main_invalid_version_string(self):
        """Main returns 2 for invalid version string."""
        result = main(["--version", "invalid", "--quiet"])
        assert result == 2
        result = main(["--version", "", "--quiet"])
        assert result == 2

    def test_main_no_version_provided(self):
        """Main returns 2 when no version is provided."""
        result = main(["--quiet"])
        assert result == 2


# =============================================================================
# Subprocess end-to-end tests
# =============================================================================


class TestSubprocess:
    """End-to-end tests running the script as subprocess."""

    def test_script_supported_version(self):
        """Script exits 0 for supported version."""
        result = subprocess.run(
            [sys.executable, "bin/version_compatibility_check.py", "--version", "1.20.4", "--quiet"],
            capture_output=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0

    def test_script_supported_1_21(self):
        """Script exits 0 for 1.21.x."""
        result = subprocess.run(
            [sys.executable, "bin/version_compatibility_check.py", "--version", "1.21.0", "--quiet"],
            capture_output=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0

    def test_script_unsupported_version(self):
        """Script exits 1 for unsupported version."""
        result = subprocess.run(
            [sys.executable, "bin/version_compatibility_check.py", "--version", "1.19.0", "--quiet"],
            capture_output=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 1

    def test_script_invalid_version(self):
        """Script exits 2 for invalid version."""
        result = subprocess.run(
            [sys.executable, "bin/version_compatibility_check.py", "--version", "bad", "--quiet"],
            capture_output=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 2

    def test_script_no_version(self):
        """Script exits 2 when no version provided."""
        result = subprocess.run(
            [sys.executable, "bin/version_compatibility_check.py", "--quiet"],
            capture_output=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 2

    def test_script_help(self):
        """Script shows help with --help."""
        result = subprocess.run(
            [sys.executable, "bin/version_compatibility_check.py", "--help"],
            capture_output=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0
        assert b"--version" in result.stdout
        assert b"--version-file" in result.stdout
        assert b"--auto" in result.stdout

    def test_script_version_file(self, tmp_path):
        """Script reads version from file."""
        version_file = tmp_path / "version.txt"
        version_file.write_text("1.20.4")
        result = subprocess.run(
            [sys.executable, "bin/version_compatibility_check.py", "--version-file", str(version_file), "--quiet"],
            capture_output=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0
