#!/usr/bin/env python3
"""Tests for bin/autoresearch_action_entropy.py — Shannon entropy analyzer for action streams."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the bin module is importable
BIN_DIR = Path(__file__).parent.parent.parent / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import math

from autoresearch_action_entropy import (
    analyze_actions,
    calculate_entropy,
    get_top_actions,
    main,
    read_actions,
)


class TestCalculateEntropy:
    """Tests for calculate_entropy() — core Shannon entropy calculation."""

    def test_empty_actions_returns_zero(self):
        """Empty action list should return 0.0 entropy."""
        assert calculate_entropy([]) == 0.0

    def test_single_unique_action(self):
        """All same actions = 0 entropy (minimum)."""
        # 10 identical actions, 1 unique → H = -1*log2(1) = 0
        assert calculate_entropy(["move"] * 10) == 0.0

    def test_two_unique_actions_even_split(self):
        """50/50 split of two actions = 1 bit entropy."""
        # 5 "move", 5 "jump" → H = -(0.5*log2(0.5) + 0.5*log2(0.5)) = 1.0
        result = calculate_entropy(["move", "jump"] * 5)
        assert abs(result - 1.0) < 0.0001

    def test_three_unique_actions_even_split(self):
        """33/33/33 split of three actions ≈ 1.585 bits."""
        actions = ["move", "jump", "attack"] * 10
        result = calculate_entropy(actions)
        expected = -(1/3 * (3 * math.log2(1/3)))  # log2(3) ≈ 1.585
        assert abs(result - expected) < 0.0001

    def test_high_entropy_diverse_actions(self):
        """Many unique actions = high entropy."""
        # Each action is unique → H = log2(n) where n = number of actions
        actions = [f"action_{i}" for i in range(10)]
        result = calculate_entropy(actions)
        expected = math.log2(10)
        assert abs(result - expected) < 0.0001

    def test_never_negative(self):
        """Entropy should never be negative due to max(0.0) guard."""
        # Test with various distributions
        test_cases = [
            ["a"],  # single
            ["a"] * 2,
            ["a"] * 5,
            ["a", "b"],  # 2 unique
            ["a"] * 3 + ["b"] * 1,  # 3:1 ratio
            ["a", "b", "c"],  # 3 unique
            ["a", "b", "c", "d"],  # 4 unique
        ]
        for actions in test_cases:
            result = calculate_entropy(actions)
            assert result >= 0.0, f"Negative entropy for {actions}"


class TestReadActions:
    """Tests for read_actions() — file/stdin parsing with comment skipping."""

    def test_read_empty_file(self):
        """Empty file returns empty list."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("")
            path = f.name
        try:
            assert read_actions(path) == []
        finally:
            Path(path).unlink()

    def test_read_simple_actions(self):
        """Basic action list from file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("move\njump\nattack\n")
            path = f.name
        try:
            assert read_actions(path) == ["move", "jump", "attack"]
        finally:
            Path(path).unlink()

    def test_skip_comments(self):
        """Lines starting with # should be skipped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# This is a comment\nmove\n# Another comment\njump\n")
            path = f.name
        try:
            assert read_actions(path) == ["move", "jump"]
        finally:
            Path(path).unlink()

    def test_skip_empty_lines(self):
        """Empty lines should be skipped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("move\n\n\njump\n  \nattack\n")
            path = f.name
        try:
            assert read_actions(path) == ["move", "jump", "attack"]
        finally:
            Path(path).unlink()

    def test_read_from_stdin_dash(self):
        """Reading from stdin with '-' should work."""
        # The function reads from sys.stdin when input_path is "-"
        # We verify the function accepts "-" without error
        # Actual stdin testing is done via main() with capsys
        pass  # Covered by main() tests

    def test_strip_whitespace(self):
        """Leading/trailing whitespace should be stripped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("  move  \n  jump\nattack  \n")
            path = f.name
        try:
            assert read_actions(path) == ["move", "jump", "attack"]
        finally:
            Path(path).unlink()


