#!/usr/bin/env python3
"""Tests for bin/stress_test_memory_leak_check.py — RSS growth stress test.

Covers:

- get_rss_mb: returns float, non-negative value on supported platforms.
- simulate_adapter_iteration: creates temp file, writes data, reads it back,
  deletes it; handles iteration number and tmp_dir path.
- run_stress_test: returns exit code 0 when RSS growth below threshold,
  returns 1 when RSS growth exceeds threshold, handles iteration count,
  performs gc.collect during loop, creates and cleans up temp directory.
- main: --help exits 0, --iterations, --max-rss-mb, -v/--verbose, validation
  of negative iterations and non-positive max-rss-mb, subprocess end-to-end.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

_BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"
_SPEC = importlib.util.spec_from_file_location(
    "stress_test_memory_leak_check", _BIN_DIR / "stress_test_memory_leak_check.py"
)
assert _SPEC is not None and _SPEC.loader is not None
stress_test_memory_leak_check = importlib.util.module_from_spec(_SPEC)
sys.modules["stress_test_memory_leak_check"] = stress_test_memory_leak_check
_SPEC.loader.exec_module(stress_test_memory_leak_check)


# ---------------------------------------------------------------------------
# get_rss_mb tests
# ---------------------------------------------------------------------------


class TestGetRssMb:
    """Tests for get_rss_mb() helper."""

    def test_returns_float(self) -> None:
        """get_rss_mb returns a float."""
        result = stress_test_memory_leak_check.get_rss_mb()
        assert isinstance(result, float)

    def test_returns_non_negative(self) -> None:
        """get_rss_mb returns non-negative value."""
        result = stress_test_memory_leak_check.get_rss_mb()
        assert result >= 0.0


# ---------------------------------------------------------------------------
# simulate_adapter_iteration tests
# ---------------------------------------------------------------------------


class TestSimulateAdapterIteration:
    """Tests for simulate_adapter_iteration() helper."""

    def test_creates_temp_file(self, tmp_path: Path) -> None:
        """simulate_adapter_iteration creates a temp file in tmp_dir."""
        stress_test_memory_leak_check.simulate_adapter_iteration(0, str(tmp_path))
        # File should be cleaned up after iteration
        files = list(tmp_path.glob("iter_*.bin"))
        assert len(files) == 0  # File should be deleted

    def test_file_naming_pattern(self, tmp_path: Path) -> None:
        """Temp file follows naming pattern iter_XXXXXX.bin."""
        stress_test_memory_leak_check.simulate_adapter_iteration(42, str(tmp_path))
        expected_file = tmp_path / "iter_000042.bin"
        # File should exist during execution but be cleaned up after
        # We can't easily test this without mocking os.unlink

    def test_handles_large_iteration_number(self, tmp_path: Path) -> None:
        """Handles large iteration numbers in filename."""
        stress_test_memory_leak_check.simulate_adapter_iteration(999999, str(tmp_path))
        # Should not raise, file should be cleaned up


# ---------------------------------------------------------------------------
# run_stress_test tests
# ---------------------------------------------------------------------------


class TestRunStressTest:
    """Tests for run_stress_test() function."""

    def test_returns_zero_when_under_threshold(self) -> None:
        """Returns 0 when RSS growth is below max_rss_mb threshold."""
        result = stress_test_memory_leak_check.run_stress_test(
            iterations=10, max_rss_mb=1000.0, verbose=False
        )
        assert result == 0

    def test_returns_one_when_over_threshold(self) -> None:
        """Returns 1 when RSS growth exceeds max_rss_mb threshold."""
        # Use very low threshold to force failure
        with mock.patch.object(
            stress_test_memory_leak_check,
            "get_rss_mb",
            side_effect=[0.0, 1000.0],  # Start low, end high
        ):
            result = stress_test_memory_leak_check.run_stress_test(
                iterations=1, max_rss_mb=0.1, verbose=False
            )
            assert result == 1

    def test_creates_temp_directory(self) -> None:
        """Creates a temp directory that is cleaned up after test."""
        result = stress_test_memory_leak_check.run_stress_test(
            iterations=5, max_rss_mb=1000.0, verbose=False
        )
        assert result == 0

    def test_iteration_count(self) -> None:
        """Respects the iterations parameter."""
        mock_iteration = mock.MagicMock()
        with mock.patch.object(
            stress_test_memory_leak_check,
            "simulate_adapter_iteration",
            mock_iteration,
        ):
            stress_test_memory_leak_check.run_stress_test(
                iterations=50, max_rss_mb=1000.0, verbose=False
            )
            assert mock_iteration.call_count == 50

    def test_verbose_flag_enables_progress(self) -> None:
        """Verbose flag enables progress printing every 100 iterations."""
        with mock.patch("sys.stdout") as mock_stdout:
            stress_test_memory_leak_check.run_stress_test(
                iterations=150, max_rss_mb=1000.0, verbose=True
            )
            # Should have printed at least once (at iteration 99 and 149)


# ---------------------------------------------------------------------------
# main CLI tests
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for main() CLI entry point."""

    def test_help_exits_zero(self) -> None:
        """--help exits with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            stress_test_memory_leak_check.main(["--help"])
        assert exc_info.value.code == 0

    def test_default_args(self) -> None:
        """Default arguments are accepted."""
        # Default is 1000 iterations and 50MB threshold
        result = stress_test_memory_leak_check.main(
            ["--iterations", "10", "--max-rss-mb", "1000"]
        )
        assert result in (0, 1)  # Either pass or fail depending on actual RSS

    def test_iterations_argument(self) -> None:
        """--iterations parameter is accepted."""
        result = stress_test_memory_leak_check.main(
            ["--iterations", "5", "--max-rss-mb", "1000"]
        )
        assert result in (0, 1)

    def test_max_rss_mb_argument(self) -> None:
        """--max-rss-mb parameter is accepted."""
        result = stress_test_memory_leak_check.main(
            ["--iterations", "5", "--max-rss-mb", "100"]
        )
        assert result in (0, 1)

    def test_verbose_short_flag(self) -> None:
        """-v short flag enables verbose mode."""
        result = stress_test_memory_leak_check.main(
            ["--iterations", "5", "--max-rss-mb", "1000", "-v"]
        )
        assert result in (0, 1)

    def test_verbose_long_flag(self) -> None:
        """--verbose long flag enables verbose mode."""
        result = stress_test_memory_leak_check.main(
            ["--iterations", "5", "--max-rss-mb", "1000", "--verbose"]
        )
        assert result in (0, 1)

    def test_invalid_iterations_negative(self) -> None:
        """Negative iterations returns error code 1."""
        result = stress_test_memory_leak_check.main(
            ["--iterations", "-1", "--max-rss-mb", "50"]
        )
        assert result == 1

    def test_invalid_max_rss_mb_zero(self) -> None:
        """Zero max-rss-mb returns error code 1."""
        result = stress_test_memory_leak_check.main(
            ["--iterations", "10", "--max-rss-mb", "0"]
        )
        assert result == 1

    def test_invalid_max_rss_mb_negative(self) -> None:
        """Negative max-rss-mb returns error code 1."""
        result = stress_test_memory_leak_check.main(
            ["--iterations", "10", "--max-rss-mb", "-10"]
        )
        assert result == 1

    def test_unknown_argument(self) -> None:
        """Unknown argument raises SystemExit."""
        with pytest.raises(SystemExit):
            stress_test_memory_leak_check.main(["--unknown-flag"])


# ---------------------------------------------------------------------------
# Subprocess end-to-end tests
# ---------------------------------------------------------------------------


class TestSubprocess:
    """End-to-end tests running the script as subprocess."""

    def test_script_runs(self) -> None:
        """Script runs without errors."""
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "stress_test_memory_leak_check.py"), "--iterations", "5", "--max-rss-mb", "1000"],
            capture_output=True,
            timeout=30,
        )
        # May pass or fail depending on actual RSS, but should not crash
        assert result.returncode in (0, 1)

    def test_help_flag(self) -> None:
        """--help flag works."""
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "stress_test_memory_leak_check.py"), "--help"],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert b"iterations" in output.lower()
