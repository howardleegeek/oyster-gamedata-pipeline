#!/usr/bin/env python3
"""
Tests for bin/real_session_validator.py

Covers:
- mock subprocess.run for each of the 4 gate scripts
- case: 3 sessions, all PASS → verdict PASS @ 100%
- case: 3 sessions, 1 pipeline-blocked → verdict FAIL on that session
- case: 3 sessions, 1 has G8 FAIL → DEGRADED tier
- case: empty dir → no sessions → exit 0 with "no sessions found"
"""

import json
import pathlib
import sys
import unittest
from unittest import mock

# Ensure bin/ is on path
BIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))

import real_session_validator as rsv

# ---------------------------------------------------------------------------
# Helpers to create fake session dirs
# ---------------------------------------------------------------------------


def make_fake_session(root: pathlib.Path, name: str, files: list[str] | None = None):
    """Create a fake session directory with required files."""
    session_dir = root / name
    session_dir.mkdir(parents=True, exist_ok=True)
    required = files or ["recording.mp4", "game_state.jsonl"]
    for f in required:
        (session_dir / f).write_text("fake")
    (session_dir / ".session_complete").write_text("{}")
    return session_dir


def make_fake_lem_session(root: pathlib.Path, name: str):
    """Create a fake recorder LEM session with nested files."""
    session_dir = root / name
    (session_dir / "recordings").mkdir(parents=True, exist_ok=True)
    (session_dir / "streams").mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata").mkdir(parents=True, exist_ok=True)
    (session_dir / "recordings" / "main_record.mp4").write_bytes(b"fake-mp4")
    (session_dir / "streams" / "states.jsonl").write_text('{"tick":1}\n')
    (session_dir / "streams" / "actions.jsonl").write_text('{"event_type":"ACTION"}\n')
    (session_dir / "metadata" / "session.json").write_text('{"session_id":"lem"}\n')
    (session_dir / ".session_complete").write_text("{}")
    return session_dir


def make_subprocess_mock(
    pipeline_results: dict, gate_results: dict, sign_results: dict, verify_results: dict
):
    """
    Create a subprocess.run mock that returns different results based on
    which script is being called.

    pipeline_results: {session_name: exit_code}
    gate_results: {session_name: {"overall": ..., "gates": {...}}}
    sign_results: {session_name: exit_code}
    verify_results: {session_name: exit_code}
    """

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd)
        # Determine which script is being called
        if "canonical_pipeline.py" in cmd_str:
            # Extract session name from command
            session_name = None
            for part in cmd:
                if "session_" in part:
                    session_name = pathlib.Path(part).name
                    break
            exit_code = pipeline_results.get(session_name, 0)
            return mock.MagicMock(returncode=exit_code, stdout="", stderr="")

        elif "end_to_end_gate_smoke.py" in cmd_str:
            session_name = None
            for part in cmd:
                if "session_" in part:
                    session_name = pathlib.Path(part).name
                    break
            gate_data = gate_results.get(session_name, {"overall": "PASS", "gates": {}})
            return mock.MagicMock(
                returncode=0,
                stdout=json.dumps(gate_data),
                stderr="",
            )

        elif "provenance_sign.py" in cmd_str:
            session_name = None
            for part in cmd:
                if "MANIFEST" in part:
                    session_name = pathlib.Path(part).parent.name
                    break
            exit_code = sign_results.get(session_name, 0)
            if exit_code == 0:
                # Create the signed file so verify can find it
                for part in cmd:
                    if "MANIFEST" in part:
                        signed_path = pathlib.Path(part).parent / "MANIFEST.signed.json"
                        signed_path.write_text('{"signed": true}')
                        break
            return mock.MagicMock(returncode=exit_code, stdout="", stderr="")

        elif "provenance_verify.py" in cmd_str:
            session_name = None
            for part in cmd:
                if "MANIFEST" in part:
                    session_name = pathlib.Path(part).parent.name
                    break
            exit_code = verify_results.get(session_name, 0)
            return mock.MagicMock(returncode=exit_code, stdout="", stderr="")

        return mock.MagicMock(returncode=0, stdout="", stderr="")

    return mock_run


