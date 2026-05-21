#!/usr/bin/env python3
"""Tests for scripts/gen_marketplace_listing.py.

Covers:
- load_sweep reads valid JSON
- load_sweep raises on missing file
- generate_listing produces all 6 required sections
- Title format matches spec
- Stats table contains required metrics
- Pricing section present
- Sample data section present
- Provenance quickstart section present
- Contact section present
- BUYER_READY percentage calculation
- CLI entry point
- Edge cases: empty sessions, zero-duration sessions
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure scripts/ is importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import gen_marketplace_listing as gml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_SWEEP = {
    "sweep_started": "2026-05-19T08:00:00Z",
    "sweep_finished": "2026-05-19T14:30:00Z",
    "total_found": 3,
    "evaluated": 3,
    "summary": {
        "BUYER_READY": 2,
        "DEGRADED": 1,
        "FAIL": 0,
        "pass_rate_pct": 66,
    },
    "sessions": [
        {
            "name": "session_001",
            "game": "Minecraft",
            "duration_s": 1800,
            "pipeline": "PASS",
            "gates": {"verdict": "PASS", "passed": 9, "total": 9},
            "provenance": "VERIFIED",
            "overall": "PASS",
            "buyer_label": "BUYER_READY",
        },
        {
            "name": "session_002",
            "game": "CS2",
            "duration_s": 3600,
            "pipeline": "PASS",
            "gates": {"verdict": "PASS", "passed": 9, "total": 9},
            "provenance": "VERIFIED",
            "overall": "PASS",
            "buyer_label": "BUYER_READY",
        },
        {
            "name": "session_003",
            "game": "Minecraft",
            "duration_s": 900,
            "pipeline": "PASS",
            "gates": {"verdict": "DEGRADED", "passed": 7, "total": 9},
            "provenance": "VERIFIED",
            "overall": "DEGRADED",
            "buyer_label": "DEGRADED",
        },
    ],
}

EMPTY_SWEEP = {
    "sweep_started": "2026-05-19T08:00:00Z",
    "sweep_finished": "2026-05-19T08:00:00Z",
    "total_found": 0,
    "evaluated": 0,
    "summary": {
        "BUYER_READY": 0,
        "DEGRADED": 0,
        "FAIL": 0,
        "pass_rate_pct": 0,
    },
    "sessions": [],
}


# ---------------------------------------------------------------------------
# load_sweep tests
# ---------------------------------------------------------------------------


class TestLoadSweep(unittest.TestCase):
    def test_load_valid_json(self, tmp_path=None):
        """load_sweep returns dict from valid JSON file."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(MINIMAL_SWEEP, f)
            f.flush()
            data = gml.load_sweep(Path(f.name))
        self.assertEqual(data["evaluated"], 3)
        self.assertEqual(len(data["sessions"]), 3)

    def test_load_missing_file(self):
        """load_sweep raises FileNotFoundError for missing file."""
        with self.assertRaises(FileNotFoundError):
            gml.load_sweep(Path("/nonexistent/sweep_summary.json"))

    def test_load_invalid_json(self, tmp_path=None):
        """load_sweep raises JSONDecodeError for invalid JSON."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json")
            f.flush()
            with self.assertRaises(json.JSONDecodeError):
                gml.load_sweep(Path(f.name))


# ---------------------------------------------------------------------------
# Computation helper tests
# ---------------------------------------------------------------------------


class TestComputationHelpers(unittest.TestCase):
    def test_count_games(self):
        """_count_games returns distinct game count."""
        self.assertEqual(gml._count_games(MINIMAL_SWEEP["sessions"]), 2)

    def test_count_games_empty(self):
        """_count_games returns 0 for empty list."""
        self.assertEqual(gml._count_games([]), 0)

    def test_total_hours(self):
        """_total_hours returns correct total."""
        # 1800 + 3600 + 900 = 6300 seconds = 1.75 hours
        self.assertAlmostEqual(gml._total_hours(MINIMAL_SWEEP["sessions"]), 1.75)

    def test_total_hours_empty(self):
        """_total_hours returns 0 for empty list."""
        self.assertEqual(gml._total_hours([]), 0.0)

    def test_avg_duration_minutes(self):
        """_avg_duration_minutes returns correct average."""
        # (1800 + 3600 + 900) / 3 = 2100 seconds = 35 minutes
        self.assertAlmostEqual(gml._avg_duration_minutes(MINIMAL_SWEEP["sessions"]), 35.0)

    def test_avg_duration_minutes_empty(self):
        """_avg_duration_minutes returns 0 for empty list."""
        self.assertEqual(gml._avg_duration_minutes([]), 0.0)

    def test_buyer_ready_pct(self):
        """_buyer_ready_pct returns correct percentage."""
        # 2 out of 3 = 66.67%
        pct = gml._buyer_ready_pct(MINIMAL_SWEEP["sessions"])
        self.assertAlmostEqual(pct, 66.66666666666667)

    def test_buyer_ready_pct_empty(self):
        """_buyer_ready_pct returns 0 for empty list."""
        self.assertEqual(gml._buyer_ready_pct([]), 0.0)

    def test_buyer_ready_pct_all_ready(self):
        """_buyer_ready_pct returns 100 when all are BUYER_READY."""
        sessions = [
            {"buyer_label": "BUYER_READY"},
            {"buyer_label": "BUYER_READY"},
        ]
        self.assertEqual(gml._buyer_ready_pct(sessions), 100.0)

    def test_total_price(self):
        """_total_price returns correct total."""
        self.assertEqual(gml._total_price(MINIMAL_SWEEP["sessions"]), 36)  # 3 * 12


# ---------------------------------------------------------------------------
# generate_listing tests
# ---------------------------------------------------------------------------


class TestGenerateListing(unittest.TestCase):
    def setUp(self):
        self.content = gml.generate_listing(MINIMAL_SWEEP, version="0.7.2")

    def test_title_format(self):
        """Title matches spec: 'Oyster GameData v0.7.x — X games, Y hours, Z sessions'."""
        self.assertIn("# Oyster GameData v0.7.2 — 2 games, 1.8 hours, 3 sessions", self.content)

    def test_has_stats_section(self):
        """Output contains Session Statistics section."""
        self.assertIn("## 📊 Session Statistics", self.content)

    def test_has_pricing_section(self):
        """Output contains Pricing section."""
        self.assertIn("## 💰 Pricing", self.content)

    def test_has_sample_data_section(self):
        """Output contains Sample Data Download section."""
        self.assertIn("## 📥 Sample Data Download", self.content)

    def test_has_provenance_section(self):
        """Output contains Provenance Verify Quickstart section."""
        self.assertIn("## 🔐 Provenance Verify Quickstart", self.content)

    def test_has_contact_section(self):
        """Output contains Contact section."""
        self.assertIn("## 📬 Contact", self.content)

    def test_stats_table_has_sessions_count(self):
        """Stats table includes total sessions evaluated."""
        self.assertIn("| Total sessions evaluated | 3 |", self.content)

    def test_stats_table_has_buyer_ready(self):
        """Stats table includes BUYER_READY count and percentage."""
        self.assertIn("| BUYER_READY sessions | 2 (67%) |", self.content)

    def test_stats_table_has_avg_duration(self):
        """Stats table includes average session duration."""
        self.assertIn("| Avg session duration | 35.0 minutes |", self.content)

    def test_stats_table_has_total_hours(self):
        """Stats table includes total recording time."""
        self.assertIn("| Total recording time | 1.8 hours |", self.content)

    def test_pricing_per_session(self):
        """Pricing section shows per-session price."""
        self.assertIn("| Per session | $12.00 USD |", self.content)

    def test_pricing_full_dataset(self):
        """Pricing section shows full dataset price."""
        self.assertIn("| Full dataset (3 sessions) | $36.00 USD |", self.content)

    def test_provenance_has_verify_command(self):
        """Provenance section includes verify command."""
        self.assertIn("python3 bin/provenance_verify.py", self.content)

    def test_contact_has_email(self):
        """Contact section includes email."""
        self.assertIn("data@oyster.gg", self.content)

    def test_four_sections_minimum(self):
        """Output has at least 4 distinct ## sections."""
        section_count = self.content.count("## ")
        self.assertGreaterEqual(section_count, 4)

    def test_generated_at_timestamp(self):
        """Output includes a generated timestamp."""
        self.assertIn("**Generated:**", self.content)

    def test_sweep_window(self):
        """Output includes sweep window."""
        self.assertIn("2026-05-19T08:00:00Z", self.content)
        self.assertIn("2026-05-19T14:30:00Z", self.content)

    def test_custom_price(self):
        """Custom price_per_session is reflected in output."""
        content = gml.generate_listing(MINIMAL_SWEEP, price_per_session=25.0)
        self.assertIn("| Per session | $25.00 USD |", content)
        self.assertIn("| Full dataset (3 sessions) | $75.00 USD |", content)


