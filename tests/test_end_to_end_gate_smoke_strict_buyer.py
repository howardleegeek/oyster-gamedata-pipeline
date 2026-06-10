#!/usr/bin/env python3
"""
Tests for --strict-buyer evidence provenance (S06).

Covers:
  - Synthetic fixture session → exit 2 (STRICT_GATES_PASS_SYNTHETIC)
  - Real session (H8 engine_zbuffer + EXR > 1MB) → exit 0 (BUYER_READY)
  - Real session (OysterClips/finalized/ path) → exit 0 (BUYER_READY)
  - Any strict gate FAIL → exit 1 (STRICT_VIOLATIONS)
  - evidence_provenance field present in JSON output
  - Non-integer video duration → real
  - Unknown provenance → treated as synthetic (exit 2)
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure bin/ is importable
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

from end_to_end_gate_smoke import (
    _compute_strict_buyer_verdict,
    _compute_verdict,
    _detect_evidence_provenance,
    _detect_h8_real,
    _detect_video_non_integer_duration,
    main,
)


def _make_mock_result(stdout_json: dict, returncode: int = 0, stderr: str = ""):
    """Create a mock subprocess.CompletedProcess."""
    return MagicMock(
        returncode=returncode,
        stdout=json.dumps(stdout_json),
        stderr=stderr,
    )


def _all_pass_gates():
    """Return a gates_result dict where all gates PASS."""
    return {
        "H8_depth_source": {"status": "PASS", "evidence": "engine Z-buffer, EXR ok"},
        "S1_sync_tolerance": {"status": "PASS", "evidence": "98.1% within 50ms"},
        "S2_input_latency": {"status": "PASS", "evidence": "avg 12ms"},
        "V1_video_quality": {"status": "PASS", "evidence": "PSNR 42dB"},
        "V2_video_artifacts": {"status": "PASS", "evidence": "no artifacts"},
        "B2_provenance": {"status": "PASS", "evidence": "sign + verify round-trip OK"},
    }


def _all_pass_with_one_fail():
    """Return a gates_result dict where V1 FAILs."""
    gates = _all_pass_gates()
    gates["V1_video_quality"] = {"status": "FAIL", "evidence": "PSNR below threshold"}
    return gates


def _all_pass_with_skip():
    """Return a gates_result dict where H8 SKIPs."""
    gates = _all_pass_gates()
    gates["H8_depth_source"] = {"status": "SKIP", "evidence": "no depth/.source"}
    return gates


class TestDetectH8Real(unittest.TestCase):
    """Test Rule 1: H8 marker kind=engine_zbuffer + EXR > 1MB."""

    def test_h8_real_with_large_exr(self):
        """engine_zbuffer marker + EXR > 1MB → True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            depth = Path(tmpdir) / "depth"
            depth.mkdir()
            marker = depth / ".source"
            marker.write_text(
                json.dumps(
                    {
                        "kind": "engine_zbuffer",
                        "frame_count": 720,
                        "gap_miss_ratio": 0.0,
                    }
                )
            )
            # Create a fake EXR file > 1MB
            exr = depth / "frame_000.exr"
            exr.write_bytes(b"\x00" * 1_100_000)

            self.assertTrue(_detect_h8_real(Path(tmpdir)))

    def test_h8_not_real_small_exr(self):
        """engine_zbuffer marker but EXR < 1MB → False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            depth = Path(tmpdir) / "depth"
            depth.mkdir()
            marker = depth / ".source"
            marker.write_text(
                json.dumps(
                    {
                        "kind": "engine_zbuffer",
                        "frame_count": 720,
                    }
                )
            )
            exr = depth / "frame_000.exr"
            exr.write_bytes(b"\x00" * 500_000)  # 500KB

            self.assertFalse(_detect_h8_real(Path(tmpdir)))

    def test_h8_not_real_monocular(self):
        """kind=monocular_da_v2 → False regardless of EXR size."""
        with tempfile.TemporaryDirectory() as tmpdir:
            depth = Path(tmpdir) / "depth"
            depth.mkdir()
            marker = depth / ".source"
            marker.write_text(
                json.dumps(
                    {
                        "kind": "monocular_da_v2",
                        "frame_count": 720,
                    }
                )
            )
            exr = depth / "frame_000.exr"
            exr.write_bytes(b"\x00" * 2_000_000)  # 2MB

            self.assertFalse(_detect_h8_real(Path(tmpdir)))

    def test_h8_no_marker(self):
        """No depth/.source → False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_detect_h8_real(Path(tmpdir)))

    def test_h8_no_exr_files(self):
        """engine_zbuffer but no EXR files → False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            depth = Path(tmpdir) / "depth"
            depth.mkdir()
            marker = depth / ".source"
            marker.write_text(
                json.dumps(
                    {
                        "kind": "engine_zbuffer",
                        "frame_count": 720,
                    }
                )
            )
            self.assertFalse(_detect_h8_real(Path(tmpdir)))


class TestDetectVideoNonIntegerDuration(unittest.TestCase):
    """Test Rule 2: ffprobe duration non-integer → real."""

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_non_integer_duration_is_real(self, mock_run):
        """Duration 30.5s → real."""
        mock_run.return_value = _make_mock_result({"format": {"duration": "30.5"}})
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "recording.mp4").touch()
            self.assertTrue(_detect_video_non_integer_duration(Path(tmpdir)))

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_integer_duration_not_real(self, mock_run):
        """Duration 1.0s (synthetic) → not real."""
        mock_run.return_value = _make_mock_result({"format": {"duration": "1.0"}})
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "recording.mp4").touch()
            self.assertFalse(_detect_video_non_integer_duration(Path(tmpdir)))

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_no_recording_file(self, mock_run):
        """No recording.mp4 → not real."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_detect_video_non_integer_duration(Path(tmpdir)))
            mock_run.assert_not_called()