# ---------------------------------------------------------------------------
# Test: discover_sessions
# ---------------------------------------------------------------------------


class TestDiscoverSessions(unittest.TestCase):
    def test_empty_dir(self):
        with self.subTest("nonexistent dir"):
            result = rsv.discover_sessions(pathlib.Path("/tmp/nonexistent_rsv_test_xyz"))
            self.assertEqual(result, [])

    def test_dirs_without_required_files(self):
        root = pathlib.Path("/tmp/rsv_test_empty")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        root.mkdir(parents=True)
        # Create a dir without required files
        (root / "bad_session").mkdir()
        (root / "bad_session" / "notes.txt").write_text("hello")

        result = rsv.discover_sessions(root)
        self.assertEqual(result, [])

        import shutil

        shutil.rmtree(root)

    def test_dirs_with_required_files(self):
        root = pathlib.Path("/tmp/rsv_test_valid")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        root.mkdir(parents=True)

        make_fake_session(root, "session_001")
        make_fake_session(root, "session_002")

        result = rsv.discover_sessions(root)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].name, "session_001")
        self.assertEqual(result[1].name, "session_002")

        import shutil

        shutil.rmtree(root)

    def test_dirs_without_complete_marker_are_refused(self):
        root = pathlib.Path("/tmp/rsv_test_no_complete_marker")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        root.mkdir(parents=True)

        session_dir = make_fake_session(root, "session_001")
        (session_dir / ".session_complete").unlink()

        result = rsv.discover_sessions(root)
        self.assertEqual(result, [])

        import shutil

        shutil.rmtree(root)

    def test_discovers_lem_sessions(self):
        root = pathlib.Path("/tmp/rsv_test_lem_valid")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        root.mkdir(parents=True)

        make_fake_lem_session(root, "lem_session_001")
        (root / "incomplete_lem").mkdir()
        (root / "incomplete_lem" / "recordings").mkdir()
        (root / "incomplete_lem" / "recordings" / "main_record.mp4").write_bytes(b"fake")

        result = rsv.discover_sessions(root)
        self.assertEqual([p.name for p in result], ["lem_session_001"])

        import shutil

        shutil.rmtree(root)

    def test_lem_validation_view_maps_to_legacy_files(self):
        root = pathlib.Path("/tmp/rsv_test_lem_view")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        root.mkdir(parents=True)
        session_dir = make_fake_lem_session(root, "lem_session_001")

        with rsv.session_validation_view(session_dir) as view_dir:
            self.assertTrue((view_dir / "recording.mp4").exists())
            self.assertTrue((view_dir / "game_state.jsonl").exists())
            self.assertTrue((view_dir / "inputs.jsonl").exists())
            self.assertTrue((view_dir / ".session_complete").exists())
            self.assertEqual((view_dir / "game_state.jsonl").read_text(), '{"tick":1}\n')
            self.assertFalse((session_dir / "game_state.jsonl").exists())

        import shutil

        shutil.rmtree(root)


# ---------------------------------------------------------------------------
# Test: 3 sessions, all PASS → verdict PASS @ 100%
# ---------------------------------------------------------------------------


class TestAllPass(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/rsv_test_all_pass")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

        for i in range(1, 4):
            session_dir = make_fake_session(self.root, f"session_00{i}")
            # Create MANIFEST.json for provenance
            (session_dir / "MANIFEST.json").write_text('{"version": 1}')

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root)

    @mock.patch("subprocess.run")
    def test_all_pass(self, mock_run):
        mock_run.side_effect = make_subprocess_mock(
            pipeline_results={"session_001": 0, "session_002": 0, "session_003": 0},
            gate_results={
                "session_001": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
                "session_002": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
                "session_003": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
            },
            sign_results={"session_001": 0, "session_002": 0, "session_003": 0},
            verify_results={"session_001": 0, "session_002": 0, "session_003": 0},
        )

        # Capture stdout
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f), mock.patch.object(
            sys,
            "argv",
            [
                "real_session_validator.py",
                "--sessions-root",
                str(self.root),
                "--keyfile",
                "/tmp/fake.key",
            ],
        ):
            try:
                rsv.main()
            except SystemExit as e:
                self.assertEqual(e.code, 0)

        output = f.getvalue()
        self.assertIn("Full PASS:          3 (100%)", output)
        self.assertIn("BUYER-READY", output)


