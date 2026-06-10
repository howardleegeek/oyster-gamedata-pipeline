#!/usr/bin/env python3
"""
Tests for end_to_end_gate_smoke.py

Uses unittest.mock to patch subprocess.run and verify:
  - all PASS → verdict PASS
  - H8 FAIL → verdict FAIL with H8 in details
  - V2 crashes (CalledProcessError) → verdict FAIL with ERROR status for V2
  - mix PASS + SKIP → verdict PASS
  - --json mode produces valid parseable JSON
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure bin/ is importable
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

from end_to_end_gate_smoke import (
    _compute_verdict,
    _format_table,
    _run_b2_provenance,
    _run_gate,
    main,
)


def _make_mock_result(stdout_json: dict, returncode: int = 0, stderr: str = ""):
    """Create a mock subprocess.CompletedProcess."""
    return MagicMock(
        returncode=returncode,
        stdout=json.dumps(stdout_json),
        stderr=stderr,
    )


class TestRunGate(unittest.TestCase):
    """Test _run_gate with various subprocess outcomes."""

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_gate_pass(self, mock_run):
        """Gate returns PASS JSON."""
        mock_run.return_value = _make_mock_result(
            {"status": "PASS", "evidence": "engine Z-buffer, 720 frames, EXR ok"}
        )
        result = _run_gate("prd_compliance_audit_H8_patch.py", "/tmp/session")
        self.assertEqual(result["status"], "PASS")
        self.assertIn("engine Z-buffer", result["evidence"])

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_gate_fail(self, mock_run):
        """Gate returns FAIL JSON."""
        mock_run.return_value = _make_mock_result(
            {"status": "FAIL", "evidence": "no depth source found"}, returncode=1
        )
        result = _run_gate("prd_compliance_audit_H8_patch.py", "/tmp/session")
        self.assertEqual(result["status"], "FAIL")

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_gate_pass_ok(self, mock_run):
        """Gate returns PASS_OK JSON."""
        mock_run.return_value = _make_mock_result(
            {"status": "PASS_OK", "evidence": "95.2% within 50ms"}
        )
        result = _run_gate("sync_tolerance_gate.py", "/tmp/session")
        self.assertEqual(result["status"], "PASS_OK")

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_gate_timeout(self, mock_run):
        """Gate subprocess times out."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=120)
        result = _run_gate("video_artifact_scanner.py", "/tmp/session")
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("timed out", result["evidence"])

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_gate_exception(self, mock_run):
        """Gate subprocess raises generic exception."""
        mock_run.side_effect = OSError("file not found")
        result = _run_gate("video_quality_gate.py", "/tmp/session")
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("subprocess exception", result["evidence"])

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_gate_nonzero_no_json(self, mock_run):
        """Gate exits non-zero with no valid JSON output."""
        mock_run.return_value = _make_mock_result(
            {"status": "PASS"}, returncode=1, stderr="something went wrong"
        )
        # Gate returned valid JSON even with non-zero exit — should honour status
        result = _run_gate("input_latency_analyzer.py", "/tmp/session")
        self.assertEqual(result["status"], "PASS")

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_gate_nonzero_invalid_json(self, mock_run):
        """Gate exits non-zero with garbage stdout."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="not json at all", stderr="crash dump"
        )
        result = _run_gate("video_quality_gate.py", "/tmp/session")
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("exit code 1", result["evidence"])

    @patch("end_to_end_gate_smoke.subprocess.run")
    def test_gate_valid_json_parse_error(self, mock_run):
        """Gate exits 0 but stdout is not valid JSON."""
        mock_run.return_value = MagicMock(returncode=0, stdout="{broken json", stderr="")
        result = _run_gate("video_artifact_scanner.py", "/tmp/session")
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("JSON parse error", result["evidence"])


class TestRunB2Provenance(unittest.TestCase):
    """Test _run_b2_provenance special handling."""

    @patch("end_to_end_gate_smoke._bin_dir")
    def test_skip_sign(self, mock_bin_dir):
        """--skip-sign returns SKIP."""
        mock_bin_dir.return_value = Path("/tmp/fake-bin")
        result = _run_b2_provenance("/tmp/session", skip_sign=True)
        self.assertEqual(result["status"], "SKIP")
        self.assertIn("--skip-sign", result["evidence"])

    @patch("end_to_end_gate_smoke._bin_dir")
    def test_scripts_not_found(self, mock_bin_dir):
        """When sign/verify scripts don't exist, returns SKIP."""
        mock_bin_dir.return_value = Path("/tmp/nonexistent-bin")
        result = _run_b2_provenance("/tmp/session", skip_sign=False)
        self.assertEqual(result["status"], "SKIP")
        self.assertIn("not found", result["evidence"])


