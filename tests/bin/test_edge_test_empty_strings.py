#!/usr/bin/env python3
"""Tests for bin/edge_test_empty_strings.py — Boundary test for empty-string required fields.

Verifies that required string fields (e.g. route_type) reject empty
strings with a fail-closed posture.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path to the edge test script (in parent bin/ directory)
EDGE_TEST_SCRIPT = Path(__file__).parent.parent.parent / "bin" / "edge_test_empty_strings.py"


class TestEdgeTestEmptyStrings:
    """Test suite for edge_test_empty_strings.py."""

    def test_script_exists(self):
        """Verify the edge test script exists and is executable."""
        assert EDGE_TEST_SCRIPT.exists(), f"Script not found: {EDGE_TEST_SCRIPT}"

    def test_runs_successfully(self):
        """Verify the edge test runs and exits with success (0)."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

    def test_verbose_mode(self):
        """Verify verbose mode works without errors."""
        result = subprocess.run(
            [sys.executable, str(EDGE_TEST_SCRIPT), "--verbose"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Verbose mode failed: {result.stderr}"
        # Verify expected output markers are present
        assert "REJECTED" in result.stdout or "OK" in result.stdout

    def test_rejects_empty_string_cli_args(self):
        """Verify empty string is rejected for cli_args payload."""
        # Import the module to test internal validation
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_empty_strings import (
            _cli_args_payload,
            _validate_required_string,
        )

        payload = _cli_args_payload("")
        is_valid = _validate_required_string(payload, "route_type")
        assert not is_valid, "Empty string should be rejected (fail-closed)"

    def test_rejects_empty_string_json_body(self):
        """Verify empty string is rejected for json_body payload."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_empty_strings import (
            _json_payload,
            _validate_required_string,
        )

        payload = _json_payload("")
        is_valid = _validate_required_string(payload, "route_type")
        assert not is_valid, "Empty string should be rejected (fail-closed)"

    def test_rejects_empty_string_yaml_config(self):
        """Verify empty string is rejected for yaml_config payload."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_empty_strings import _validate_required_string, _yaml_payload

        payload = _yaml_payload("")
        is_valid = _validate_required_string(payload, "route_type")
        assert not is_valid, "Empty string should be rejected (fail-closed)"

    def test_rejects_whitespace_only(self):
        """Verify whitespace-only string is rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_empty_strings import _validate_required_string, _whitespace_payload

        payload = _whitespace_payload("")
        is_valid = _validate_required_string(payload, "route_type")
        assert not is_valid, "Whitespace-only string should be rejected"

    def test_accepts_valid_non_empty_string(self):
        """Verify valid non-empty string is accepted."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_empty_strings import _validate_required_string

        valid_payloads = [
            {"route_type": "express", "source": "test"},
            {"route_type": "standard", "source": "test"},
            {"route_type": "a", "source": "test"},
            {"route_type": "  trimmed  ", "source": "test"},
        ]
        for payload in valid_payloads:
            is_valid = _validate_required_string(payload, "route_type")
            assert is_valid, f"Valid payload should be accepted: {payload}"

    def test_rejects_missing_field(self):
        """Verify missing field is rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_empty_strings import _validate_required_string

        payload = {"source": "test"}  # route_type missing
        is_valid = _validate_required_string(payload, "route_type")
        assert not is_valid, "Missing field should be rejected"

    def test_rejects_non_string_value(self):
        """Verify non-string values are rejected."""
        sys.path.insert(0, str(EDGE_TEST_SCRIPT.parent))
        from edge_test_empty_strings import _validate_required_string

        invalid_payloads = [
            {"route_type": None, "source": "test"},
            {"route_type": 123, "source": "test"},
            {"route_type": [], "source": "test"},
            {"route_type": {}, "source": "test"},
        ]
        for payload in invalid_payloads:
            is_valid = _validate_required_string(payload, "route_type")
            assert not is_valid, f"Non-string should be rejected: {payload}"