# ---------------------------------------------------------------------------
# Test: 3 sessions, 1 pipeline-blocked → FAIL on that session
# ---------------------------------------------------------------------------


class TestPipelineBlocked(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/rsv_test_blocked")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

        for i in range(1, 4):
            session_dir = make_fake_session(self.root, f"session_00{i}")
            (session_dir / "MANIFEST.json").write_text('{"version": 1}')

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root)

    @mock.patch("subprocess.run")
    def test_one_blocked(self, mock_run):
        mock_run.side_effect = make_subprocess_mock(
            pipeline_results={"session_001": 0, "session_002": 1, "session_003": 0},
            gate_results={
                "session_001": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
                "session_003": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
            },
            sign_results={"session_001": 0, "session_003": 0},
            verify_results={"session_001": 0, "session_003": 0},
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--keyfile",
                    "/tmp/fake.key",
                ],
            ):
                try:
                    rsv.main()
                except SystemExit as e:
                    self.assertEqual(e.code, 1)  # exit 1 because there's a FAIL

        output = f.getvalue()
        self.assertIn("FAIL:               1 (33%)", output)
        self.assertIn("Pipeline BLOCKED", output)
        self.assertIn("NOT BUYER-READY", output)


# ---------------------------------------------------------------------------
# Test: 3 sessions, 1 has G8 FAIL → DEGRADED tier
# ---------------------------------------------------------------------------


class TestGateFailDegraded(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/rsv_test_degraded")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

        for i in range(1, 4):
            session_dir = make_fake_session(self.root, f"session_00{i}")
            (session_dir / "MANIFEST.json").write_text('{"version": 1}')

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root)

    @mock.patch("subprocess.run")
    def test_one_gate_fail_degraded(self, mock_run):
        # session_002 has G8 FAIL → overall DEGRADED
        gates_002 = {f"G{i}": {"status": "PASS"} for i in range(1, 10)}
        gates_002["G8"] = {"status": "FAIL", "reason": "video_quality below threshold"}

        mock_run.side_effect = make_subprocess_mock(
            pipeline_results={"session_001": 0, "session_002": 0, "session_003": 0},
            gate_results={
                "session_001": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
                "session_002": {"overall": "DEGRADED", "gates": gates_002},
                "session_003": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
            },
            sign_results={"session_001": 0, "session_003": 0},
            verify_results={"session_001": 0, "session_003": 0},
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--keyfile",
                    "/tmp/fake.key",
                ],
            ):
                try:
                    rsv.main()
                except SystemExit as e:
                    # DEGRADED is not FAIL, so exit 0
                    self.assertEqual(e.code, 0)

        output = f.getvalue()
        self.assertIn("DEGRADED:           1 (33%)", output)
        self.assertIn("Full PASS:          2 (66%)", output)
        self.assertIn("G8 failed", output)


# ---------------------------------------------------------------------------
# Test: empty dir → no sessions → exit 0
# ---------------------------------------------------------------------------


class TestEmptyDir(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/rsv_test_empty_dir")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root)

    def test_empty_dir_text(self):
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                ],
            ):
                try:
                    rsv.main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

        output = f.getvalue()
        self.assertIn("Found: 0 session dirs", output)
        self.assertIn("No sessions found", output)

    def test_empty_dir_json(self):
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--json",
                ],
            ):
                try:
                    rsv.main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

        output = f.getvalue()
        data = json.loads(output)
        self.assertEqual(data["total_found"], 0)
        self.assertEqual(data["evaluated"], 0)