class TestDetectEvidenceProvenance(unittest.TestCase):
    """Test the full provenance detection pipeline."""

    def test_synthetic_fixture_path(self):
        """Path containing tests/fixtures/ → synthetic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixtures_dir = Path(tmpdir) / "tests" / "fixtures" / "session_001"
            fixtures_dir.mkdir(parents=True)
            result = _detect_evidence_provenance(str(fixtures_dir))
            self.assertEqual(result, "synthetic")

    def test_tmp_path(self):
        """Path containing /tmp/ → synthetic."""
        # Use actual /tmp/ path (not tempfile.TemporaryDirectory which may use /var/folders on macOS)

        tmp_session = os.path.join("/tmp", "test_session_s06")
        os.makedirs(tmp_session, exist_ok=True)
        try:
            result = _detect_evidence_provenance(tmp_session)
            self.assertEqual(result, "synthetic")
        finally:
            os.rmdir(tmp_session)

    def test_oysterclips_finalized_path(self):
        """Path containing OysterClips/finalized/ → real."""
        with tempfile.TemporaryDirectory() as tmpdir:
            oyster_dir = Path(tmpdir) / "OysterClips" / "finalized" / "session_42"
            oyster_dir.mkdir(parents=True)
            result = _detect_evidence_provenance(str(oyster_dir))
            self.assertEqual(result, "real")

    def test_h8_engine_zbuffer_real(self):
        """H8 engine_zbuffer + large EXR → real."""
        with tempfile.TemporaryDirectory() as tmpdir:
            depth = Path(tmpdir) / "depth"
            depth.mkdir()
            marker = depth / ".source"
            marker.write_text(
                json.dumps(
                    {
                        "kind": "engine_zbuffer",
                        "frame_count": 720,
                    }
                )
            )
            exr = depth / "frame_000.exr"
            exr.write_bytes(b"\x00" * 1_100_000)
            result = _detect_evidence_provenance(tmpdir)
            self.assertEqual(result, "real")

    def test_unknown_provenance(self):
        """No matching rules → unknown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use a path that doesn't match any rule
            custom_dir = Path(tmpdir) / "custom_sessions" / "real_session"
            custom_dir.mkdir(parents=True)
            result = _detect_evidence_provenance(str(custom_dir))
            self.assertEqual(result, "unknown")


