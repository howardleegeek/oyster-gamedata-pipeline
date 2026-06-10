#!/usr/bin/env python3
"""
Tests for scripts/gen_quickstart.py

Mocks subprocess calls to avoid actually running the CLI tools.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure scripts/ is importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Sample --help outputs for mocking
PROVENANCE_HELP = """\
usage: provenance_verify.py [-h] [--expect-pubkey EXPECT_PUBKEY]
                            signed_manifest

Verify an Ed25519-signed batch manifest

positional arguments:
  signed_manifest       Path to signed manifest JSON

options:
  -h, --help            show this help message and exit
  --expect-pubkey EXPECT_PUBKEY
                        Expected pubkey fingerprint (first 16 hex chars of
                        sha256(pubkey))
"""

E2E_HELP = """\
usage: end_to_end_gate_smoke.py [-h] [--json] [--skip-sign] [--strict-buyer]
                                session_dir

End-to-end gate smoke test — runs all gates against a session dir

positional arguments:
  session_dir     Path to session directory

options:
  -h, --help      show this help message and exit
  --json          Output JSON instead of human-readable table
  --skip-sign     Skip B2 provenance sign/verify round-trip
  --strict-buyer  v0.4.1: BLOCK on SKIP/PASS_DEGRADED for H8/S1/V1/V2/B2
                  gates. Required for production buyer deliverables. Without
                  this flag, the gate is in DEMO mode and SKIP is permitted
                  (e.g. H8 monocular fallback won't block but also won't ship
                  as production data).
"""

SAMPLE_CHANGELOG = """\
# CHANGELOG

## v0.4.1 · 2026-05-19

### Added
- `--strict-buyer` flag on `bin/end_to_end_gate_smoke.py`
- `bin/provenance_verify.py`: Ed25519-signed batch manifest verifier

### Changed
- Gate smoke test now distinguishes DEMO mode from production mode.
"""


def _make_mock_result(stdout: str, returncode: int = 0):
    """Create a mock subprocess.CompletedProcess."""
    return MagicMock(returncode=returncode, stdout=stdout, stderr="")


class TestGenQuickstart(unittest.TestCase):
    """Test the gen_quickstart module."""

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_generate_contains_required_strings(self, mock_run):
        """Generated content must contain 'exit 0', 'verify.sh', 'strict-buyer'."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import generate

        content = generate()
        self.assertIn("exit 0", content)
        self.assertIn("verify.sh", content)
        self.assertIn("strict-buyer", content)

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_generate_contains_help_blocks(self, mock_run):
        """Generated content must include help output from both tools."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import generate

        content = generate()
        self.assertIn("provenance_verify.py", content)
        self.assertIn("end_to_end_gate_smoke.py", content)
        self.assertIn("Ed25519-signed batch manifest", content)

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_generate_under_1000_lines(self, mock_run):
        """Generated content must be ≤ 1000 lines."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import generate

        content = generate()
        lines = content.split("\n")
        self.assertLessEqual(len(lines), 1000)

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_generate_contains_sections(self, mock_run):
        """Generated content must have all required sections."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import generate

        content = generate()
        self.assertIn("## 1. Install Python 3.10+", content)
        self.assertIn("## 2. Download the Bundle", content)
        self.assertIn("## 3. Run Verification", content)
        self.assertIn("## 4.", content)  # Optional gate smoke test
        self.assertIn("## 5. Contact", content)
        self.assertIn("## FAQ", content)

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_generate_contains_faq_entries(self, mock_run):
        """Generated content must have 5 FAQ entries."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import generate

        content = generate()
        faq_count = content.count("### Q")
        self.assertGreaterEqual(faq_count, 5)

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_generate_no_external_links(self, mock_run):
        """Generated content must not contain external http/https links."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import generate

        content = generate()
        # Allow only python.org as a package manager reference (not a real link)
        import re

        external_links = re.findall(r"https?://[^\s\)]+", content)
        # python.org mention is acceptable as it's a standard reference
        for link in external_links:
            self.assertIn("python.org", link)

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_generate_no_images(self, mock_run):
        """Generated content must not contain image references."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import generate

        content = generate()
        self.assertNotIn("![", content)
        self.assertNotIn("<img", content)

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_generate_contains_auto_generated_note(self, mock_run):
        """Generated content must note it was auto-generated."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import generate

        content = generate()
        self.assertIn("Auto-generated", content)
        self.assertIn("gen_quickstart.py", content)


class TestExtractOptions(unittest.TestCase):
    """Test the _extract_options helper."""

    def test_extract_options_from_help(self):
        """Options are correctly parsed from --help output."""
        from gen_quickstart import _extract_options

        options = _extract_options(E2E_HELP)
        self.assertGreater(len(options), 0)
        flags = [o["flag"] for o in options]
        self.assertIn("--strict-buyer", flags)
        self.assertIn("--json", flags)
        self.assertIn("--skip-sign", flags)

    def test_extract_options_from_provenance_help(self):
        """Options are correctly parsed from provenance_verify --help."""
        from gen_quickstart import _extract_options

        options = _extract_options(PROVENANCE_HELP)
        flags = [o["flag"] for o in options]
        self.assertIn("--expect-pubkey", flags)


class TestExtractPositional(unittest.TestCase):
    """Test the _extract_positional helper."""

    def test_extract_positional_args(self):
        """Positional arguments are correctly parsed."""
        from gen_quickstart import _extract_positional

        positionals = _extract_positional(E2E_HELP)
        self.assertGreater(len(positionals), 0)
        names = [p["name"] for p in positionals]
        self.assertIn("session_dir", names)


class TestGetLatestVersion(unittest.TestCase):
    """Test the _get_latest_version helper."""

    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    def test_no_changelog_returns_default(self):
        """Returns v0.0.0 when CHANGELOG doesn't exist."""
        from gen_quickstart import _get_latest_version

        version = _get_latest_version()
        self.assertEqual(version, "v0.0.0")

    def test_parses_changelog_version(self):
        """Parses version from CHANGELOG content."""
        import gen_quickstart

        original_changelog = gen_quickstart.CHANGELOG
        gen_quickstart.CHANGELOG = Path("/tmp/test_changelog.md")

        # Write a temporary changelog
        Path("/tmp/test_changelog.md").write_text(SAMPLE_CHANGELOG, encoding="utf-8")

        try:
            version = gen_quickstart._get_latest_version()
            self.assertIn("v0.4.1", version)
        finally:
            gen_quickstart.CHANGELOG = original_changelog
            Path("/tmp/test_changelog.md").unlink(missing_ok=True)


