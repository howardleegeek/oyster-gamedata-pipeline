#!/usr/bin/env python3
"""
Tests for daemon/rsv_feeder.py

Covers:
- session discovery (valid + invalid dirs)
- state file load/save/round-trip
- hash computation and change detection
- new-session filtering (idempotency)
- RSV invocation (mocked subprocess)
- verdict extraction from various RSV output shapes
- dashboard accumulation
- --once --dry-run mode
- daemon loop (mocked)
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

# Ensure repo root is on path
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import daemon.rsv_feeder as rsv_feeder  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_fake_session(root: pathlib.Path, name: str, files: list[str] | None = None):
    """Create a fake session directory with required files."""
    session_dir = root / name
    session_dir.mkdir(parents=True, exist_ok=True)
    required = files or ["recording.mp4", "game_state.jsonl"]
    for f in required:
        (session_dir / f).write_text(f"fake content for {name}/{f}")
    return session_dir


def make_fake_finalized_dir(tmp_path: pathlib.Path, session_names: list[str]) -> pathlib.Path:
    """Create a fake finalized directory with multiple sessions."""
    finalized = tmp_path / "finalized"
    finalized.mkdir(parents=True, exist_ok=True)
    for name in session_names:
        make_fake_session(finalized, name)
    return finalized


# ---------------------------------------------------------------------------
# Tests: Session discovery
# ---------------------------------------------------------------------------


class TestDiscoverSessions(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            sessions = rsv_feeder.discover_sessions(root)
            self.assertEqual(sessions, [])

    def test_nonexistent_dir(self):
        sessions = rsv_feeder.discover_sessions(pathlib.Path("/nonexistent/path"))
        self.assertEqual(sessions, [])

    def test_valid_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            make_fake_session(root, "session_001")
            make_fake_session(root, "session_002")
            sessions = rsv_feeder.discover_sessions(root)
            self.assertEqual(len(sessions), 2)
            self.assertEqual(sessions[0].name, "session_001")
            self.assertEqual(sessions[1].name, "session_002")

    def test_invalid_session_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            # Missing game_state.jsonl
            session_dir = root / "session_bad"
            session_dir.mkdir()
            (session_dir / "recording.mp4").write_text("fake")
            sessions = rsv_feeder.discover_sessions(root)
            self.assertEqual(sessions, [])

    def test_mixed_valid_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            make_fake_session(root, "session_good")
            bad_dir = root / "session_bad"
            bad_dir.mkdir()
            (bad_dir / "notes.txt").write_text("no required files")
            sessions = rsv_feeder.discover_sessions(root)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].name, "session_good")


# ---------------------------------------------------------------------------
# Tests: State management
# ---------------------------------------------------------------------------


class TestStateManagement(unittest.TestCase):
    def test_load_state_nonexistent(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / "state.json"
            state = rsv_feeder.load_state(state_path)
            self.assertEqual(state, {})

    def test_load_state_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / "state.json"
            state_path.write_text("not valid json {{{")
            state = rsv_feeder.load_state(state_path)
            self.assertEqual(state, {})

    def test_save_and_load_state(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / "state.json"
            original = {
                "session_001": {
                    "sha256": "abc123",
                    "verdict": "BUYER_READY",
                    "processed_at": "2026-05-19T12:00:00Z",
                }
            }
            rsv_feeder.save_state(state_path, original)
            loaded = rsv_feeder.load_state(state_path)
            self.assertEqual(loaded, original)

    def test_state_file_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / "nested" / "deep" / "state.json"
            rsv_feeder.save_state(state_path, {})
            self.assertTrue(state_path.exists())


# ---------------------------------------------------------------------------
# Tests: Hash computation
# ---------------------------------------------------------------------------


class TestHashComputation(unittest.TestCase):
    def test_same_content_same_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            s1 = make_fake_session(root, "session_a")
            s2 = make_fake_session(root, "session_b")
            # Same files, same content → same hash
            h1 = rsv_feeder.compute_session_hash(s1)
            h2 = rsv_feeder.compute_session_hash(s2)
            self.assertEqual(h1, h2)

    def test_different_content_different_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            s1 = make_fake_session(root, "session_a")
            s2 = make_fake_session(root, "session_b")
            # Modify one file
            (s2 / "recording.mp4").write_text("different content")
            h1 = rsv_feeder.compute_session_hash(s1)
            h2 = rsv_feeder.compute_session_hash(s2)
            self.assertNotEqual(h1, h2)

    def test_hash_is_valid_sha256(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            s = make_fake_session(root, "session_x")
            h = rsv_feeder.compute_session_hash(s)
            self.assertEqual(len(h), 64)  # sha256 hex length
            # Verify it's valid hex
            int(h, 16)


# ---------------------------------------------------------------------------
# Tests: New session filtering
# ---------------------------------------------------------------------------


class TestFilterNewSessions(unittest.TestCase):
    def test_all_new(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            _s1 = make_fake_session(root, "s1")
            make_fake_session(root, "s2")
            sessions = rsv_feeder.discover_sessions(root)
            new = rsv_feeder.filter_new_sessions(sessions, {})
            self.assertEqual(len(new), 2)

    def test_all_processed(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            s1 = make_fake_session(root, "s1")
            s2 = make_fake_session(root, "s2")
            h1 = rsv_feeder.compute_session_hash(s1)
            h2 = rsv_feeder.compute_session_hash(s2)
            state = {
                "s1": {"sha256": h1, "verdict": "BUYER_READY", "processed_at": "now"},
                "s2": {"sha256": h2, "verdict": "NOT_READY", "processed_at": "now"},
            }
            sessions = rsv_feeder.discover_sessions(root)
            new = rsv_feeder.filter_new_sessions(sessions, state)
            self.assertEqual(new, [])

    def test_partial_processed(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            s1 = make_fake_session(root, "s1")
            _s2 = make_fake_session(root, "s2")
            h1 = rsv_feeder.compute_session_hash(s1)
            state = {
                "s1": {"sha256": h1, "verdict": "BUYER_READY", "processed_at": "now"},
            }
            sessions = rsv_feeder.discover_sessions(root)
            new = rsv_feeder.filter_new_sessions(sessions, state)
            self.assertEqual(len(new), 1)
            self.assertEqual(new[0].name, "s2")

    def test_content_changed_reprocess(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            _s1 = make_fake_session(root, "s1")
            # State has old hash
            state = {
                "s1": {
                    "sha256": "old_hash_value",
                    "verdict": "BUYER_READY",
                    "processed_at": "now",
                }
            }
            sessions = rsv_feeder.discover_sessions(root)
            new = rsv_feeder.filter_new_sessions(sessions, state)
            # Should re-process because hash changed
            self.assertEqual(len(new), 1)
            self.assertEqual(new[0].name, "s1")


# ---------------------------------------------------------------------------
# Tests: Verdict extraction
# ---------------------------------------------------------------------------


class TestExtractVerdict(unittest.TestCase):
    def test_error_verdict(self):
        self.assertEqual(rsv_feeder.extract_verdict({"error": "TIMEOUT"}), "NOT_READY")

    def test_summary_all_pass(self):
        output = {
            "summary": {"PASS": 3, "total": 3},
            "sessions": [],
        }
        self.assertEqual(rsv_feeder.extract_verdict(output), "BUYER_READY")

    def test_summary_partial_pass(self):
        output = {
            "summary": {"PASS": 2, "total": 3},
            "sessions": [],
        }
        self.assertEqual(rsv_feeder.extract_verdict(output), "NOT_READY")

    def test_sessions_list_pass(self):
        output = {
            "sessions": [{"overall": "PASS"}],
        }
        self.assertEqual(rsv_feeder.extract_verdict(output), "BUYER_READY")

    def test_sessions_list_fail(self):
        output = {
            "sessions": [{"overall": "FAIL"}],
        }
        self.assertEqual(rsv_feeder.extract_verdict(output), "NOT_READY")

    def test_sessions_list_degraded(self):
        output = {
            "sessions": [{"overall": "DEGRADED"}],
        }
        self.assertEqual(rsv_feeder.extract_verdict(output), "NOT_READY")

    def test_top_level_verdict_pass(self):
        output = {"verdict": "PASS"}
        self.assertEqual(rsv_feeder.extract_verdict(output), "BUYER_READY")

    def test_top_level_verdict_fail(self):
        output = {"verdict": "FAIL"}
        self.assertEqual(rsv_feeder.extract_verdict(output), "NOT_READY")

    def test_empty_output(self):
        self.assertEqual(rsv_feeder.extract_verdict({}), "NOT_READY")


# ---------------------------------------------------------------------------
# Tests: Dashboard
# ---------------------------------------------------------------------------


class TestDashboard(unittest.TestCase):
    def test_load_dashboard_nonexistent(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "dashboard.json"
            d = rsv_feeder.load_dashboard(path)
            self.assertEqual(d["total"], 0)
            self.assertEqual(d["buyer_ready"], 0)
            self.assertEqual(d["pct"], 0.0)

    def test_load_dashboard_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "dashboard.json"
            path.write_text("corrupt{{{")
            d = rsv_feeder.load_dashboard(path)
            self.assertEqual(d["total"], 0)

    def test_update_dashboard_new(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "dashboard.json"
            d = rsv_feeder.update_dashboard(path, ["BUYER_READY", "NOT_READY"])
            self.assertEqual(d["total"], 2)
            self.assertEqual(d["buyer_ready"], 1)
            self.assertAlmostEqual(d["pct"], 0.5, places=4)
            self.assertTrue(d["updated_at"])

    def test_update_dashboard_accumulates(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "dashboard.json"
            # First batch
            rsv_feeder.update_dashboard(path, ["BUYER_READY"])
            # Second batch
            d = rsv_feeder.update_dashboard(path, ["BUYER_READY", "NOT_READY"])
            self.assertEqual(d["total"], 3)
            self.assertEqual(d["buyer_ready"], 2)
            self.assertAlmostEqual(d["pct"], 2 / 3, places=4)

    def test_update_dashboard_all_ready(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "dashboard.json"
            d = rsv_feeder.update_dashboard(path, ["BUYER_READY", "BUYER_READY", "BUYER_READY"])
            self.assertEqual(d["total"], 3)
            self.assertEqual(d["buyer_ready"], 3)
            self.assertAlmostEqual(d["pct"], 1.0, places=4)

    def test_update_dashboard_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "nested" / "dir" / "dashboard.json"
            rsv_feeder.update_dashboard(path, ["BUYER_READY"])
            self.assertTrue(path.exists())


# ---------------------------------------------------------------------------
# Tests: RSV invocation (mocked)
# ---------------------------------------------------------------------------


class TestRSVInvocation(unittest.TestCase):
    def test_run_rsv_success(self):
        with tempfile.TemporaryDirectory() as td:
            session_dir = pathlib.Path(td) / "session_001"
            session_dir.mkdir()
            (session_dir / "recording.mp4").write_text("fake")
            (session_dir / "game_state.jsonl").write_text("fake")

            output_path = pathlib.Path(td) / "rsv_output.json"
            output_data = {
                "summary": {"PASS": 1, "total": 1},
                "sessions": [{"overall": "PASS"}],
            }
            output_path.write_text(json.dumps(output_data))

            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")
                result = rsv_feeder.run_rsv(session_dir, output_path)
                self.assertEqual(result["summary"]["PASS"], 1)

    def test_run_rsv_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            session_dir = pathlib.Path(td) / "session_001"
            session_dir.mkdir()
            output_path = pathlib.Path(td) / "rsv_output.json"

            with mock.patch("subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired(cmd=[], timeout=900)
                result = rsv_feeder.run_rsv(session_dir, output_path)
                self.assertEqual(result["error"], "TIMEOUT")

    def test_run_rsv_exception(self):
        with tempfile.TemporaryDirectory() as td:
            session_dir = pathlib.Path(td) / "session_001"
            session_dir.mkdir()
            output_path = pathlib.Path(td) / "rsv_output.json"

            with mock.patch("subprocess.run") as mock_run:
                mock_run.side_effect = OSError("command not found")
                result = rsv_feeder.run_rsv(session_dir, output_path)
                self.assertIn("command not found", result["error"])


# ---------------------------------------------------------------------------
# Tests: run_once
# ---------------------------------------------------------------------------


class TestRunOnce(unittest.TestCase):
    def test_no_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            finalized = pathlib.Path(td) / "finalized"
            finalized.mkdir()
            state_path = pathlib.Path(td) / "state.json"
            dashboard_path = pathlib.Path(td) / "dashboard.json"

            count = rsv_feeder.run_once(finalized, state_path, dashboard_path)
            self.assertEqual(count, 0)

    def test_dry_run_processes_all(self):
        with tempfile.TemporaryDirectory() as td:
            finalized = make_fake_finalized_dir(
                pathlib.Path(td), ["session_a", "session_b", "session_c"]
            )
            state_path = pathlib.Path(td) / "state.json"
            dashboard_path = pathlib.Path(td) / "dashboard.json"

            count = rsv_feeder.run_once(finalized, state_path, dashboard_path, dry_run=True)
            self.assertEqual(count, 3)

            # State should have all 3 sessions
            state = rsv_feeder.load_state(state_path)
            self.assertEqual(len(state), 3)
            self.assertIn("session_a", state)
            self.assertIn("session_b", state)
            self.assertIn("session_c", state)

            # Dashboard should reflect dry-run verdicts
            dashboard = rsv_feeder.load_dashboard(dashboard_path)
            self.assertEqual(dashboard["total"], 3)
            self.assertEqual(dashboard["buyer_ready"], 3)

    def test_idempotent_second_run(self):
        with tempfile.TemporaryDirectory() as td:
            finalized = make_fake_finalized_dir(pathlib.Path(td), ["session_a", "session_b"])
            state_path = pathlib.Path(td) / "state.json"
            dashboard_path = pathlib.Path(td) / "dashboard.json"

            # First run
            count1 = rsv_feeder.run_once(finalized, state_path, dashboard_path, dry_run=True)
            self.assertEqual(count1, 2)

            # Second run — should find no new sessions
            count2 = rsv_feeder.run_once(finalized, state_path, dashboard_path, dry_run=True)
            self.assertEqual(count2, 0)

            # Dashboard should not have doubled
            dashboard = rsv_feeder.load_dashboard(dashboard_path)
            self.assertEqual(dashboard["total"], 2)

    def test_run_once_with_mocked_rsv(self):
        """Test run_once with mocked RSV subprocess calls."""
        with tempfile.TemporaryDirectory() as td:
            finalized = make_fake_finalized_dir(pathlib.Path(td), ["session_pass", "session_fail"])
            state_path = pathlib.Path(td) / "state.json"
            dashboard_path = pathlib.Path(td) / "dashboard.json"

            # Track which sessions were processed
            processed_sessions: dict[str, str] = {}

            def mock_run(cmd, *args, **kwargs):
                # Extract session ID from the --output path: /tmp/rsv_<id>.json
                output_idx = cmd.index("--output")
                output_path = pathlib.Path(cmd[output_idx + 1])
                # Extract session id from filename: rsv_<id>.json
                session_id = output_path.stem.replace("rsv_", "")

                # Write appropriate output based on session id
                if "pass" in session_id:
                    data = {
                        "summary": {"PASS": 1, "total": 1},
                        "sessions": [{"overall": "PASS"}],
                    }
                else:
                    data = {
                        "summary": {"PASS": 0, "total": 1},
                        "sessions": [{"overall": "FAIL"}],
                    }
                output_path.write_text(json.dumps(data))
                processed_sessions[session_id] = data["sessions"][0]["overall"]

                return mock.MagicMock(returncode=0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=mock_run):
                count = rsv_feeder.run_once(finalized, state_path, dashboard_path, dry_run=False)
                self.assertEqual(count, 2)

            state = rsv_feeder.load_state(state_path)
            self.assertEqual(len(state), 2)
            self.assertEqual(state["session_pass"]["verdict"], "BUYER_READY")
            self.assertEqual(state["session_fail"]["verdict"], "NOT_READY")

            dashboard = rsv_feeder.load_dashboard(dashboard_path)
            self.assertEqual(dashboard["total"], 2)
            self.assertEqual(dashboard["buyer_ready"], 1)
            self.assertAlmostEqual(dashboard["pct"], 0.5, places=4)


# ---------------------------------------------------------------------------
# Tests: CLI
# ---------------------------------------------------------------------------


class TestCLI(unittest.TestCase):
    def test_parse_args_once(self):
        args = rsv_feeder.parse_args(["--once"])
        self.assertTrue(args.once)
        self.assertFalse(args.dry_run)

    def test_parse_args_dry_run(self):
        args = rsv_feeder.parse_args(["--once", "--dry-run"])
        self.assertTrue(args.once)
        self.assertTrue(args.dry_run)

    def test_parse_args_defaults(self):
        args = rsv_feeder.parse_args([])
        self.assertFalse(args.once)
        self.assertFalse(args.dry_run)
        self.assertIsNone(args.finalized_dir)

    def test_parse_args_overrides(self):
        args = rsv_feeder.parse_args(
            [
                "--once",
                "--dry-run",
                "--finalized-dir",
                "/tmp/test_finalized",
                "--state-file",
                "/tmp/test_state.json",
                "--dashboard-file",
                "/tmp/test_dashboard.json",
            ]
        )
        self.assertTrue(args.once)
        self.assertTrue(args.dry_run)
        self.assertEqual(args.finalized_dir, "/tmp/test_finalized")
        self.assertEqual(args.state_file, "/tmp/test_state.json")
        self.assertEqual(args.dashboard_file, "/tmp/test_dashboard.json")


# ---------------------------------------------------------------------------
# Tests: main() integration
# ---------------------------------------------------------------------------


class TestMainIntegration(unittest.TestCase):
    def test_main_once_dry_run_no_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            finalized = pathlib.Path(td) / "finalized"
            finalized.mkdir()
            state_path = pathlib.Path(td) / "state.json"
            dashboard_path = pathlib.Path(td) / "dashboard.json"

            exit_code = rsv_feeder.main(
                [
                    "--once",
                    "--dry-run",
                    "--finalized-dir",
                    str(finalized),
                    "--state-file",
                    str(state_path),
                    "--dashboard-file",
                    str(dashboard_path),
                ]
            )
            self.assertEqual(exit_code, 0)

    def test_main_once_dry_run_with_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            finalized = make_fake_finalized_dir(pathlib.Path(td), ["session_001", "session_002"])
            state_path = pathlib.Path(td) / "state.json"
            dashboard_path = pathlib.Path(td) / "dashboard.json"

            exit_code = rsv_feeder.main(
                [
                    "--once",
                    "--dry-run",
                    "--finalized-dir",
                    str(finalized),
                    "--state-file",
                    str(state_path),
                    "--dashboard-file",
                    str(dashboard_path),
                ]
            )
            self.assertEqual(exit_code, 0)

            # Verify state
            state = rsv_feeder.load_state(state_path)
            self.assertEqual(len(state), 2)

            # Verify dashboard
            dashboard = rsv_feeder.load_dashboard(dashboard_path)
            self.assertEqual(dashboard["total"], 2)
            self.assertEqual(dashboard["buyer_ready"], 2)
            self.assertAlmostEqual(dashboard["pct"], 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
