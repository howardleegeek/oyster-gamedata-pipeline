#!/usr/bin/env python3
"""Tests for bin/prd_test_30min_scene_cap.py"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_30min_scene_cap.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_default_passes_instantly():
    """Default invocation should pass immediately (no sleep)."""
    result = _run([])
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert "OK" in result.stdout


def test_duration_under_threshold_passes():
    """A 10-minute simulated duration should pass the 30-minute cap."""
    result = _run(["--duration", "10"])
    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_duration_over_threshold_fails():
    """A 35-minute simulated duration should fail the 30-minute cap."""
    result = _run(["--duration", "35"])
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert "EXCEEDED" in result.stdout


def test_duration_at_warning_level():
    """A 25-minute duration (83% of 30) should trigger warning."""
    result = _run(["--duration", "25"])
    assert result.returncode == 0
    assert "WARNING" in result.stdout


def test_json_output():
    """--json flag should produce valid JSON."""
    import json
    result = _run(["--duration", "10", "--json"])
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["duration_minutes"] == 10.0
    assert data["threshold_minutes"] == 30.0
    assert data["exceeded"] is False


def test_custom_threshold():
    """Custom threshold should be respected."""
    result = _run(["--duration", "5", "--threshold", "3"])
    assert result.returncode == 1
    assert "EXCEEDED" in result.stdout


def test_no_actual_sleep():
    """The test should complete in under 1 second (no real sleep)."""
    import time
    start = time.time()
    _run(["--duration", "0"])
    elapsed = time.time() - start
    assert elapsed < 1.0, f"Test took {elapsed:.2f}s — should be instant"
