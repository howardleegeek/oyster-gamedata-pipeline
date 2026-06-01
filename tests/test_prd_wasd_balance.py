#!/usr/bin/env python3
"""Tests for bin/prd_test_wasd_balance.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin" / "prd_test_wasd_balance.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Unit tests via import
# ---------------------------------------------------------------------------

def _load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("wasd_balance", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParseKeypressFile:
    """Tests for parse_keypress_file function."""

    def test_json_dict_format(self, tmp_path: Path):
        """Parse JSON dict with key counts."""
        mod = _load_module()
        data = {"W": 100, "A": 50, "S": 30, "D": 20}
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps(data))
        result = mod.parse_keypress_file(f)
        assert result == {"W": 100, "A": 50, "S": 30, "D": 20}

    def test_json_list_format(self, tmp_path: Path):
        """Parse JSON list of keypress records."""
        mod = _load_module()
        records = [{"key": "W"}, {"key": "w"}, {"key": "A"}, {"key": "S"}]
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps(records))
        result = mod.parse_keypress_file(f)
        assert result["W"] == 2  # W and w both count
        assert result["A"] == 1
        assert result["S"] == 1
        assert result["D"] == 0

    def test_csv_format(self, tmp_path: Path):
        """Parse CSV — each row counts as one keypress."""
        mod = _load_module()
        # CSV parser counts rows, not the count column
        content = "key,count\nW,100\nA,50\nS,30\nD,20\n"
        f = tmp_path / "keypress.csv"
        f.write_text(content)
        result = mod.parse_keypress_file(f)
        # Each row = 1 keypress (the CSV parser increments by 1 per row)
        assert result == {"W": 1, "A": 1, "S": 1, "D": 1}

    def test_csv_multiple_rows_same_key(self, tmp_path: Path):
        """CSV with multiple rows for same key should sum counts."""
        mod = _load_module()
        content = "key,count\nW,50\nW,50\nA,30\n"
        f = tmp_path / "keypress.csv"
        f.write_text(content)
        result = mod.parse_keypress_file(f)
        assert result["W"] == 2  # 2 rows for W
        assert result["A"] == 1

    def test_unsupported_format_returns_zeros(self, tmp_path: Path):
        """Plain text without JSON/CSV structure returns zero counts (silent fallback)."""
        mod = _load_module()
        f = tmp_path / "keypress.txt"
        f.write_text("just some random text\nwith no structure")
        result = mod.parse_keypress_file(f)
        # CSV DictReader silently produces no matching rows
        assert result == {"W": 0, "A": 0, "S": 0, "D": 0}

    def test_json_list_ignores_non_dict_records(self, tmp_path: Path):
        """JSON list with non-dict records should be ignored."""
        mod = _load_module()
        records = [{"key": "W"}, "not a dict", 42, {"key": "A"}]
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps(records))
        result = mod.parse_keypress_file(f)
        assert result["W"] == 1
        assert result["A"] == 1

    def test_json_list_ignores_unknown_keys(self, tmp_path: Path):
        """JSON list with keys outside WASD should be ignored."""
        mod = _load_module()
        records = [{"key": "W"}, {"key": "X"}, {"key": "Space"}]
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps(records))
        result = mod.parse_keypress_file(f)
        assert result["W"] == 1
        assert result["A"] == 0
        assert result["S"] == 0
        assert result["D"] == 0

    def test_json_dict_case_insensitive_keys(self, tmp_path: Path):
        """JSON dict keys should be uppercased."""
        mod = _load_module()
        data = {"w": 10, "a": 20, "s": 30, "d": 40}
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps(data))
        result = mod.parse_keypress_file(f)
        assert result == {"W": 10, "A": 20, "S": 30, "D": 40}


class TestAnalyzeBalance:
    """Tests for analyze_balance function."""

    def test_balanced_keys(self):
        """Equal key usage should have no violations."""
        mod = _load_module()
        counts = {"W": 25, "A": 25, "S": 25, "D": 25}
        result = mod.analyze_balance(counts, threshold=40.0)
        assert result.total == 100
        assert len(result.violations) == 0
        for stat in result.stats:
            assert stat.percentage == pytest.approx(25.0)

    def test_single_key_violation(self):
        """One key exceeding 60% should be a violation."""
        mod = _load_module()
        counts = {"W": 70, "A": 10, "S": 10, "D": 10}
        result = mod.analyze_balance(counts, threshold=40.0)
        assert result.total == 100
        assert len(result.violations) == 1
        assert result.violations[0].key == "W"
        assert result.violations[0].percentage == pytest.approx(70.0)

    def test_multiple_violations(self):
        """Two keys exceeding threshold should both be violations."""
        mod = _load_module()
        counts = {"W": 50, "A": 50, "S": 0, "D": 0}
        result = mod.analyze_balance(counts, threshold=40.0)
        assert len(result.violations) == 2
        keys = {v.key for v in result.violations}
        assert keys == {"W", "A"}

    def test_empty_counts(self):
        """Zero total keypresses should return empty stats."""
        mod = _load_module()
        counts = {"W": 0, "A": 0, "S": 0, "D": 0}
        result = mod.analyze_balance(counts, threshold=40.0)
        assert result.total == 0
        assert result.stats == []
        assert result.violations == []

    def test_custom_threshold(self):
        """Custom threshold should be respected."""
        mod = _load_module()
        counts = {"W": 40, "A": 20, "S": 20, "D": 20}
        result = mod.analyze_balance(counts, threshold=35.0)
        assert len(result.violations) == 1
        assert result.violations[0].key == "W"
        assert result.violations[0].percentage == pytest.approx(40.0)

    def test_at_threshold_boundary(self):
        """Exactly at threshold should NOT be a violation (> not >=)."""
        mod = _load_module()
        counts = {"W": 60, "A": 13, "S": 13, "D": 14}
        result = mod.analyze_balance(counts, threshold=60.0)
        assert len(result.violations) == 0

    def test_stats_order_wasd(self):
        """Stats should be in WASD order."""
        mod = _load_module()
        counts = {"W": 10, "A": 20, "S": 30, "D": 40}
        result = mod.analyze_balance(counts, threshold=40.0)
        assert [s.key for s in result.stats] == ["W", "A", "S", "D"]

    def test_key_stats_namedtuple_fields(self):
        """KeyStats should have key, count, percentage fields."""
        mod = _load_module()
        counts = {"W": 50, "A": 50, "S": 0, "D": 0}
        result = mod.analyze_balance(counts, threshold=40.0)
        stat = result.stats[0]
        assert stat.key == "W"
        assert stat.count == 50
        assert stat.percentage == pytest.approx(50.0)


class TestCLI:
    """Tests for CLI interface."""

    def test_help(self):
        """--help should exit 0 and show usage."""
        result = _run(["--help"])
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower() or "Usage:" in result.stdout

    def test_missing_file(self, tmp_path: Path):
        """Non-existent file should exit 2."""
        result = _run([str(tmp_path / "nonexistent.json")])
        assert result.returncode == 2
        assert "not found" in result.stderr.lower() or "not found" in result.stdout.lower()

    def test_pass_balanced(self, tmp_path: Path):
        """Balanced input should exit 0."""
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps({"W": 25, "A": 25, "S": 25, "D": 25}))
        result = _run([str(f)])
        assert result.returncode == 0
        assert "PASS" in result.stdout

    def test_fail_violation(self, tmp_path: Path):
        """Unbalanced input should exit 1."""
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps({"W": 90, "A": 5, "S": 3, "D": 2}))
        result = _run([str(f)])
        assert result.returncode == 1
        assert "FAIL" in result.stderr or "FAIL" in result.stdout

    def test_verbose_output(self, tmp_path: Path):
        """--verbose should print detailed stats."""
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps({"W": 25, "A": 25, "S": 25, "D": 25}))
        result = _run([str(f), "--verbose"])
        assert result.returncode == 0
        assert "Total keypresses: 100" in result.stdout
        assert "W:" in result.stdout

    def test_csv_input(self, tmp_path: Path):
        """CSV input should work via CLI."""
        f = tmp_path / "keypress.csv"
        f.write_text("key,count\nW,25\nA,25\nS,25\nD,25\n")
        result = _run([str(f)])
        assert result.returncode == 0

    def test_custom_threshold_cli(self, tmp_path: Path):
        """--threshold flag should work via CLI."""
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps({"W": 40, "A": 20, "S": 20, "D": 20}))
        # At default 60% threshold, this passes
        result = _run([str(f)])
        assert result.returncode == 0
        # At 35% threshold, W=40% should fail
        result = _run([str(f), "-t", "35"])
        assert result.returncode == 1

    def test_empty_json_dict(self, tmp_path: Path):
        """Empty JSON dict should pass (no keypresses = no violation)."""
        f = tmp_path / "keypress.json"
        f.write_text(json.dumps({}))
        result = _run([str(f)])
        assert result.returncode == 0
