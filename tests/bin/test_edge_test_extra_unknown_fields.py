#!/usr/bin/env python3
"""Tests for bin/edge_test_extra_unknown_fields.py — Boundary test for extra unknown fields.

Verifies that vendor-added extra keys to an action_camera record generate
warnings but are still accepted (fail-open for vendor extensions).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "edge_test_extra_unknown_fields.py"


class TestEdgeTestExtraUnknownFields:
    """Test suite for edge_test_extra_unknown_fields.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_successfully(self):
        """Verify the edge test runs and exits with success (0) when not strict."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "ACCEPTED" in result.stdout, "Expected ACCEPTED status in output"

    def test_verbose_mode(self):
        """Verify verbose mode works without errors."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Verbose mode failed: {result.stderr}"
        assert "vendor_sku" in result.stdout, "Expected vendor field in verbose output"

    def test_strict_mode_rejects(self):
        """Verify strict mode rejects records with unknown fields."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--strict"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, f"Strict mode should reject: {result.stderr}"
        assert "REJECTED" in result.stdout, "Expected REJECTED status in strict mode output"

    def test_warns_about_unknown_fields(self):
        """Verify warnings are generated for unknown fields."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert "WARNING: unknown field" in result.stdout, "Expected warning for unknown fields"
        assert "vendor_sku" in result.stdout, "Expected vendor_sku in warnings"

    def test_internal_lint_function(self):
        """Test the internal lint_action_camera function."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_extra_unknown_fields import (
            build_sample_record,
            lint_action_camera,
        )

        record = build_sample_record()

        # Non-strict mode should accept
        accepted, messages = lint_action_camera(record, strict=False)
        assert accepted, "Non-strict mode should accept records with unknown fields"
        assert any("unknown field" in msg for msg in messages), "Should warn about unknown fields"

        # Strict mode should reject
        accepted_strict, messages_strict = lint_action_camera(record, strict=True)
        assert not accepted_strict, "Strict mode should reject records with unknown fields"

    def test_sample_record_has_vendor_fields(self):
        """Verify sample record includes vendor-specific extensions."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_extra_unknown_fields import build_sample_record

        record = build_sample_record()
        # Known fields
        assert "brand" in record
        assert "model" in record
        # Vendor extensions
        assert "vendor_sku" in record
        assert "firmware_version" in record
        assert record["vendor_sku"] == "GP-H12B-2024"