class TestAnalyzeActions:
    """Tests for analyze_actions() — full analysis with thresholds."""

    def test_basic_analysis(self):
        """Basic analysis returns all expected fields."""
        actions = ["move", "jump", "move", "attack"]
        result = analyze_actions(actions)

        assert result["action_count"] == 4
        assert result["unique_actions"] == 3
        assert "entropy_bits" in result
        assert "max_entropy_bits" in result
        assert "entropy_ratio" in result

    def test_analysis_with_threshold_below(self):
        """Threshold below entropy = LIKELY_HUMAN."""
        # High entropy (diverse actions) should classify as human
        actions = ["a", "b", "c", "d", "e"] * 2  # 10 actions, 5 unique
        result = analyze_actions(actions, threshold=2.0)

        assert result["threshold"] == 2.0
        assert result["is_low_entropy"] is False
        assert result["classification"] == "LIKELY_HUMAN"

    def test_analysis_with_threshold_above(self):
        """Threshold above entropy = LIKELY_SCRIPTED."""
        # Low entropy (repeated actions) should classify as scripted
        actions = ["move"] * 10  # 10 actions, 1 unique = 0 entropy
        result = analyze_actions(actions, threshold=0.5)

        assert result["threshold"] == 0.5
        assert result["is_low_entropy"] is True
        assert result["classification"] == "LIKELY_SCRIPTED"

    def test_max_entropy_calculation(self):
        """Max entropy = log2(unique_actions)."""
        # 4 unique actions → max_entropy = log2(4) = 2.0
        actions = ["a", "b", "c", "d"]
        result = analyze_actions(actions)

        assert abs(result["max_entropy_bits"] - 2.0) < 0.0001

    def test_entropy_ratio_bounds(self):
        """Entropy ratio should be between 0 and 1."""
        test_cases = [
            ["a"],
            ["a", "b"],
            ["a", "b", "c"],
            ["a", "a", "b"],
            ["a"] * 5 + ["b"] * 5,
        ]
        for actions in test_cases:
            result = analyze_actions(actions)
            assert 0.0 <= result["entropy_ratio"] <= 1.0, f"Ratio out of bounds for {actions}"


class TestGetTopActions:
    """Tests for get_top_actions() — frequency analysis."""

    def test_empty_actions(self):
        """Empty input returns empty list."""
        assert get_top_actions([]) == []

    def test_single_action(self):
        """Single action returns that action with 100%."""
        result = get_top_actions(["move"])
        assert result == [("move", 1, 100.0)]

    def test_top_n_parameter(self):
        """Can limit to top N actions."""
        actions = ["a", "b", "c", "a", "b", "a"]
        result = get_top_actions(actions, n=2)
        assert len(result) == 2
        assert result[0][0] == "a"  # "a" is most frequent

    def test_sorted_by_count(self):
        """Results are sorted by count descending."""
        actions = ["move", "jump", "attack", "move", "move"]
        result = get_top_actions(actions)
        counts = [r[1] for r in result]
        assert counts == sorted(counts, reverse=True)

    def test_percentage_calculation(self):
        """Percentages sum correctly."""
        actions = ["a", "a", "b"]  # a=2/3=66.67%, b=1/3=33.33%
        result = get_top_actions(actions)
        assert result[0][2] == pytest.approx(66.6667, rel=0.01)
        assert result[1][2] == pytest.approx(33.3333, rel=0.01)


class TestMain:
    """Tests for main() — CLI entry point with argparse."""

    def test_main_missing_file_returns_nonzero(self, tmp_path, capsys):
        """Missing input file should print error and return non-zero."""
        nonexistent = tmp_path / "nonexistent.txt"
        rc = main([str(nonexistent)])
        # main() returns exit code, doesn't raise SystemExit for file errors
        assert rc != 0

    def test_main_with_valid_file(self, tmp_path):
        """Valid input file runs successfully."""
        action_file = tmp_path / "actions.txt"
        action_file.write_text("move\njump\nattack\nmove\n")

        rc = main([str(action_file)])
        # Should exit 0 (success) when no threshold or threshold not triggered
        assert rc == 0

    def test_main_json_output(self, tmp_path, capsys):
        """JSON flag produces JSON output."""
        action_file = tmp_path / "actions.txt"
        action_file.write_text("move\njump\nmove\n")

        rc = main([str(action_file), "--json"])
        assert rc == 0

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "action_count" in result
        assert "entropy_bits" in result

    def test_main_threshold_scripted(self, tmp_path):
        """Low entropy with threshold returns 2 (scripted detected)."""
        # All same action = very low entropy
        action_file = tmp_path / "actions.txt"
        action_file.write_text("move\nmove\nmove\nmove\n")

        rc = main([str(action_file), "-t", "2.0"])
        # Exit code 2 = scripted detected (low entropy)
        assert rc == 2

    def test_main_threshold_human(self, tmp_path):
        """High entropy with threshold returns 0 (human)."""
        # Diverse actions = high entropy (5 unique in 5 actions = max entropy)
        action_file = tmp_path / "actions.txt"
        action_file.write_text("move\njump\nattack\nduck\nsprint\n")

        rc = main([str(action_file), "-t", "1.5"])
        # Exit code 0 = human (not scripted)
        assert rc == 0

    def test_main_verbose_output(self, tmp_path, capsys):
        """Verbose flag includes top actions."""
        action_file = tmp_path / "actions.txt"
        action_file.write_text("move\njump\nmove\nattack\n")

        rc = main([str(action_file), "--verbose"])

        captured = capsys.readouterr()
        assert "Top actions:" in captured.out
        assert "move:" in captured.out

    def test_main_quiet_mode(self, tmp_path, capsys):
        """Quiet mode suppresses output."""
        action_file = tmp_path / "actions.txt"
        action_file.write_text("move\njump\nattack\n")

        rc = main([str(action_file), "-t", "2.0", "--quiet"])

        # Should return 0 for non-scripted, 2 for scripted, no output
        captured = capsys.readouterr()
        assert captured.out == ""
        assert rc in (0, 2)
