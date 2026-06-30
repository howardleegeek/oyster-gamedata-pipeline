#!/usr/bin/env python3
"""Tests for bin/prd_compliance_audit.py utility functions.

Covers: _result, _is_pass_status, _tag_critical_checks, summarize_audit_items,
_truthy_bool, _paused_sample_stats, and find_video.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bin.prd_compliance_audit import (
    _is_pass_status,
    _paused_sample_stats,
    _result,
    _tag_critical_checks,
    _truthy_bool,
    find_video,
    summarize_audit_items,
)


class TestResult:
    """Tests for _result helper."""

    def test_pass_result(self):
        """PASS result when ok=True."""
        r = _result("A1", True, "video exists")
        assert r["id"] == "A1"
        assert r["status"] == "PASS"
        assert r["evidence"] == "video exists"

    def test_fail_result(self):
        """FAIL result when ok=False."""
        r = _result("B8", False, "no video found")
        assert r["id"] == "B8"
        assert r["status"] == "FAIL"
        assert r["evidence"] == "no video found"


class TestIsPassStatus:
    """Tests for _is_pass_status helper."""

    @pytest.mark.parametrize(
        "status,expected",
        [
            ("PASS", True),
            ("PASS_STRICT", True),
            ("PASS_DEGRADED", True),
            ("FAIL", False),
            ("SKIP", False),
            ("", False),
            (None, False),
            (123, False),
            (["PASS"], False),
        ],
    )
    def test_various_statuses(self, status: Any, expected: bool):
        """_is_pass_status correctly identifies PASS* prefixes."""
        assert _is_pass_status(status) is expected


class TestTagCriticalChecks:
    """Tests for _tag_critical_checks helper."""

    def test_critical_checks_tagged(self):
        """Critical check IDs get critical=True."""
        items = [
            {"id": "A1", "status": "PASS"},
            {"id": "B8", "status": "FAIL"},
            {"id": "F8-manifest", "status": "PASS"},
            {"id": "C1", "status": "PASS"},
        ]
        _tag_critical_checks(items)
        assert items[0].get("critical") is True
        assert items[1].get("critical") is True
        assert items[2].get("critical") is True
        assert items[3].get("critical") is None

    def test_preserves_other_fields(self):
        """_tag_critical_checks doesn't mutate unrelated fields."""
        items = [{"id": "A1", "status": "PASS", "evidence": "test"}]
        _tag_critical_checks(items)
        assert items[0]["evidence"] == "test"


class TestSummarizeAuditItems:
    """Tests for summarize_audit_items main logic."""

    def test_all_pass_returns_pass_verdict(self):
        """All items pass → PASS verdict, 100% score."""
        items = [
            {"id": "A1", "status": "PASS", "evidence": "ok"},
            {"id": "B8", "status": "PASS", "evidence": "ok"},
        ]
        result = summarize_audit_items(items)
        assert result["verdict"] == "PASS"
        assert result["score"] == 100.0
        assert result["passed"] == 2
        assert result["failed"] == 0

    def test_any_fail_returns_fail_verdict(self):
        """Any FAIL → FAIL verdict."""
        items = [
            {"id": "A1", "status": "PASS", "evidence": "ok"},
            {"id": "B8", "status": "FAIL", "evidence": "missing"},
        ]
        result = summarize_audit_items(items)
        assert result["verdict"] == "FAIL"
        assert result["failed"] == 1

    def test_critical_failed_overrides_score(self):
        """Critical check failure → score capped to CRITICAL_SCORE_CAP_PERCENT (0%)."""
        items = [
            {"id": "A1", "status": "PASS", "evidence": "ok"},
            {"id": "A2", "status": "FAIL", "evidence": "missing"},  # critical
            {"id": "C1", "status": "PASS", "evidence": "ok"},
            {"id": "D1", "status": "PASS", "evidence": "ok"},
        ]
        result = summarize_audit_items(items)
        assert result["score"] == 0.0  # capped
        assert result["proportional_score_percent"] == 75.0  # 3/4 pass
        assert len(result["critical_failed"]) == 1
        assert result["critical_failed"][0]["id"] == "A2"

    def test_minor_fail_no_critical_pass(self):
        """Minor-only failures → proportional score (not capped)."""
        items = [
            {"id": "A1", "status": "PASS", "evidence": "ok"},  # critical
            {"id": "C1", "status": "FAIL", "evidence": "missing"},  # non-critical
            {"id": "D1", "status": "PASS", "evidence": "ok"},
        ]
        result = summarize_audit_items(items)
        assert result["score"] == 66.67  # 2/3 proportional
        assert result["critical_failed"] == []

    def test_empty_items_returns_zero_score(self):
        """Empty list → 0.0 score, PASS verdict."""
        result = summarize_audit_items([])
        assert result["score"] == 0.0
        assert result["verdict"] == "PASS"
        assert result["total_items"] == 0

    def test_score_rounding(self):
        """Score rounded to 2 decimal places; critical failures cap at 0."""
        # CRITICAL_SCORE_CAP_PERCENT = 0.0, so a failed critical check caps score at 0
        items = [
            {"id": "A1", "status": "PASS", "evidence": "ok"},
            {"id": "B8", "status": "FAIL", "evidence": "missing"},  # B8 is critical
            {"id": "C1", "status": "PASS", "evidence": "ok"},
        ]
        result = summarize_audit_items(items)
        assert result["score"] == 0.0  # capped at CRITICAL_SCORE_CAP_PERCENT (0.0)
        assert result["score_percent"] == 0.0
        assert result["score_10"] == 0.0
        assert result["proportional_score_percent"] == 66.67  # uncapped proportional

    def test_skipped_counts_as_fail(self):
        """SKIP counts as failed (not passed)."""
        items = [
            {"id": "A1", "status": "PASS", "evidence": "ok"},
            {"id": "B8", "status": "SKIP", "evidence": "skipped"},
        ]
        result = summarize_audit_items(items)
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["verdict"] == "FAIL"

    def test_critical_failed_includes_evidence(self):
        """critical_failed includes id, status, evidence."""
        items = [
            {"id": "A1", "status": "FAIL", "evidence": "missing video"},
        ]
        result = summarize_audit_items(items)
        assert result["critical_failed"][0]["evidence"] == "missing video"


