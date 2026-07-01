"""Tests for bin/autoresearch_recovery_time.py — G119 crash-recovery
measurement: find/kill/start adapter, wait for first new clip, run trials,
emit JSON report."""

from __future__ import annotations

import json
import logging
import signal
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the bin module is importable
BIN_DIR = Path(__file__).parent.parent.parent / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import autoresearch_recovery_time as recovery  # noqa: E402

# -----------------------------------------------------------------------
# find_adapter_pid
# -----------------------------------------------------------------------


class TestFindAdapterPid:
    """Tests for find_adapter_pid() — pgrep wrapper."""

    def test_returns_first_pid_on_success(self):
        completed = subprocess.CompletedProcess(
            args=["pgrep"], returncode=0, stdout="1234\n5678\n", stderr=""
        )
        with mock.patch.object(recovery.subprocess, "run", return_value=completed):
            assert recovery.find_adapter_pid("adapter") == 1234

    def test_returns_none_on_nonzero_returncode(self):
        completed = subprocess.CompletedProcess(
            args=["pgrep"], returncode=1, stdout="", stderr=""
        )
        with mock.patch.object(recovery.subprocess, "run", return_value=completed):
            assert recovery.find_adapter_pid("adapter") is None

    def test_returns_none_on_empty_stdout(self):
        completed = subprocess.CompletedProcess(
            args=["pgrep"], returncode=0, stdout="   \n", stderr=""
        )
        with mock.patch.object(recovery.subprocess, "run", return_value=completed):
            assert recovery.find_adapter_pid("adapter") is None

    def test_returns_none_on_oserror(self):
        with mock.patch.object(
            recovery.subprocess, "run", side_effect=OSError("no pgrep")
        ):
            assert recovery.find_adapter_pid("adapter") is None

    def test_returns_none_on_valueerror(self):
        completed = subprocess.CompletedProcess(
            args=["pgrep"], returncode=0, stdout="notanumber\n", stderr=""
        )
        with mock.patch.object(recovery.subprocess, "run", return_value=completed):
            assert recovery.find_adapter_pid("adapter") is None


# -----------------------------------------------------------------------
# kill_adapter
# -----------------------------------------------------------------------


class TestKillAdapter:
    """Tests for kill_adapter() — SIGKILL wrapper."""

    def test_returns_true_on_success(self):
        with mock.patch.object(recovery.os, "kill") as mkill, mock.patch.object(
            recovery.time, "sleep"
        ):
            assert recovery.kill_adapter(4242) is True
            mkill.assert_called_once_with(4242, signal.SIGKILL)

    def test_returns_false_on_process_lookup_error(self):
        with mock.patch.object(
            recovery.os, "kill", side_effect=ProcessLookupError("gone")
        ):
            assert recovery.kill_adapter(9999) is False

    def test_returns_false_on_oserror(self):
        with mock.patch.object(recovery.os, "kill", side_effect=OSError("denied")):
            assert recovery.kill_adapter(9999) is False


# -----------------------------------------------------------------------
# start_adapter
# -----------------------------------------------------------------------


class TestStartAdapter:
    """Tests for start_adapter() — Popen wrapper."""

    def test_returns_popen_on_success(self):
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.pid = 7777
        with mock.patch.object(recovery.subprocess, "Popen", return_value=fake_proc):
            proc = recovery.start_adapter(["python", "adapter.py"], "/tmp/work")
        assert proc is fake_proc
        assert proc.pid == 7777

    def test_returns_none_on_oserror(self):
        with mock.patch.object(
            recovery.subprocess,
            "Popen",
            side_effect=OSError("exec failed"),
        ):
            assert recovery.start_adapter(["python", "adapter.py"], "/tmp/work") is None


# -----------------------------------------------------------------------
# wait_for_first_clip
# -----------------------------------------------------------------------