class TestRenderOptionsTable(unittest.TestCase):
    """Test the _render_options_table helper."""

    def test_renders_table(self):
        """Options are rendered as a markdown table."""
        from gen_quickstart import _render_options_table

        options = [
            {"flag": "--json", "description": "Output JSON"},
            {"flag": "--strict-buyer", "description": "Strict mode"},
        ]
        table = _render_options_table(options)
        self.assertIn("| Flag | Description |", table)
        self.assertIn("`--json`", table)
        self.assertIn("`--strict-buyer`", table)

    def test_empty_options(self):
        """Empty options list returns placeholder."""
        from gen_quickstart import _render_options_table

        table = _render_options_table([])
        self.assertIn("No options", table)


class TestRunHelp(unittest.TestCase):
    """Test the _run_help helper."""

    @patch("gen_quickstart.subprocess.run")
    def test_run_help_calls_subprocess(self, mock_run):
        """_run_help calls subprocess.run with correct args."""
        mock_run.return_value = _make_mock_result("help output")

        from gen_quickstart import _run_help

        result = _run_help("provenance_verify.py")
        self.assertEqual(result, "help output")
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        self.assertIn("provenance_verify.py", str(call_args))


class TestMain(unittest.TestCase):
    """Test the main() entry point."""

    @patch("gen_quickstart.subprocess.run")
    @patch("gen_quickstart.CHANGELOG", Path("/nonexistent/CHANGELOG.md"))
    @patch("gen_quickstart.OUTPUT", Path("/tmp/test_quickstart.md"))
    def test_main_writes_file(self, mock_run):
        """main() writes the generated content to the output file."""
        mock_run.side_effect = [
            _make_mock_result(PROVENANCE_HELP),
            _make_mock_result(E2E_HELP),
        ]

        from gen_quickstart import main

        main()

        output_path = Path("/tmp/test_quickstart.md")
        self.assertTrue(output_path.exists())
        content = output_path.read_text(encoding="utf-8")
        self.assertIn("exit 0", content)
        self.assertIn("verify.sh", content)
        self.assertIn("strict-buyer", content)

        # Cleanup
        output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