# ---------------------------------------------------------------------------
# Test: no --keyfile → provenance SKIPPED
# ---------------------------------------------------------------------------


class TestNoKeyfile(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/rsv_test_no_keyfile")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        make_fake_session(self.root, "session_001")
        (self.root / "session_001" / "MANIFEST.json").write_text('{"version": 1}')

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root)

    @mock.patch("subprocess.run")
    def test_provenance_skipped_without_keyfile(self, mock_run):
        mock_run.side_effect = make_subprocess_mock(
            pipeline_results={"session_001": 0},
            gate_results={
                "session_001": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
            },
            sign_results={},
            verify_results={},
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    # NO --keyfile
                ],
            ):
                try:
                    rsv.main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

        output = f.getvalue()
        self.assertIn("SKIPPED (no --keyfile)", output)
        # Without provenance, it should be DEGRADED
        self.assertIn("DEGRADED", output)

        # Verify sign/verify were NOT called
        calls = [str(call) for call in mock_run.call_args_list]
        for call in calls:
            self.assertNotIn("provenance_sign", call)
            self.assertNotIn("provenance_verify", call)


# ---------------------------------------------------------------------------
# Test: JSON output format
# ---------------------------------------------------------------------------


class TestJsonOutput(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/rsv_test_json")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        make_fake_session(self.root, "session_001")
        (self.root / "session_001" / "MANIFEST.json").write_text('{"version": 1}')

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root)

    @mock.patch("subprocess.run")
    def test_json_output_structure(self, mock_run):
        mock_run.side_effect = make_subprocess_mock(
            pipeline_results={"session_001": 0},
            gate_results={
                "session_001": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
            },
            sign_results={"session_001": 0},
            verify_results={"session_001": 0},
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--keyfile",
                    "/tmp/fake.key",
                    "--json",
                ],
            ):
                try:
                    rsv.main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

        output = f.getvalue()
        data = json.loads(output)
        self.assertIn("timestamp", data)
        self.assertIn("sessions_root", data)
        self.assertIn("summary", data)
        self.assertIn("sessions", data)
        self.assertEqual(len(data["sessions"]), 1)
        self.assertEqual(data["sessions"][0]["overall"], "PASS")


# ---------------------------------------------------------------------------
# Test: CSV output
# ---------------------------------------------------------------------------


class TestCsvOutput(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/rsv_test_csv")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        make_fake_session(self.root, "session_001")
        (self.root / "session_001" / "MANIFEST.json").write_text('{"version": 1}')
        self.csv_path = pathlib.Path("/tmp/rsv_test_csv_output.csv")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root)
        if self.csv_path.exists():
            self.csv_path.unlink()

    @mock.patch("subprocess.run")
    def test_csv_output(self, mock_run):
        mock_run.side_effect = make_subprocess_mock(
            pipeline_results={"session_001": 0},
            gate_results={
                "session_001": {
                    "overall": "PASS",
                    "gates": {f"G{i}": {"status": "PASS"} for i in range(1, 10)},
                },
            },
            sign_results={"session_001": 0},
            verify_results={"session_001": 0},
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--keyfile",
                    "/tmp/fake.key",
                    "--csv",
                    str(self.csv_path),
                ],
            ):
                try:
                    rsv.main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

        self.assertTrue(self.csv_path.exists())
        content = self.csv_path.read_text()
        self.assertIn("session_001", content)
        self.assertIn("PASS", content)


# ---------------------------------------------------------------------------
# Test: compute_overall logic
# ---------------------------------------------------------------------------


