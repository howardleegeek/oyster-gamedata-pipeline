"""Tests for bin/verify_action_camera.py layer3_behavioral timestamp parsing.

These tests cover the silent-error-swallow fix in the frame-timestamp
continuity check: bad or missing 'time' fields must be surfaced as
issues (not silently dropped), and the operator must be able to tell
*why* a frame was rejected.
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from bin.verify_action_camera import layer3_behavioral  # noqa: E402


def _good_records(n: int = 10) -> list[dict[str, Any]]:
    """10 records with monotonic ~33ms-spaced timestamps, all valid."""
    return [
        {
            "time": f"2026-01-01 00:00:00.{i * 33:03d}",
            "mouse_dx": 0.0,
            "camera_rotation_euler": [0.0, 0.0, 0.0],
        }
        for i in range(n)
    ]


class TestLayer3TimestampParsing:
    def test_all_valid_passes(self) -> None:
        """All timestamps valid → passed=True, timestamps_bad=0."""
        result = layer3_behavioral(_good_records(10))
        assert result["passed"] is True
        assert result["issue_count"] == 0
        assert result["stats"]["timestamps_bad"] == 0
        assert result["stats"]["timestamps_parsed"] == 10

    def test_missing_time_field_surfaces_as_issue(self) -> None:
        """A record with no 'time' key must be flagged, not silently dropped.

        Regression test: previously, r["time"] would raise KeyError → caught
        by `except Exception: pass` → record silently disappeared from the
        gap check, giving the operator a false-positive "all pass".
        """
        records = _good_records(5)
        # 1 record uses 'timestamp' (float) instead of 'time' (str)
        records.append({"timestamp": 0.165, "camera_rotation_euler": [0.0, 0.0, 0.0]})
        result = layer3_behavioral(records)
        assert result["passed"] is False
        assert result["stats"]["timestamps_bad"] == 1
        assert result["stats"]["timestamps_parsed"] == 5
        # The issue string must mention the cause so the operator can act.
        joined = " ".join(result["first_issues"])
        assert "missing_field" in joined
        assert "unparseable 'time' field" in joined

    def test_unparseable_time_surfaces_as_issue(self) -> None:
        """A record with garbage in 'time' must be flagged, not silently dropped.

        Regression test: previously, strptime would raise ValueError → caught
        by `except Exception: pass` → record silently disappeared.
        """
        records = _good_records(5)
        records.append({"time": "NOT A TIME", "camera_rotation_euler": [0.0, 0.0, 0.0]})
        result = layer3_behavioral(records)
        assert result["passed"] is False
        assert result["stats"]["timestamps_bad"] == 1
        joined = " ".join(result["first_issues"])
        assert "unparseable" in joined
        assert "unparseable 'time' field" in joined

    def test_mixed_missing_and_unparseable_counted_separately(self) -> None:
        """Operator can distinguish missing_field vs unparseable from output."""
        records = _good_records(3)
        records.append({"timestamp": 0.1})  # missing
        records.append({"time": "garbage"})  # unparseable
        result = layer3_behavioral(records)
        assert result["passed"] is False
        assert result["stats"]["timestamps_bad"] == 2
        joined = " ".join(result["first_issues"])
        assert "missing_field=1" in joined
        assert "unparseable=1" in joined