class TestComputeStrictBuyerVerdict(unittest.TestCase):
    """Test the three-tier strict-buyer verdict computation."""

    def test_buyer_ready_real_evidence(self):
        """All strict gates PASS + real evidence → BUYER_READY, exit 0."""
        gates = _all_pass_gates()
        result = _compute_strict_buyer_verdict(gates, "real")
        self.assertEqual(result["verdict"], "BUYER_READY")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["evidence_provenance"], "real")

    def test_synthetic_gates_pass(self):
        """All strict gates PASS + synthetic evidence → STRICT_GATES_PASS_SYNTHETIC, exit 2."""
        gates = _all_pass_gates()
        result = _compute_strict_buyer_verdict(gates, "synthetic")
        self.assertEqual(result["verdict"], "STRICT_GATES_PASS_SYNTHETIC")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["evidence_provenance"], "synthetic")

    def test_unknown_treated_as_synthetic(self):
        """All strict gates PASS + unknown evidence → STRICT_GATES_PASS_SYNTHETIC, exit 2."""
        gates = _all_pass_gates()
        result = _compute_strict_buyer_verdict(gates, "unknown")
        self.assertEqual(result["verdict"], "STRICT_GATES_PASS_SYNTHETIC")
        self.assertEqual(result["exit_code"], 2)

    def test_strict_violations_fail(self):
        """Any strict gate FAIL → STRICT_VIOLATIONS, exit 1."""
        gates = _all_pass_with_one_fail()
        result = _compute_strict_buyer_verdict(gates, "real")
        self.assertEqual(result["verdict"], "STRICT_VIOLATIONS")
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("strict_violations", result)

    def test_strict_violations_skip(self):
        """Any strict gate SKIP → STRICT_VIOLATIONS, exit 1."""
        gates = _all_pass_with_skip()
        result = _compute_strict_buyer_verdict(gates, "real")
        self.assertEqual(result["verdict"], "STRICT_VIOLATIONS")
        self.assertEqual(result["exit_code"], 1)

    def test_pass_ok_counts_as_pass(self):
        """PASS_OK on strict gates counts as PASS."""
        gates = _all_pass_gates()
        gates["H8_depth_source"]["status"] = "PASS_OK"
        result = _compute_strict_buyer_verdict(gates, "real")
        self.assertEqual(result["verdict"], "BUYER_READY")
        self.assertEqual(result["exit_code"], 0)


