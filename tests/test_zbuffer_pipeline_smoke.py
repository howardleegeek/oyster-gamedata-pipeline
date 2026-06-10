"""Tests for the zbuffer pipeline smoke script."""

import subprocess
import sys

import pytest


def _openexr_available() -> bool:
    try:
        import OpenEXR  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _openexr_available(), reason="OpenEXR not installed")
def test_smoke_script_passes():
    """Run the smoke script and assert it exits 0 with PASS in stdout."""
    result = subprocess.run(
        [sys.executable, "bin/zbuffer_pipeline_smoke.py"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Smoke script failed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "PASS" in result.stdout, f"'PASS' not found in smoke output:\n{result.stdout}"