class TestTruthyBool:
    """Tests for _truthy_bool helper."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("", False),
            (None, False),
            ("invalid", False),
        ],
    )
    def test_truthy_bool(self, value: Any, expected: bool):
        """_truthy_bool correctly parses common truthy strings."""
        assert _truthy_bool(value) is expected


class TestFindVideo:
    """Tests for find_video helper."""

    def test_finds_recording_mp4(self, tmp_path: Path):
        """Finds recording.mp4."""
        (tmp_path / "recording.mp4").touch()
        result = find_video(tmp_path)
        assert result is not None
        assert result.name == "recording.mp4"

    def test_finds_video_mp4(self, tmp_path: Path):
        """Finds video.mp4 when recording.mp4 absent."""
        (tmp_path / "video.mp4").touch()
        result = find_video(tmp_path)
        assert result is not None
        assert result.name == "video.mp4"

    def test_finds_screen_mp4(self, tmp_path: Path):
        """Finds screen.mp4 when others absent."""
        (tmp_path / "screen.mp4").touch()
        result = find_video(tmp_path)
        assert result is not None
        assert result.name == "screen.mp4"

    def test_prefers_recording_over_video(self, tmp_path: Path):
        """recording.mp4 takes precedence over video.mp4."""
        (tmp_path / "recording.mp4").touch()
        (tmp_path / "video.mp4").touch()
        (tmp_path / "screen.mp4").touch()
        result = find_video(tmp_path)
        assert result.name == "recording.mp4"

    def test_returns_none_when_no_video(self, tmp_path: Path):
        """Returns None when no video candidates exist."""
        result = find_video(tmp_path)
        assert result is None


class TestPausedSampleStats:
    """Tests for _paused_sample_stats helper."""

    def test_empty_session_returns_zeros(self, tmp_path: Path):
        """Empty session → 0, 0, 'game_state.paused' source."""
        count, total, evidence = _paused_sample_stats(tmp_path)
        assert count == 0
        assert total == 0
        assert evidence == "game_state.paused"

    def test_parses_game_state_jsonl(self, tmp_path: Path):
        """Parses paused markers from game_state.jsonl rows."""
        gs_path = tmp_path / "game_state.jsonl"
        rows = [
            {"frame": 1, "paused": True},
            {"frame": 2, "paused": False},
            {"frame": 3, "paused": "yes"},
        ]
        gs_path.write_text("\n".join(json.dumps(r) for r in rows))
        count, total, evidence = _paused_sample_stats(tmp_path)
        assert count == 2
        assert total == 3
        assert evidence == "game_state.paused"

    def test_parses_action_camera_json(self, tmp_path: Path):
        """Parses action_camera.json rows with _paused field."""
        ac_path = tmp_path / "action_camera.json"
        ac_path.write_text(
            json.dumps(
                [
                    {"frame": 1, "_paused": True},
                    {"frame": 2, "_paused": False},
                    {"frame": 3, "_paused": True},
                ]
            )
        )
        count, total, evidence = _paused_sample_stats(tmp_path)
        assert count == 2
        assert total == 3
        assert evidence == "action_camera._paused"