class TestWaitForFirstClip:
    """Tests for wait_for_first_clip() — polls clip dir for new files."""

    def test_returns_true_when_new_clip_appears(self, tmp_path):
        # Pre-populate a file in the initial set
        (tmp_path / "old.mp4").write_bytes(b"old")
        # Schedule a "new" file to appear after the first poll
        real_glob = tmp_path.glob
        call_count = {"n": 0}

        def fake_glob(self, pattern):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return iter([tmp_path / "old.mp4"])
            return iter([tmp_path / "old.mp4", tmp_path / "fresh.mp4"])

        with mock.patch.object(Path, "glob", fake_glob), mock.patch.object(
            recovery.time, "sleep"
        ):
            ok, elapsed = recovery.wait_for_first_clip(tmp_path, timeout=5.0)
        assert ok is True
        assert elapsed >= 0.0

    def test_returns_false_on_timeout(self, tmp_path):
        # Always returns only the same set → never sees a new file
        with mock.patch.object(
            recovery.time, "sleep"
        ), mock.patch.object(recovery.time, "time", side_effect=[0.0, 100.0]):
            ok, elapsed = recovery.wait_for_first_clip(tmp_path, timeout=10.0)
        assert ok is False
        assert elapsed == 10.0

    def test_ignores_non_clip_extensions(self, tmp_path):
        real_glob = tmp_path.glob
        call_count = {"n": 0}

        def fake_glob(self, pattern):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return iter([])
            return iter([tmp_path / "junk.txt", tmp_path / "notes.md"])

        with mock.patch.object(Path, "glob", fake_glob), mock.patch.object(
            recovery.time, "sleep"
        ):
            ok, elapsed = recovery.wait_for_first_clip(tmp_path, timeout=2.0)
        assert ok is False
        assert elapsed == 2.0

    def test_handles_missing_clip_dir(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        # clip_dir.exists() is False → initial_files empty, current_files empty
        ok, elapsed = recovery.wait_for_first_clip(missing, timeout=0.05)
        assert ok is False

    def test_recognises_all_clip_extensions(self, tmp_path):
        # Pre-stage a file (in initial snapshot), then have glob() return an
        # additional new clip on the second poll.
        (tmp_path / "existing.mp4").write_bytes(b"x")
        call_count = {"n": 0}

        def fake_glob(self, pattern):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return iter([tmp_path / "existing.mp4"])
            # Second poll: return a fresh new clip with each supported ext
            return iter(
                [
                    tmp_path / "existing.mp4",
                    tmp_path / "new.mp4",
                    tmp_path / "new.avi",
                    tmp_path / "new.mov",
                    tmp_path / "new.mkv",
                    tmp_path / "new.MP4",
                ]
            )

        with mock.patch.object(Path, "glob", fake_glob), mock.patch.object(
            recovery.time, "sleep"
        ):
            ok, _elapsed = recovery.wait_for_first_clip(tmp_path, timeout=5.0)
        assert ok is True


# -----------------------------------------------------------------------
# run_single_trial
# -----------------------------------------------------------------------


class TestRunSingleTrial:
    """Tests for run_single_trial() — single crash-recovery iteration."""

    def test_returns_none_when_no_pid_and_start_fails(self, tmp_path):
        with mock.patch.object(recovery, "find_adapter_pid", return_value=None), mock.patch.object(
            recovery, "start_adapter", return_value=None
        ):
            assert recovery.run_single_trial(["echo"], tmp_path, "/tmp", "adapter") is None

    def test_returns_none_when_kill_fails(self, tmp_path):
        with mock.patch.object(
            recovery, "find_adapter_pid", return_value=1234
        ), mock.patch.object(recovery, "kill_adapter", return_value=False):
            assert recovery.run_single_trial(["echo"], tmp_path, "/tmp", "adapter") is None

    def test_returns_recovery_time_on_success(self, tmp_path):
        fake_proc = mock.MagicMock()
        with mock.patch.object(
            recovery, "find_adapter_pid", return_value=1234
        ), mock.patch.object(recovery, "kill_adapter", return_value=True), mock.patch.object(
            recovery, "start_adapter", return_value=fake_proc
        ), mock.patch.object(
            recovery, "wait_for_first_clip", return_value=(True, 1.23)
        ):
            assert recovery.run_single_trial(["echo"], tmp_path, "/tmp", "adapter") == 1.23

    def test_returns_none_on_wait_timeout_and_terminates(self, tmp_path):
        fake_proc = mock.MagicMock()
        with mock.patch.object(
            recovery, "find_adapter_pid", return_value=1234
        ), mock.patch.object(recovery, "kill_adapter", return_value=True), mock.patch.object(
            recovery, "start_adapter", return_value=fake_proc
        ), mock.patch.object(
            recovery, "wait_for_first_clip", return_value=(False, 5.0)
        ):
            assert recovery.run_single_trial(["echo"], tmp_path, "/tmp", "adapter") is None
        fake_proc.terminate.assert_called_once()


# -----------------------------------------------------------------------
# main()
# -----------------------------------------------------------------------


class TestMain:
    """Tests for main() — CLI + JSON output."""

    def test_missing_clip_dir_returns_1(self, tmp_path, caplog):
        missing = tmp_path / "nope"
        with caplog.at_level(logging.ERROR):
            rc = recovery.main(["--adapter-cmd", "echo", "--clip-dir", str(missing)])
        assert rc == 1

    def test_no_successful_trials_returns_1(self, tmp_path):
        with mock.patch.object(
            recovery, "run_single_trial", return_value=None
        ), mock.patch.object(recovery.tempfile, "TemporaryDirectory") as mtd:
            mtd.return_value.__enter__.return_value = "/tmp/fake"
            mtd.return_value.__exit__.return_value = False
            rc = recovery.main(["--adapter-cmd", "echo", "--clip-dir", str(tmp_path)])
        assert rc == 1

    def test_writes_json_output_with_aggregates(self, tmp_path):
        with mock.patch.object(
            recovery, "run_single_trial", side_effect=[1.0, 2.0, 3.0, None, None]
        ), mock.patch.object(recovery.time, "sleep"), mock.patch.object(
            recovery.tempfile, "TemporaryDirectory"
        ) as mtd:
            mtd.return_value.__enter__.return_value = "/tmp/fake"
            mtd.return_value.__exit__.return_value = False
            out = tmp_path / "results.json"
            rc = recovery.main(
                [
                    "--adapter-cmd",
                    "echo",
                    "--clip-dir",
                    str(tmp_path),
                    "--trials",
                    "5",
                    "--timeout",
                    "1.0",
                    "--output",
                    str(out),
                ]
            )
        assert rc == 0
        data = json.loads(out.read_text())
        assert data["trials"] == 5
        assert data["successful_trials"] == 3
        assert data["recovery_times"] == [1.0, 2.0, 3.0]
        assert data["mean_recovery_time"] == 2.0
        assert data["min_recovery_time"] == 1.0
        assert data["max_recovery_time"] == 3.0
        assert data["successful_trials"] == 3

    def test_uses_default_trials_and_timeout(self, tmp_path):
        with mock.patch.object(recovery, "run_single_trial", return_value=0.5), mock.patch.object(
            recovery.time, "sleep"
        ), mock.patch.object(recovery.tempfile, "TemporaryDirectory") as mtd:
            mtd.return_value.__enter__.return_value = "/tmp/fake"
            mtd.return_value.__exit__.return_value = False
            rc = recovery.main(["--adapter-cmd", "echo", "--clip-dir", str(tmp_path)])
        assert rc == 0

    def test_help_exits_cleanly(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            recovery.main(["--help"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "recovery time" in captured.out.lower() or "adapter" in captured.out.lower()

    def test_adapter_name_is_passed_through(self, tmp_path):
        captured_kwargs = {}

        def fake_trial(cmd, clip_dir, workdir, adapter_name):
            captured_kwargs["adapter_name"] = adapter_name
            return 0.1

        with mock.patch.object(recovery, "run_single_trial", side_effect=fake_trial), mock.patch.object(
            recovery.time, "sleep"
        ), mock.patch.object(recovery.tempfile, "TemporaryDirectory") as mtd:
            mtd.return_value.__enter__.return_value = "/tmp/fake"
            mtd.return_value.__exit__.return_value = False
            rc = recovery.main(
                [
                    "--adapter-cmd",
                    "echo",
                    "--clip-dir",
                    str(tmp_path),
                    "--adapter-name",
                    "myadapter",
                ]
            )
        assert rc == 0
        assert captured_kwargs["adapter_name"] == "myadapter"


# -----------------------------------------------------------------------
# Module-level / integration sanity
# -----------------------------------------------------------------------


class TestModuleSanity:
    """Module-level invariants — guard against import-time regressions."""

    def test_module_imports_cleanly(self):
        # If this fails, the import at the top of the file already broke
        assert hasattr(recovery, "find_adapter_pid")
        assert hasattr(recovery, "kill_adapter")
        assert hasattr(recovery, "start_adapter")
        assert hasattr(recovery, "wait_for_first_clip")
        assert hasattr(recovery, "run_single_trial")
        assert hasattr(recovery, "main")
        assert callable(recovery.main)
