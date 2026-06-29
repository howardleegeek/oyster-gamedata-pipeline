#!/usr/bin/env python3
"""
Tests for bin/prd_test_wasd_balance.py

PRD p6 #4: Validate no single WASD key exceeds 60% usage in long captures.
"""

import json
import tempfile
from pathlib import Path

from bin.prd_test_wasd_balance import (
    BalanceResult,
    KeyStats,
    analyze_balance,
    parse_keypress_file,
)


class TestParseKeypressFile:
    """Tests for parse_keypress_file function."""

    def test_parse_json_dict_format(self):
        """Test parsing JSON dictionary format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"W": 100, "A": 50, "S": 30, "D": 20}, f)
            f.flush()
            result = parse_keypress_file(Path(f.name))

        assert result == {"W": 100, "A": 50, "S": 30, "D": 20}

    def test_parse_json_list_format(self):
        """Test parsing JSON list format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([
                {"key": "W"}, {"key": "W"}, {"key": "A"}, {"key": "S"}, {"key": "D"}
            ], f)
            f.flush()
            result = parse_keypress_file(Path(f.name))

        assert result == {"W": 2, "A": 1, "S": 1, "D": 1}

    def test_parse_csv_format(self):
        """Test parsing CSV format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("key\nW\nW\nA\nS\nD\n")
            f.flush()
            result = parse_keypress_file(Path(f.name))

        assert result == {"W": 2, "A": 1, "S": 1, "D": 1}

    def test_parse_json_case_insensitive(self):
        """Test JSON parsing is case insensitive for keys."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"w": 10, "a": 5, "s": 3, "d": 2}, f)
            f.flush()
            result = parse_keypress_file(Path(f.name))

        assert result == {"W": 10, "A": 5, "S": 3, "D": 2}

    def test_empty_json_list(self):
        """Test parsing empty JSON list returns zeros."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([], f)
            f.flush()
            result = parse_keypress_file(Path(f.name))

        assert result == {"W": 0, "A": 0, "S": 0, "D": 0}


class TestAnalyzeBalance:
    """Tests for analyze_balance function."""

    def test_balanced_keys(self):
        """Test analysis with balanced keys (each 25%)."""
        counts = {"W": 25, "A": 25, "S": 25, "D": 25}
        result = analyze_balance(counts, threshold=60.0)

        assert result.total == 100
        assert len(result.violations) == 0
        for stat in result.stats:
            assert stat.percentage == 25.0

    def test_at_threshold_boundary(self):
        """Test that exactly 60% is NOT a violation (only above 60%)."""
        counts = {"W": 60, "A": 13, "S": 14, "D": 13}
        result = analyze_balance(counts, threshold=60.0)

        assert result.total == 100
        # 60% should NOT be a violation (only > 60%)
        assert len(result.violations) == 0

    def test_just_over_threshold(self):
        """Test that 60.01% IS a violation."""
        counts = {"W": 61, "A": 13, "S": 13, "D": 13}
        result = analyze_balance(counts, threshold=60.0)

        assert len(result.violations) == 1
        assert result.violations[0].key == "W"
        assert result.violations[0].percentage > 60.0

    def test_single_key_dominant(self):
        """Test with one key heavily dominant."""
        counts = {"W": 90, "A": 3, "S": 4, "D": 3}
        result = analyze_balance(counts, threshold=60.0)

        assert len(result.violations) == 1
        assert result.violations[0].key == "W"
        assert result.violations[0].percentage == 90.0

    def test_multiple_violations(self):
        """Test with multiple keys exceeding threshold."""
        counts = {"W": 50, "A": 50, "S": 0, "D": 0}
        result = analyze_balance(counts, threshold=40.0)

        assert len(result.violations) == 2

    def test_empty_counts(self):
        """Test with empty counts returns zero total."""
        counts = {"W": 0, "A": 0, "S": 0, "D": 0}
        result = analyze_balance(counts, threshold=60.0)

        assert result.total == 0
        assert result.stats == []
        assert result.violations == []

    def test_custom_threshold(self):
        """Test with custom threshold."""
        counts = {"W": 70, "A": 10, "S": 10, "D": 10}
        result = analyze_balance(counts, threshold=80.0)

        assert len(result.violations) == 0

    def test_stats_order_preserved(self):
        """Test that stats are returned in WASD order."""
        counts = {"D": 10, "W": 40, "S": 30, "A": 20}
        result = analyze_balance(counts, threshold=60.0)

        keys = [stat.key for stat in result.stats]
        assert keys == ["W", "A", "S", "D"]

    def test_off_by_one_at_boundary_59_99(self):
        """Test off-by-one: 59.99% should not be a violation."""
        counts = {"W": 5999, "A": 1334, "S": 1333, "D": 1334}
        result = analyze_balance(counts, threshold=60.0)

        # 5999/10000 = 59.99%
        assert len(result.violations) == 0


class TestKeyStats:
    """Tests for KeyStats NamedTuple."""

    def test_key_stats_creation(self):
        """Test KeyStats creation."""
        stat = KeyStats(key="W", count=100, percentage=50.0)
        assert stat.key == "W"
        assert stat.count == 100
        assert stat.percentage == 50.0


class TestBalanceResult:
    """Tests for BalanceResult NamedTuple."""

    def test_balance_result_creation(self):
        """Test BalanceResult creation."""
        stats = [
            KeyStats(key="W", count=50, percentage=50.0),
            KeyStats(key="A", count=50, percentage=50.0),
        ]
        result = BalanceResult(total=100, stats=stats, violations=[])
        assert result.total == 100
        assert len(result.stats) == 2
        assert result.violations == []
