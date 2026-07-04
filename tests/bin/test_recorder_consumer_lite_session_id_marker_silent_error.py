#!/usr/bin/env python3
"""
Regression test: _read_session_id_marker should surface errors, not swallow silently.

Round 280: Surface silent error in bin/recorder_consumer_lite.py _read_session_id_marker
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the function under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bin"))
from recorder_consumer_lite import _read_session_id_marker


class TestReadSessionIdMarkerSilentError:
    """Test that _read_session_id_marker surfaces errors rather than swallowing them."""

    def test_corrupt_json_logs_via_trace_and_returns_empty_dict(self, tmp_path):
        """Regression: corrupt JSON should be surfaced via _trace, not silently swallowed."""
        marker = tmp_path / ".session_id"
        marker.write_text("{invalid json", encoding="utf-8")

        # Mock _trace to verify it's called with error info
        with patch("recorder_consumer_lite._trace") as mock_trace:
            result = _read_session_id_marker(tmp_path)

        # Control flow unchanged: returns empty dict on error
        assert result == {}

        # Verify _trace was called with error info
        mock_trace.assert_called()
        call_args = str(mock_trace.call_args)
        # Should contain error info
        assert "failed" in call_args.lower() or "error" in call_args.lower() or "JSONDecodeError" in call_args

    def test_missing_marker_returns_empty_dict(self, tmp_path):
        """Missing marker file should return empty dict."""
        result = _read_session_id_marker(tmp_path)
        assert result == {}

    def test_valid_json_returns_dict(self, tmp_path):
        """Valid JSON should be returned as dict."""
        marker = tmp_path / ".session_id"
        data = {"session_id": "test-123", "started_at": "2026-01-01T00:00:00Z"}
        marker.write_text(json.dumps(data), encoding="utf-8")

        result = _read_session_id_marker(tmp_path)
        assert result == data

    def test_non_dict_json_returns_empty_dict(self, tmp_path):
        """JSON that isn't a dict should return empty dict."""
        marker = tmp_path / ".session_id"
        marker.write_text("[1, 2, 3]", encoding="utf-8")

        result = _read_session_id_marker(tmp_path)
        assert result == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
