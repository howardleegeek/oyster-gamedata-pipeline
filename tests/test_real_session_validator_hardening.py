#!/usr/bin/env python3
"""
Hardening tests for bin/real_session_validator.py

Covers:
- --continue-on-error flag: session failure doesn't block remaining sessions
- --output report.json: writes correct sweep JSON schema
- --sample N: random sampling of sessions
- Gate timeout → FAIL_TIMEOUT verdict
- Per-session duration_s tracking
"""

import contextlib
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

# Ensure bin/ is on path
BIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

import real_session_validator as rsv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_fake_session(
    root: pathlib.Path,
    name: str,
    files: list[str] | None = None,
    with_manifest: bool = False,
):
    """Create a fake session directory with required files."""
    session_dir = root / name
    session_dir.mkdir(parents=True, exist_ok=True)
    required = files or ["recording.mp4", "game_state.jsonl"]
    for f in required:
        (session_dir / f).write_text("fake")
    (session_dir / ".session_complete").write_text("{}")
    if with_manifest:
        (session_dir / "MANIFEST.json").write_text("{}")
    return session_dir


def make_subprocess_mock_for_timeout(
    pipeline_results: dict,
    gate_results: dict,
    sign_results: dict | None = None,
    verify_results: dict | None = None,
):
    """
    Create a subprocess.run mock that can simulate timeouts.

    pipeline_results: {session_name: exit_code | "TIMEOUT"}
    gate_results: {session_name: {"overall": ..., "gates": {...}} | "TIMEOUT"}
    """

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd)
        # Extract session name
        session_name = None
        for part in cmd:
            if "session_" in part:
                session_name = pathlib.Path(part).name
                break

        if "canonical_pipeline.py" in cmd_str:
            val = pipeline_results.get(session_name, 0)
            if val == "TIMEOUT":
                raise subprocess.TimeoutExpired(cmd, 600)
            return mock.MagicMock(returncode=val, stdout="", stderr="")

        elif "end_to_end_gate_smoke.py" in cmd_str:
            val = gate_results.get(session_name, {"overall": "PASS", "gates": {}})
            if val == "TIMEOUT":
                raise subprocess.TimeoutExpired(cmd, 60)
            return mock.MagicMock(
                returncode=0,
                stdout=json.dumps(val),
                stderr="",
            )

        elif "provenance_sign.py" in cmd_str:
            exit_code = (sign_results or {}).get(session_name, 0)
            if exit_code == "TIMEOUT":
                raise subprocess.TimeoutExpired(cmd, 30)
            return mock.MagicMock(returncode=exit_code, stdout="", stderr="")

        elif "provenance_verify.py" in cmd_str:
            exit_code = (verify_results or {}).get(session_name, 0)
            if exit_code == "TIMEOUT":
                raise subprocess.TimeoutExpired(cmd, 30)
            return mock.MagicMock(returncode=exit_code, stdout="", stderr="")

        return mock.MagicMock(returncode=0, stdout="", stderr="")

    return mock_run


# ---------------------------------------------------------------------------
# Test: --continue-on-error
# ---------------------------------------------------------------------------