class TestComputeVerdict(unittest.TestCase):
    """Test _compute_verdict logic."""

    def test_all_pass(self):
        gates = {
            "H8_depth_source": {"status": "PASS", "evidence": "ok"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "PASS", "evidence": "ok"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
            "B2_provenance": {"status": "PASS", "evidence": "ok"},
        }
        summary = _compute_verdict(gates)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["pass"], 6)
        self.assertEqual(summary["fail"], 0)
        self.assertEqual(summary["skip"], 0)

    def test_h8_fail(self):
        gates = {
            "H8_depth_source": {"status": "FAIL", "evidence": "no depth"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "PASS", "evidence": "ok"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
            "B2_provenance": {"status": "PASS", "evidence": "ok"},
        }
        summary = _compute_verdict(gates)
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(summary["fail"], 1)

    def test_v2_error_crash(self):
        """V2 crashes (ERROR) → verdict FAIL. ERROR is tracked separately from FAIL."""
        gates = {
            "H8_depth_source": {"status": "PASS", "evidence": "ok"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "PASS", "evidence": "ok"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "ERROR", "evidence": "crashed"},
            "B2_provenance": {"status": "PASS", "evidence": "ok"},
        }
        summary = _compute_verdict(gates)
        self.assertEqual(summary["verdict"], "FAIL")
        # ERROR gates contribute to FAIL verdict but are counted separately
        # The summary shows fail count for explicit FAILs; ERROR is a separate category
        self.assertEqual(summary["fail"], 0)  # no explicit FAIL, but verdict is FAIL due to ERROR

    def test_mix_pass_skip(self):
        gates = {
            "H8_depth_source": {"status": "PASS", "evidence": "ok"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "SKIP", "evidence": "no data"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
            "B2_provenance": {"status": "SKIP", "evidence": "no keys"},
        }
        summary = _compute_verdict(gates)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["pass"], 4)
        self.assertEqual(summary["skip"], 2)
        self.assertEqual(summary["fail"], 0)

    def test_pass_degraded(self):
        gates = {
            "H8_depth_source": {"status": "PASS", "evidence": "ok"},
            "S1_sync_tolerance": {"status": "PASS_DEGRADED", "evidence": "marginal"},
            "S2_input_latency": {"status": "PASS", "evidence": "ok"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
            "B2_provenance": {"status": "PASS", "evidence": "ok"},
        }
        summary = _compute_verdict(gates)
        self.assertEqual(summary["verdict"], "PASS_DEGRADED")

    def test_pass_ok_counted_as_pass(self):
        gates = {
            "H8_depth_source": {"status": "PASS_OK", "evidence": "ok"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "PASS", "evidence": "ok"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
            "B2_provenance": {"status": "PASS", "evidence": "ok"},
        }
        summary = _compute_verdict(gates)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["pass"], 6)

    def test_error_and_fail_together(self):
        gates = {
            "H8_depth_source": {"status": "FAIL", "evidence": "bad"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "ERROR", "evidence": "crash"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
            "B2_provenance": {"status": "PASS", "evidence": "ok"},
        }
        summary = _compute_verdict(gates)
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(summary["fail"], 1)


class TestFormatTable(unittest.TestCase):
    """Test _format_table output."""

    def test_table_contains_verdict(self):
        gates = {
            "H8_depth_source": {
                "status": "PASS",
                "evidence": "engine Z-buffer, 720 frames, EXR ok",
            },
            "S1_sync_tolerance": {
                "status": "PASS_OK",
                "evidence": "95.2% within 50ms, 0.7% > 100ms",
            },
            "S2_input_latency": {"status": "PASS", "evidence": "honest p99 = 91ms (filtered: 38)"},
            "V1_video_quality": {"status": "PASS", "evidence": "hevc 1920x1080 60fps 12.4Mbps"},
            "V2_video_artifacts": {
                "status": "PASS",
                "evidence": "0 freezes, 1 stutter (ratio 0.001)",
            },
            "B2_provenance": {"status": "PASS", "evidence": "sign + verify round-trip OK"},
        }
        summary = {"session_id": "test-session", "pass": 6, "fail": 0, "skip": 0, "verdict": "PASS"}
        table = _format_table(gates, summary)
        self.assertIn("END-TO-END GATE SMOKE — test-session", table)
        self.assertIn("Overall verdict: PASS", table)
        self.assertIn("6 PASS / 0 FAIL / 0 SKIP", table)
        # Check all gate labels appear
        self.assertIn("H8 depth source", table)
        self.assertIn("S1 sync tolerance", table)
        self.assertIn("S2 input latency", table)
        self.assertIn("V1 video quality", table)
        self.assertIn("V2 video artifacts", table)
        self.assertIn("B2 provenance", table)

    def test_table_fail_verdict(self):
        gates = {
            "H8_depth_source": {"status": "FAIL", "evidence": "no depth source"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "PASS", "evidence": "ok"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
            "B2_provenance": {"status": "PASS", "evidence": "ok"},
        }
        summary = {"session_id": "fail-session", "pass": 5, "fail": 1, "skip": 0, "verdict": "FAIL"}
        table = _format_table(gates, summary)
        self.assertIn("Overall verdict: FAIL", table)
        self.assertIn("5 PASS / 1 FAIL / 0 SKIP", table)


class TestJsonMode(unittest.TestCase):
    """Test that --json mode produces valid parseable JSON."""

    @patch("end_to_end_gate_smoke._run_gate")
    @patch("end_to_end_gate_smoke._run_b2_provenance")
    @patch("end_to_end_gate_smoke.os.path.isdir")
    def test_json_output_is_valid(self, mock_isdir, mock_b2, mock_gate):
        """--json flag produces valid JSON on stdout."""
        mock_isdir.return_value = True
        mock_gate.return_value = {"status": "PASS", "evidence": "ok"}
        mock_b2.return_value = {"status": "PASS", "evidence": "round-trip OK"}

        # Capture stdout
        import io

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        old_argv = sys.argv
        sys.argv = [
            "end_to_end_gate_smoke.py",
            "/tmp/test-session",
            "--json",
        ]

        try:
            main()
        except SystemExit:
            pass
        finally:
            sys.stdout = old_stdout
            sys.argv = old_argv

        output = captured.getvalue()
        # Should be valid JSON
        data = json.loads(output)
        self.assertIn("session_id", data)
        self.assertIn("gates", data)
        self.assertIn("summary", data)
        self.assertIn("verdict", data["summary"])

    @patch("end_to_end_gate_smoke._run_gate")
    @patch("end_to_end_gate_smoke._run_b2_provenance")
    @patch("end_to_end_gate_smoke.os.path.isdir")
    def test_json_output_has_all_gates(self, mock_isdir, mock_b2, mock_gate):
        """JSON output contains all 6 gate keys."""
        mock_isdir.return_value = True
        mock_gate.return_value = {"status": "PASS", "evidence": "ok"}
        mock_b2.return_value = {"status": "PASS", "evidence": "round-trip OK"}

        import io

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        old_argv = sys.argv
        sys.argv = [
            "end_to_end_gate_smoke.py",
            "/tmp/test-session",
            "--json",
        ]

        try:
            main()
        except SystemExit:
            pass
        finally:
            sys.stdout = old_stdout
            sys.argv = old_argv

        data = json.loads(captured.getvalue())
        expected_keys = {
            "H8_depth_source",
            "S1_sync_tolerance",
            "S2_input_latency",
            "V1_video_quality",
            "V2_video_artifacts",
            "B2_provenance",
        }
        self.assertEqual(set(data["gates"].keys()), expected_keys)


class TestEndToEndIntegration(unittest.TestCase):
    """Integration-style tests with full subprocess mocking."""

    def _mock_all_gates(self, statuses: dict):
        """
        Helper to mock subprocess.run for all gates.

        statuses: dict mapping gate key -> {"status": ..., "evidence": ...}
        """
        gate_script_map = {
            "H8_depth_source": "prd_compliance_audit_H8_patch.py",
            "S1_sync_tolerance": "sync_tolerance_gate.py",
            "S2_input_latency": "input_latency_analyzer.py",
            "V1_video_quality": "video_quality_gate.py",
            "V2_video_artifacts": "video_artifact_scanner.py",
        }

        def side_effect(cmd, **kwargs):
            # Determine which gate is being called
            script_name = os.path.basename(cmd[1]) if len(cmd) > 1 else ""
            for key, script in gate_script_map.items():
                if script == script_name:
                    info = statuses.get(key, {"status": "PASS", "evidence": "ok"})
                    return _make_mock_result(info)
            # B2 provenance scripts
            if "provenance_sign" in " ".join(cmd):
                return _make_mock_result({"status": "PASS", "evidence": "signed"})
            if "provenance_verify" in " ".join(cmd):
                return _make_mock_result({"status": "PASS", "evidence": "verified"})
            return _make_mock_result({"status": "PASS", "evidence": "ok"})

        return side_effect

    @patch("end_to_end_gate_smoke.subprocess.run")
    @patch("end_to_end_gate_smoke._bin_dir")
    @patch("end_to_end_gate_smoke.os.path.isdir")
    def test_all_pass_integration(self, mock_isdir, mock_bin_dir, mock_run):
        """All gates PASS → overall PASS."""
        mock_isdir.return_value = True
        mock_bin_dir.return_value = Path("/tmp/fake-bin")

        all_pass = {
            "H8_depth_source": {
                "status": "PASS",
                "evidence": "engine Z-buffer, 720 frames, EXR ok",
            },
            "S1_sync_tolerance": {"status": "PASS_OK", "evidence": "95.2% within 50ms"},
            "S2_input_latency": {"status": "PASS", "evidence": "honest p99 = 91ms"},
            "V1_video_quality": {"status": "PASS", "evidence": "hevc 1920x1080 60fps"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "0 freezes"},
        }
        mock_run.side_effect = self._mock_all_gates(all_pass)

        # Run B2 separately since it has special handling
        with patch("end_to_end_gate_smoke._run_b2_provenance") as mock_b2:
            mock_b2.return_value = {"status": "PASS", "evidence": "sign + verify round-trip OK"}

            import io

            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured

            old_argv = sys.argv
            sys.argv = ["end_to_end_gate_smoke.py", "/tmp/session"]

            try:
                main()
            except SystemExit:
                pass
            finally:
                sys.stdout = old_stdout
                sys.argv = old_argv

            output = captured.getvalue()
            self.assertIn("Overall verdict: PASS", output)
            self.assertIn("6 PASS / 0 FAIL / 0 SKIP", output)

    @patch("end_to_end_gate_smoke.subprocess.run")
    @patch("end_to_end_gate_smoke._bin_dir")
    @patch("end_to_end_gate_smoke.os.path.isdir")
    def test_h8_fail_integration(self, mock_isdir, mock_bin_dir, mock_run):
        """H8 FAIL → overall FAIL with H8 in details."""
        mock_isdir.return_value = True
        mock_bin_dir.return_value = Path("/tmp/fake-bin")

        h8_fail = {
            "H8_depth_source": {"status": "FAIL", "evidence": "no depth source found"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "PASS", "evidence": "ok"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
        }
        mock_run.side_effect = self._mock_all_gates(h8_fail)

        with patch("end_to_end_gate_smoke._run_b2_provenance") as mock_b2:
            mock_b2.return_value = {"status": "PASS", "evidence": "round-trip OK"}

            import io

            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured

            old_argv = sys.argv
            sys.argv = ["end_to_end_gate_smoke.py", "/tmp/session"]

            try:
                main()
            except SystemExit:
                pass
            finally:
                sys.stdout = old_stdout
                sys.argv = old_argv

            output = captured.getvalue()
            self.assertIn("Overall verdict: FAIL", output)
            self.assertIn("H8 depth source", output)
            self.assertIn("FAIL", output)

    @patch("end_to_end_gate_smoke.subprocess.run")
    @patch("end_to_end_gate_smoke._bin_dir")
    @patch("end_to_end_gate_smoke.os.path.isdir")
    def test_v2_crash_integration(self, mock_isdir, mock_bin_dir, mock_run):
        """V2 crashes (CalledProcessError) → verdict FAIL with ERROR status."""
        mock_isdir.return_value = True
        mock_bin_dir.return_value = Path("/tmp/fake-bin")

        def side_effect(cmd, **kwargs):
            script_name = os.path.basename(cmd[1]) if len(cmd) > 1 else ""
            if script_name == "video_artifact_scanner.py":
                raise subprocess.CalledProcessError(returncode=139, cmd=cmd, stderr="segfault")
            # Other gates pass
            return _make_mock_result({"status": "PASS", "evidence": "ok"})

        mock_run.side_effect = side_effect

        with patch("end_to_end_gate_smoke._run_b2_provenance") as mock_b2:
            mock_b2.return_value = {"status": "PASS", "evidence": "round-trip OK"}

            import io

            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured

            old_argv = sys.argv
            sys.argv = ["end_to_end_gate_smoke.py", "/tmp/session"]

            try:
                main()
            except SystemExit:
                pass
            finally:
                sys.stdout = old_stdout
                sys.argv = old_argv

            output = captured.getvalue()
            self.assertIn("Overall verdict: FAIL", output)
            self.assertIn("V2 video artifacts", output)
            self.assertIn("ERROR", output)

    @patch("end_to_end_gate_smoke.subprocess.run")
    @patch("end_to_end_gate_smoke._bin_dir")
    @patch("end_to_end_gate_smoke.os.path.isdir")
    def test_mix_pass_skip_integration(self, mock_isdir, mock_bin_dir, mock_run):
        """Mix PASS + SKIP → verdict PASS (SKIP not counted as FAIL)."""
        mock_isdir.return_value = True
        mock_bin_dir.return_value = Path("/tmp/fake-bin")

        mixed = {
            "H8_depth_source": {"status": "PASS", "evidence": "ok"},
            "S1_sync_tolerance": {"status": "PASS", "evidence": "ok"},
            "S2_input_latency": {"status": "SKIP", "evidence": "no input data"},
            "V1_video_quality": {"status": "PASS", "evidence": "ok"},
            "V2_video_artifacts": {"status": "PASS", "evidence": "ok"},
        }
        mock_run.side_effect = self._mock_all_gates(mixed)

        with patch("end_to_end_gate_smoke._run_b2_provenance") as mock_b2:
            mock_b2.return_value = {"status": "SKIP", "evidence": "--skip-sign requested"}

            import io

            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured

            old_argv = sys.argv
            sys.argv = ["end_to_end_gate_smoke.py", "/tmp/session", "--skip-sign"]

            try:
                main()
            except SystemExit:
                pass
            finally:
                sys.stdout = old_stdout
                sys.argv = old_argv

            output = captured.getvalue()
            self.assertIn("Overall verdict: PASS", output)
            self.assertIn("SKIP", output)


class TestInvalidSessionDir(unittest.TestCase):
    """Test error handling for invalid session directory."""

    @patch("end_to_end_gate_smoke.os.path.isdir")
    def test_nonexistent_session_dir(self, mock_isdir):
        """Non-existent session dir → error message and exit."""
        mock_isdir.return_value = False

        import io

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured

        old_argv = sys.argv
        sys.argv = [
            "end_to_end_gate_smoke.py",
            "/tmp/nonexistent-session",
        ]

        try:
            main()
        except SystemExit:
            pass
        finally:
            sys.stderr = old_stderr
            sys.argv = old_argv

        self.assertIn("does not exist", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