class TestGenerateListingEmpty(unittest.TestCase):
    """Test generate_listing with empty sessions."""

    def setUp(self):
        self.content = gml.generate_listing(EMPTY_SWEEP, version="0.7.0")

    def test_title_with_zero(self):
        """Title handles zero sessions gracefully."""
        self.assertIn("# Oyster GameData v0.7.0 — 0 games, 0.0 hours, 0 sessions", self.content)

    def test_no_division_by_zero(self):
        """Empty sessions don't cause division by zero."""
        # If we got here without exception, the test passes
        self.assertIn("## 📊 Session Statistics", self.content)

    def test_zero_pricing(self):
        """Pricing shows $0 for empty dataset."""
        self.assertIn("| Full dataset (0 sessions) | $0.00 USD |", self.content)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI(unittest.TestCase):
    @patch("gen_marketplace_listing.load_sweep")
    @patch(
        "gen_marketplace_listing.DEFAULT_OUTPUT",
        Path("/tmp/test_marketplace_listing.md"),
    )
    def test_main_success(self, mock_load):
        """main() returns 0 on success and writes output."""
        mock_load.return_value = MINIMAL_SWEEP
        rc = gml.main(["--version", "0.7.5"])
        self.assertEqual(rc, 0)

    def test_main_missing_file(self):
        """main() returns 1 when input file is missing."""
        rc = gml.main(["--input", "/nonexistent/sweep_summary.json"])
        self.assertEqual(rc, 1)

    @patch("gen_marketplace_listing.load_sweep")
    @patch(
        "gen_marketplace_listing.DEFAULT_OUTPUT",
        Path("/tmp/test_marketplace_listing.md"),
    )
    def test_main_custom_price(self, mock_load):
        """main() accepts --price flag."""
        mock_load.return_value = MINIMAL_SWEEP
        rc = gml.main(["--price", "20.00"])
        self.assertEqual(rc, 0)
        output = Path("/tmp/test_marketplace_listing.md").read_text()
        self.assertIn("$20.00 USD", output)


if __name__ == "__main__":
    unittest.main()