class TestContinueOnError(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/test_rsv_hardening_coe")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        for i in range(1, 6):
            make_fake_session(self.root, f"session_{i:03d}")

    def tearDown(self):
        import shutil

        if self.root.exists():
            shutil.rmtree(self.root)

    def test_continue_on_error_processes_all_sessions(self):
        """With --continue-on-error, all sessions are processed even if one fails."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={
                "session_001": 0,
                "session_002": 1,  # pipeline blocked → FAIL
                "session_003": 0,
                "session_004": 0,
                "session_005": 0,
            },
            gate_results={
                "session_001": {"overall": "PASS", "gates": {}},
                "session_003": {"overall": "PASS", "gates": {}},
                "session_004": {"overall": "PASS", "gates": {}},
                "session_005": {"overall": "PASS", "gates": {}},
            },
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with (
            redirect_stdout(f),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--continue-on-error",
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            try:
                rsv.main()
            except SystemExit as e:
                self.assertEqual(e.code, 1)  # still exits 1 because there's a FAIL

        output = f.getvalue()
        # All 5 sessions should appear in output
        self.assertIn("session_001", output)
        self.assertIn("session_002", output)
        self.assertIn("session_003", output)
        self.assertIn("session_004", output)
        self.assertIn("session_005", output)

    def test_without_continue_on_error_stops_at_first_fail(self):
        """Without --continue-on-error, processing stops at first FAIL."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={
                "session_001": 0,
                "session_002": 1,  # FAIL
                "session_003": 0,
                "session_004": 0,
                "session_005": 0,
            },
            gate_results={
                "session_001": {"overall": "PASS", "gates": {}},
            },
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with (
            redirect_stdout(f),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            try:
                rsv.main()
            except SystemExit as e:
                self.assertEqual(e.code, 1)

        output = f.getvalue()
        # session_001 and session_002 should appear, but not 003+
        self.assertIn("session_001", output)
        self.assertIn("session_002", output)
        self.assertNotIn("session_003", output)
        self.assertNotIn("session_004", output)
        self.assertNotIn("session_005", output)


# ---------------------------------------------------------------------------
# Test: --output JSON schema
# ---------------------------------------------------------------------------


class TestOutputJsonSchema(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/test_rsv_hardening_output")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        for i in range(1, 4):
            make_fake_session(self.root, f"session_{i:03d}", with_manifest=True)
        self.output_path = self.root / "report.json"

    def tearDown(self):
        import shutil

        if self.root.exists():
            shutil.rmtree(self.root)

    def test_output_json_schema(self):
        """--output writes JSON conforming to the hardened schema."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={
                "session_001": 0,
                "session_002": 0,
                "session_003": 1,
            },
            gate_results={
                "session_001": {"overall": "PASS", "gates": {}},
                "session_002": {"overall": "PASS", "gates": {}},
            },
            sign_results={
                "session_001": 0,
                "session_002": 0,
            },
            verify_results={
                "session_001": 0,
                "session_002": 0,
            },
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with (
            redirect_stdout(f),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--continue-on-error",
                    "--keyfile",
                    "/tmp/fake.key",
                    "--output",
                    str(self.output_path),
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            with contextlib.suppress(SystemExit):
                rsv.main()

        self.assertTrue(self.output_path.exists())
        data = json.loads(self.output_path.read_text())

        # Validate schema fields
        self.assertIn("sweep_started", data)
        self.assertIn("sweep_finished", data)
        self.assertIn("sessions_total", data)
        self.assertIn("sessions_buyer_ready", data)
        self.assertIn("sessions_strict_violations", data)
        self.assertIn("sessions_timeout", data)
        self.assertIn("per_session", data)

        # Validate types
        self.assertIsInstance(data["sweep_started"], str)
        self.assertIsInstance(data["sweep_finished"], str)
        self.assertIsInstance(data["sessions_total"], int)
        self.assertIsInstance(data["sessions_buyer_ready"], int)
        self.assertIsInstance(data["sessions_strict_violations"], int)
        self.assertIsInstance(data["sessions_timeout"], int)
        self.assertIsInstance(data["per_session"], list)

        # Validate values
        self.assertEqual(data["sessions_total"], 3)
        self.assertEqual(data["sessions_buyer_ready"], 2)
        self.assertEqual(data["sessions_strict_violations"], 1)
        self.assertEqual(data["sessions_timeout"], 0)

        # Validate per_session entries
        self.assertEqual(len(data["per_session"]), 3)
        for entry in data["per_session"]:
            self.assertIn("session_id", entry)
            self.assertIn("verdict", entry)
            self.assertIn("duration_s", entry)
            self.assertIsInstance(entry["duration_s"], float)

        # Check verdict mapping
        verdicts = {e["session_id"]: e["verdict"] for e in data["per_session"]}
        self.assertEqual(verdicts["session_001"], "BUYER_READY")
        self.assertEqual(verdicts["session_002"], "BUYER_READY")
        self.assertEqual(verdicts["session_003"], "FAIL")

    def test_output_json_iso8601_timestamps(self):
        """sweep_started and sweep_finished are ISO8601 format."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={"session_001": 0},
            gate_results={"session_001": {"overall": "PASS", "gates": {}}},
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with (
            redirect_stdout(f),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--output",
                    str(self.output_path),
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            with contextlib.suppress(SystemExit):
                rsv.main()

        data = json.loads(self.output_path.read_text())
        # ISO8601 check: should contain 'T' and end with 'Z'
        self.assertIn("T", data["sweep_started"])
        self.assertTrue(data["sweep_started"].endswith("Z"))
        self.assertIn("T", data["sweep_finished"])
        self.assertTrue(data["sweep_finished"].endswith("Z"))


# ---------------------------------------------------------------------------
# Test: --sample N
# ---------------------------------------------------------------------------


class TestSample(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/test_rsv_hardening_sample")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        for i in range(1, 11):
            make_fake_session(self.root, f"session_{i:03d}")

    def tearDown(self):
        import shutil

        if self.root.exists():
            shutil.rmtree(self.root)

    def test_sample_reduces_sessions(self):
        """--sample 3 should only process 3 sessions out of 10."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={f"session_{i:03d}": 0 for i in range(1, 11)},
            gate_results={
                f"session_{i:03d}": {"overall": "PASS", "gates": {}} for i in range(1, 11)
            },
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with (
            redirect_stdout(f),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--sample",
                    "3",
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            try:
                rsv.main()
            except SystemExit as e:
                self.assertEqual(e.code, 0)

        output = f.getvalue()
        # Count how many session names appear
        session_count = sum(1 for i in range(1, 11) if f"session_{i:03d}" in output)
        self.assertEqual(session_count, 3)

    def test_sample_larger_than_total_uses_all(self):
        """--sample 20 with only 10 sessions should use all 10."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={f"session_{i:03d}": 0 for i in range(1, 11)},
            gate_results={
                f"session_{i:03d}": {"overall": "PASS", "gates": {}} for i in range(1, 11)
            },
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with (
            redirect_stdout(f),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--sample",
                    "20",
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            try:
                rsv.main()
            except SystemExit as e:
                self.assertEqual(e.code, 0)

        output = f.getvalue()
        # All 10 sessions should appear
        for i in range(1, 11):
            self.assertIn(f"session_{i:03d}", output)

    def test_sample_is_random(self):
        """Multiple runs with --sample should produce different subsets (probabilistic)."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={f"session_{i:03d}": 0 for i in range(1, 11)},
            gate_results={
                f"session_{i:03d}": {"overall": "PASS", "gates": {}} for i in range(1, 11)
            },
        )

        import io
        from contextlib import redirect_stdout

        seen_sessions = set()
        for _ in range(5):
            f = io.StringIO()
            with (
                redirect_stdout(f),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "real_session_validator.py",
                        "--sessions-root",
                        str(self.root),
                        "--sample",
                        "3",
                    ],
                ),
                mock.patch("subprocess.run", side_effect=mock_run),
            ):
                with contextlib.suppress(SystemExit):
                    rsv.main()

            output = f.getvalue()
            for i in range(1, 11):
                if f"session_{i:03d}" in output:
                    seen_sessions.add(f"session_{i:03d}")

        # Over 5 runs of sampling 3 from 10, we should see more than 3 unique sessions
        self.assertGreater(len(seen_sessions), 3)


# ---------------------------------------------------------------------------
# Test: Gate timeout → FAIL_TIMEOUT
# ---------------------------------------------------------------------------


class TestGateTimeout(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/test_rsv_hardening_timeout")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        make_fake_session(self.root, "session_timeout")
        make_fake_session(self.root, "session_ok")

    def tearDown(self):
        import shutil

        if self.root.exists():
            shutil.rmtree(self.root)

    def test_gate_timeout_produces_fail_timeout(self):
        """Gate subprocess.TimeoutExpired → verdict FAIL_TIMEOUT."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={
                "session_timeout": 0,
                "session_ok": 0,
            },
            gate_results={
                "session_timeout": "TIMEOUT",
                "session_ok": {"overall": "PASS", "gates": {}},
            },
        )

        with mock.patch("subprocess.run", side_effect=mock_run):
            result = rsv.run_gates(self.root / "session_timeout")

        self.assertEqual(result["verdict"], "FAIL_TIMEOUT")
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["total"], 0)

    def test_gate_timeout_overall_is_fail(self):
        """FAIL_TIMEOUT gate verdict → overall FAIL."""
        overall = rsv.compute_overall(
            {"verdict": "PASS"},
            {"verdict": "FAIL_TIMEOUT", "passed": 0, "total": 0},
            {"verdict": "n/a"},
        )
        self.assertEqual(overall, "FAIL")

    def test_timeout_counted_in_sweep_json(self):
        """sessions_timeout field counts gate timeouts."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={
                "session_timeout": 0,
                "session_ok": 0,
            },
            gate_results={
                "session_timeout": "TIMEOUT",
                "session_ok": {"overall": "PASS", "gates": {}},
            },
        )

        output_path = self.root / "report.json"

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with (
            redirect_stdout(f),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--continue-on-error",
                    "--output",
                    str(output_path),
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            with contextlib.suppress(SystemExit):
                rsv.main()

        data = json.loads(output_path.read_text())
        self.assertEqual(data["sessions_timeout"], 1)


# ---------------------------------------------------------------------------
# Test: duration_s tracking
# ---------------------------------------------------------------------------


class TestDurationTracking(unittest.TestCase):
    def test_duration_s_is_recorded(self):
        """Each session result should have a duration_s field."""
        mock_run = make_subprocess_mock_for_timeout(
            pipeline_results={"session_001": 0},
            gate_results={"session_001": {"overall": "PASS", "gates": {}}},
        )

        root = pathlib.Path("/tmp/test_rsv_hardening_duration")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        root.mkdir(parents=True)
        make_fake_session(root, "session_001")

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with (
            redirect_stdout(f),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(root),
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            with contextlib.suppress(SystemExit):
                rsv.main()

        # Verify duration_s appears in the text report
        f.getvalue()  # noqa: F841
        # The text report doesn't show duration, but the JSON output does
        # Let's check via --output
        output_path = root / "report.json"
        f2 = io.StringIO()
        with (
            redirect_stdout(f2),
            mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(root),
                    "--output",
                    str(output_path),
                ],
            ),
            mock.patch("subprocess.run", side_effect=mock_run),
        ):
            with contextlib.suppress(SystemExit):
                rsv.main()

        data = json.loads(output_path.read_text())
        self.assertEqual(len(data["per_session"]), 1)
        self.assertIsInstance(data["per_session"][0]["duration_s"], float)
        self.assertGreaterEqual(data["per_session"][0]["duration_s"], 0.0)

        import shutil

        shutil.rmtree(root)


# ---------------------------------------------------------------------------
# Test: render_sweep_json function
# ---------------------------------------------------------------------------


class TestRenderSweepJson(unittest.TestCase):
    def test_sweep_json_schema(self):
        """render_sweep_json produces correct schema."""
        results = [
            {
                "name": "session_001",
                "pipeline": {"verdict": "PASS"},
                "gates": {"verdict": "PASS", "passed": 9, "total": 9},
                "provenance": {"verdict": "VERIFIED"},
                "overall": "PASS",
                "duration_s": 12.3,
            },
            {
                "name": "session_002",
                "pipeline": {"verdict": "PASS"},
                "gates": {"verdict": "FAIL_TIMEOUT", "passed": 0, "total": 0},
                "provenance": {"verdict": "n/a"},
                "overall": "FAIL",
                "duration_s": 60.1,
            },
            {
                "name": "session_003",
                "pipeline": {"verdict": "BLOCKED"},
                "gates": {"verdict": "n/a", "passed": 0, "total": 0},
                "provenance": {"verdict": "n/a"},
                "overall": "FAIL",
                "duration_s": 0.5,
            },
        ]

        report = rsv.render_sweep_json(
            results,
            sweep_started="2025-01-01T00:00:00Z",
            sweep_finished="2025-01-01T00:05:00Z",
            sessions_total=3,
        )

        self.assertEqual(report["sweep_started"], "2025-01-01T00:00:00Z")
        self.assertEqual(report["sweep_finished"], "2025-01-01T00:05:00Z")
        self.assertEqual(report["sessions_total"], 3)
        self.assertEqual(report["sessions_buyer_ready"], 1)
        self.assertEqual(report["sessions_strict_violations"], 2)
        self.assertEqual(report["sessions_timeout"], 1)

        self.assertEqual(len(report["per_session"]), 3)
        self.assertEqual(report["per_session"][0]["session_id"], "session_001")
        self.assertEqual(report["per_session"][0]["verdict"], "BUYER_READY")
        self.assertEqual(report["per_session"][0]["duration_s"], 12.3)

    def test_verdict_to_buyer_label(self):
        """PASS → BUYER_READY, everything else stays as-is."""
        self.assertEqual(rsv.verdict_to_buyer_label("PASS"), "BUYER_READY")
        self.assertEqual(rsv.verdict_to_buyer_label("FAIL"), "FAIL")
        self.assertEqual(rsv.verdict_to_buyer_label("DEGRADED"), "DEGRADED")


# ---------------------------------------------------------------------------
# Test: GATE_TIMEOUT constant is 60
# ---------------------------------------------------------------------------


class TestGateTimeoutConstant(unittest.TestCase):
    def test_gate_timeout_is_60(self):
        """GATE_TIMEOUT should be 60 seconds per spec."""
        self.assertEqual(rsv.GATE_TIMEOUT, 60)


if __name__ == "__main__":
    unittest.main()