class TestStrictBuyerIntegration(unittest.TestCase):
    """Integration tests for --strict-buyer mode via main()."""

    def _run_main_with_mocks(
        self,
        session_dir,
        strict_buyer=True,
        gates_result=None,
        json_output=True,
        skip_sign=True,
    ):
        """Helper to run main() with mocked gate execution."""
        if gates_result is None:
            gates_result = _all_pass_gates()

        with (
            patch("end_to_end_gate_smoke._run_gate") as mock_gate,
            patch("end_to_end_gate_smoke._run_b2_provenance") as mock_b2,
            patch("end_to_end_gate_smoke.os.path.isdir", return_value=True),
        ):

            # Set up gate mocks
            gate_map = {
                "H8_depth_source": "prd_compliance_audit_H8_patch.py",
                "S1_sync_tolerance": "sync_tolerance_gate.py",
                "S2_input_latency": "input_latency_analyzer.py",
                "V1_video_quality": "video_quality_gate.py",
                "V2_video_artifacts": "video_artifact_scanner.py",
            }

            def gate_side_effect(script, session):
                for key, script_name in gate_map.items():
                    if script == script_name:
                        return gates_result.get(key, {"status": "SKIP", "evidence": "n/a"})
                return {"status": "SKIP", "evidence": "unknown gate"}

            mock_gate.side_effect = gate_side_effect
            mock_b2.return_value = gates_result.get(
                "B2_provenance", {"status": "PASS", "evidence": "ok"}
            )

            import io

            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured

            old_argv = sys.argv
            argv = ["end_to_end_gate_smoke.py", session_dir]
            if json_output:
                argv.append("--json")
            if skip_sign:
                argv.append("--skip-sign")
            if strict_buyer:
                argv.append("--strict-buyer")
            sys.argv = argv

            exit_code = None
            try:
                main()
            except SystemExit as e:
                exit_code = e.code
            finally:
                sys.stdout = old_stdout
                sys.argv = old_argv

            output = captured.getvalue()
            return exit_code, output

    def test_synthetic_fixture_exit_2(self):
        """Synthetic fixture with all gates PASS → exit 2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a path that looks like a fixture
            fixture_dir = Path(tmpdir) / "tests" / "fixtures" / "synthetic_session"
            fixture_dir.mkdir(parents=True)

            exit_code, output = self._run_main_with_mocks(str(fixture_dir), strict_buyer=True)
            self.assertEqual(exit_code, 2)
            data = json.loads(output)
            self.assertEqual(data["summary"]["evidence_provenance"], "synthetic")
            self.assertEqual(data["summary"]["strict_buyer_verdict"], "STRICT_GATES_PASS_SYNTHETIC")

    def test_real_session_exit_0(self):
        """Real session (H8 engine_zbuffer + large EXR) with all gates PASS → exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a real-looking session
            depth = Path(tmpdir) / "depth"
            depth.mkdir()
            marker = depth / ".source"
            marker.write_text(
                json.dumps(
                    {
                        "kind": "engine_zbuffer",
                        "frame_count": 720,
                    }
                )
            )
            exr = depth / "frame_000.exr"
            exr.write_bytes(b"\x00" * 1_100_000)

            exit_code, output = self._run_main_with_mocks(str(tmpdir), strict_buyer=True)
            self.assertEqual(exit_code, 0)
            data = json.loads(output)
            self.assertEqual(data["summary"]["evidence_provenance"], "real")
            self.assertEqual(data["summary"]["strict_buyer_verdict"], "BUYER_READY")

    def test_oysterclips_finalized_exit_0(self):
        """OysterClips/finalized/ path with all gates PASS → exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            oyster_dir = Path(tmpdir) / "OysterClips" / "finalized" / "session_42"
            oyster_dir.mkdir(parents=True)

            exit_code, output = self._run_main_with_mocks(str(oyster_dir), strict_buyer=True)
            self.assertEqual(exit_code, 0)
            data = json.loads(output)
            self.assertEqual(data["summary"]["evidence_provenance"], "real")
            self.assertEqual(data["summary"]["strict_buyer_verdict"], "BUYER_READY")

    def test_fail_exit_1(self):
        """Any strict gate FAIL → exit 1 (STRICT_VIOLATIONS)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Make it look like a real session so provenance doesn't interfere
            depth = Path(tmpdir) / "depth"
            depth.mkdir()
            marker = depth / ".source"
            marker.write_text(
                json.dumps(
                    {
                        "kind": "engine_zbuffer",
                        "frame_count": 720,
                    }
                )
            )
            exr = depth / "frame_000.exr"
            exr.write_bytes(b"\x00" * 1_100_000)

            exit_code, output = self._run_main_with_mocks(
                str(tmpdir), strict_buyer=True, gates_result=_all_pass_with_one_fail()
            )
            self.assertEqual(exit_code, 1)
            data = json.loads(output)
            self.assertEqual(data["summary"]["strict_buyer_verdict"], "STRICT_VIOLATIONS")

    def test_skip_exit_1(self):
        """Any strict gate SKIP → exit 1 (STRICT_VIOLATIONS)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            depth = Path(tmpdir) / "depth"
            depth.mkdir()
            marker = depth / ".source"
            marker.write_text(
                json.dumps(
                    {
                        "kind": "engine_zbuffer",
                        "frame_count": 720,
                    }
                )
            )
            exr = depth / "frame_000.exr"
            exr.write_bytes(b"\x00" * 1_100_000)

            exit_code, output = self._run_main_with_mocks(
                str(tmpdir), strict_buyer=True, gates_result=_all_pass_with_skip()
            )
            self.assertEqual(exit_code, 1)
            data = json.loads(output)
            self.assertEqual(data["summary"]["strict_buyer_verdict"], "STRICT_VIOLATIONS")

    def test_json_has_evidence_provenance_field(self):
        """JSON output must contain evidence_provenance field in strict-buyer mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_dir = Path(tmpdir) / "tests" / "fixtures" / "session_x"
            fixture_dir.mkdir(parents=True)

            _, output = self._run_main_with_mocks(str(fixture_dir), strict_buyer=True)
            data = json.loads(output)
            self.assertIn("evidence_provenance", data["summary"])
            self.assertIn(data["summary"]["evidence_provenance"], ("real", "synthetic", "unknown"))

    def test_non_strict_mode_no_provenance(self):
        """Non-strict-buyer mode should NOT include evidence_provenance in summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_dir = Path(tmpdir) / "tests" / "fixtures" / "session_x"
            fixture_dir.mkdir(parents=True)

            exit_code, output = self._run_main_with_mocks(str(fixture_dir), strict_buyer=False)
            data = json.loads(output)
            self.assertNotIn("evidence_provenance", data["summary"])
            self.assertNotIn("strict_buyer_verdict", data["summary"])
            # Should exit 0 since all gates PASS
            self.assertEqual(exit_code, 0)


class TestBackwardCompatibility(unittest.TestCase):
    """Ensure existing _compute_verdict behavior is unchanged."""

    def test_default_pass(self):
        """All PASS → verdict PASS."""
        gates = _all_pass_gates()
        result = _compute_verdict(gates, strict_buyer=False)
        self.assertEqual(result["verdict"], "PASS")

    def test_default_fail(self):
        """Any FAIL → verdict FAIL."""
        gates = _all_pass_with_one_fail()
        result = _compute_verdict(gates, strict_buyer=False)
        self.assertEqual(result["verdict"], "FAIL")

    def test_default_skip_ok(self):
        """SKIP without strict-buyer → verdict PASS."""
        gates = _all_pass_with_skip()
        result = _compute_verdict(gates, strict_buyer=False)
        self.assertEqual(result["verdict"], "PASS")

    def test_strict_buyer_skip_is_fail(self):
        """SKIP with strict-buyer → verdict FAIL."""
        gates = _all_pass_with_skip()
        result = _compute_verdict(gates, strict_buyer=True)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIn("strict_violations", result)


if __name__ == "__main__":
    unittest.main()