class TestComputeOverall(unittest.TestCase):
    def test_pipeline_blocked_is_fail(self):
        result = rsv.compute_overall(
            {"verdict": "BLOCKED"},
            {"verdict": "n/a", "passed": 0, "total": 0},
            {"verdict": "n/a"},
        )
        self.assertEqual(result, "FAIL")

    def test_gates_fail_is_fail(self):
        result = rsv.compute_overall(
            {"verdict": "PASS"},
            {"verdict": "FAIL", "passed": 7, "total": 9},
            {"verdict": "n/a"},
        )
        self.assertEqual(result, "FAIL")

    def test_gates_degraded_is_degraded(self):
        result = rsv.compute_overall(
            {"verdict": "PASS"},
            {"verdict": "DEGRADED", "passed": 8, "total": 9},
            {"verdict": "VERIFIED"},
        )
        self.assertEqual(result, "DEGRADED")

    def test_all_pass_is_pass(self):
        result = rsv.compute_overall(
            {"verdict": "PASS"},
            {"verdict": "PASS", "passed": 9, "total": 9},
            {"verdict": "VERIFIED"},
        )
        self.assertEqual(result, "PASS")

    def test_provenance_failed_is_fail(self):
        result = rsv.compute_overall(
            {"verdict": "PASS"},
            {"verdict": "PASS", "passed": 9, "total": 9},
            {"verdict": "FAILED"},
        )
        self.assertEqual(result, "FAIL")

    def test_provenance_skipped_is_degraded(self):
        result = rsv.compute_overall(
            {"verdict": "PASS"},
            {"verdict": "PASS", "passed": 9, "total": 9},
            {"verdict": "SKIPPED"},
        )
        self.assertEqual(result, "DEGRADED")


# ---------------------------------------------------------------------------
# Test: --limit flag
# ---------------------------------------------------------------------------


class TestLimit(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path("/tmp/rsv_test_limit")
        if self.root.exists():
            import shutil

            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        for i in range(1, 6):
            make_fake_session(self.root, f"session_00{i}")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root)

    @mock.patch("subprocess.run")
    def test_limit_reduces_sessions(self, mock_run):
        # Make pipeline BLOCKED so only pipeline step runs (1 call per session)
        mock_run.side_effect = make_subprocess_mock(
            pipeline_results={
                "session_001": 1,
                "session_002": 1,
                "session_003": 1,
                "session_004": 1,
                "session_005": 1,
            },
            gate_results={},
            sign_results={},
            verify_results={},
        )

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "real_session_validator.py",
                    "--sessions-root",
                    str(self.root),
                    "--limit",
                    "2",
                ],
            ):
                try:
                    rsv.main()
                except SystemExit:
                    pass

        output = f.getvalue()
        self.assertIn("Found: 5 session dirs", output)
        self.assertIn("Validating: first 2", output)
        # Only 2 sessions should have been processed (1 pipeline call each)
        self.assertEqual(mock_run.call_count, 2)


# ---------------------------------------------------------------------------
# Test: failure_reasons collection
# ---------------------------------------------------------------------------


class TestFailureReasons(unittest.TestCase):
    def test_pipeline_blocked_reason(self):
        reasons = rsv.collect_failure_reasons(
            "session_001",
            {"verdict": "BLOCKED"},
            {"verdict": "n/a", "per_gate": {}},
            {"verdict": "n/a"},
        )
        self.assertIn("Pipeline BLOCKED", reasons)

    def test_gate_failure_reason(self):
        reasons = rsv.collect_failure_reasons(
            "session_001",
            {"verdict": "PASS"},
            {
                "verdict": "FAIL",
                "per_gate": {
                    "G7": {"status": "PASS"},
                    "G8": {"status": "FAIL"},
                },
            },
            {"verdict": "n/a"},
        )
        self.assertIn("G8 failed", reasons)

    def test_provenance_failure_reason(self):
        reasons = rsv.collect_failure_reasons(
            "session_001",
            {"verdict": "PASS"},
            {"verdict": "PASS", "per_gate": {}},
            {"verdict": "FAILED"},
        )
        self.assertIn("Provenance FAILED", reasons)


if __name__ == "__main__":
    unittest.main()
