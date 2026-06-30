#!/usr/bin/env python3
"""Tests for bin/red_team_invalid_systeminfo.py — red team validator for
systeminfo.json missing required 'gpu' key.

Covers:
  * create_invalid_systeminfo writes a config without 'gpu' and returns the path
  * run_lint_v2 shells out to `python3 -m lint --version 2 --config <path>`
    and returns a (returncode, stdout, stderr) tuple
  * validate_rejection: non-zero return code is OK iff stderr mentions gpu /
    required / missing / key
  * main() exit codes 0 (rejected), 1 (accepted), 2 (error), and the
    FileNotFoundError fallback that also exits 0
  * argparse: --verbose flag is recognized
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

# Import the module under test
import red_team_invalid_systeminfo as rt

# ---------------------------------------------------------------------------
# create_invalid_systeminfo
# ---------------------------------------------------------------------------


class TestCreateInvalidSysteminfo:
    """create_invalid_systeminfo writes a config without 'gpu' and returns the path."""

    def test_writes_file_to_target_dir(self, tmp_path):
        """The config file is created inside target_dir and is returned."""
        out = rt.create_invalid_systeminfo(tmp_path)
        assert out.exists()
        assert out.parent == tmp_path
        assert out.name == "systeminfo.json"

    def test_written_content_is_valid_json(self, tmp_path):
        """The file contents are valid JSON with the expected fields."""
        out = rt.create_invalid_systeminfo(tmp_path)
        data = json.loads(out.read_text())
        assert data["hostname"] == "test-machine-01"
        assert data["os"] == "Ubuntu 22.04 LTS"
        assert data["cpu"] == {"model": "Intel Xeon", "cores": 28}
        assert data["memory_gb"] == 128

    def test_gpu_key_is_missing(self, tmp_path):
        """The 'gpu' key is intentionally absent — that's the red-team case."""
        out = rt.create_invalid_systeminfo(tmp_path)
        data = json.loads(out.read_text())
        assert "gpu" not in data

    def test_returned_path_matches_what_was_written(self, tmp_path):
        """Returned Path object points at the file that was actually written."""
        out = rt.create_invalid_systeminfo(tmp_path)
        assert out == tmp_path / "systeminfo.json"
        assert out.read_text()  # non-empty


# ---------------------------------------------------------------------------
# run_lint_v2
# ---------------------------------------------------------------------------


class TestRunLintV2:
    """run_lint_v2 shells out to `python3 -m lint --version 2 --config <cfg>`."""

    def test_returns_three_tuple(self, tmp_path):
        """Returns a (returncode, stdout, stderr) tuple from subprocess.run."""
        cfg = tmp_path / "systeminfo.json"
        cfg.write_text("{}")
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="out", stderr="err")
        with patch.object(rt.subprocess, "run", return_value=fake) as run_mock:
            ret, out, err = rt.run_lint_v2(cfg)
        assert ret == 1
        assert out == "out"
        assert err == "err"
        # Verify the command was built correctly
        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        cmd = args[0]
        assert cmd[0] == "python3"
        assert cmd[1:3] == ["-m", "lint"]
        assert "--version" in cmd
        assert "2" in cmd
        assert "--config" in cmd
        assert str(cfg) in cmd
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 60

    def test_returncode_zero_is_propagated(self, tmp_path):
        """A zero returncode from subprocess is returned as-is."""
        cfg = tmp_path / "systeminfo.json"
        cfg.write_text("{}")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.object(rt.subprocess, "run", return_value=fake):
            ret, _, _ = rt.run_lint_v2(cfg)
        assert ret == 0

    def test_path_argument_is_stringified(self, tmp_path):
        """The config path is converted to str() in the subprocess command."""
        cfg = tmp_path / "systeminfo.json"
        cfg.write_text("{}")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.object(rt.subprocess, "run", return_value=fake) as run_mock:
            rt.run_lint_v2(cfg)
        args, _ = run_mock.call_args
        cmd = args[0]
        # The config path is the last positional arg, stringified
        assert cmd[-1] == str(cfg)


# ---------------------------------------------------------------------------
# validate_rejection
# ---------------------------------------------------------------------------


class TestValidateRejection:
    """validate_rejection: non-zero return code is OK iff stderr mentions
    gpu / required / missing / key (case-insensitive)."""

    def test_returncode_zero_means_not_rejected(self):
        """If lint returned 0, the config was accepted → not rejected."""
        assert rt.validate_rejection(0, "anything goes") is False

    def test_nonzero_with_gpu_keyword_is_rejected(self):
        """Non-zero return code + 'gpu' in stderr is a valid rejection."""
        assert rt.validate_rejection(1, "error: gpu key is required") is True

    def test_nonzero_with_required_keyword_is_rejected(self):
        """Non-zero return code + 'required' in stderr is a valid rejection."""
        assert rt.validate_rejection(2, "field required but missing") is True

    def test_nonzero_with_missing_keyword_is_rejected(self):
        """Non-zero return code + 'missing' in stderr is a valid rejection."""
        assert rt.validate_rejection(1, "missing 'gpu'") is True

    def test_nonzero_with_key_keyword_is_rejected(self):
        """Non-zero return code + 'key' in stderr is a valid rejection."""
        assert rt.validate_rejection(1, "key 'gpu' not found") is True

    def test_keyword_match_is_case_insensitive(self):
        """The keyword check is case-insensitive."""
        assert rt.validate_rejection(1, "GPU key REQUIRED") is True
        assert rt.validate_rejection(1, "Missing Key 'GPU'") is True

    def test_nonzero_without_relevant_keyword_is_not_rejected(self):
        """Non-zero return code with an unrelated stderr is NOT a valid rejection."""
        # No keyword match — validate_rejection returns False (i.e. lint failed
        # for an unrelated reason, which is not the right behavior we want to
        # certify as a "pass").
        assert rt.validate_rejection(1, "internal: something else broke") is False

    def test_nonzero_with_empty_stderr_is_not_rejected(self):
        """Non-zero return code but empty stderr is NOT a valid rejection."""
        assert rt.validate_rejection(1, "") is False


# ---------------------------------------------------------------------------
# main() CLI behavior
# ---------------------------------------------------------------------------


class TestMainExitCodes:
    """main() exit codes: 0=pass, 1=fail, 2=error."""

    def test_exit_zero_when_lint_rejects_with_keyword(self, tmp_path, capsys):
        """Lint returns 1 with 'gpu' in stderr → main returns 0 (PASS)."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error: gpu key is required"
        )
        with patch.object(rt, "create_invalid_systeminfo") as create_mock, \
             patch.object(rt.subprocess, "run", return_value=fake):
            create_mock.return_value = tmp_path / "systeminfo.json"
            code = rt.main([])
        captured = capsys.readouterr()
        assert code == 0
        assert "[PASS]" in captured.out
        assert "rejected" in captured.out.lower()

    def test_exit_one_when_lint_accepts(self, tmp_path, capsys):
        """Lint returns 0 → main returns 1 (FAIL — bad config was accepted)."""
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )
        with patch.object(rt, "create_invalid_systeminfo") as create_mock, \
             patch.object(rt.subprocess, "run", return_value=fake):
            create_mock.return_value = tmp_path / "systeminfo.json"
            code = rt.main([])
        captured = capsys.readouterr()
        assert code == 1
        assert "[FAIL]" in captured.out

    def test_exit_two_when_lint_rejects_with_unrelated_stderr(self, tmp_path, capsys):
        """Lint returns 1 with no relevant keyword → main returns 1 (FAIL).

        Per the spec, 'unrelated' stderr means validate_rejection returns False,
        so main() prints [FAIL] and exits 1. (The 2 path is reserved for
        caught exceptions.)
        """
        fake = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="something completely unrelated"
        )
        with patch.object(rt, "create_invalid_systeminfo") as create_mock, \
             patch.object(rt.subprocess, "run", return_value=fake):
            create_mock.return_value = tmp_path / "systeminfo.json"
            code = rt.main([])
        captured = capsys.readouterr()
        assert code == 1
        assert "[FAIL]" in captured.out

    def test_exit_two_on_subprocess_exception(self, tmp_path, capsys):
        """Any other exception in the main flow returns 2 (ERROR)."""
        with patch.object(rt, "create_invalid_systeminfo") as create_mock, \
             patch.object(rt.subprocess, "run", side_effect=RuntimeError("boom")):
            create_mock.return_value = tmp_path / "systeminfo.json"
            code = rt.main([])
        captured = capsys.readouterr()
        assert code == 2
        assert "[ERROR]" in captured.out
        assert "boom" in captured.out

    def test_exit_zero_on_filenotfound(self, tmp_path, capsys):
        """FileNotFoundError in run_lint_v2 is treated as a PASS (exit 0).

        Rationale (per source): if the `lint` module isn't installed locally,
        we can't actually exercise the validator, so we assume correct
        behavior. This is documented in the docstring.
        """
        with patch.object(rt, "create_invalid_systeminfo") as create_mock, \
             patch.object(rt.subprocess, "run", side_effect=FileNotFoundError):
            create_mock.return_value = tmp_path / "systeminfo.json"
            code = rt.main([])
        captured = capsys.readouterr()
        assert code == 0
        assert "[PASS]" in captured.out
        assert "not available" in captured.out.lower()

    def test_verbose_flag_prints_config_path(self, tmp_path, capsys):
        """With --verbose, main prints the config path and contents."""
        cfg = tmp_path / "systeminfo.json"
        cfg.write_text(json.dumps({"hostname": "test-machine-01"}))
        fake = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="gpu key required"
        )
        with patch.object(rt, "create_invalid_systeminfo", return_value=cfg), \
             patch.object(rt.subprocess, "run", return_value=fake):
            code = rt.main(["--verbose"])
        captured = capsys.readouterr()
        assert code == 0
        assert "[INFO]" in captured.out
        assert str(cfg) in captured.out
        assert "Lint exit=1" in captured.out

    def test_uses_real_tempfile_when_unmocked(self, tmp_path, monkeypatch):
        """Without mocking create_invalid_systeminfo, main uses a real temp dir.

        This is an end-to-end smoke test: the whole flow runs against a real
        temp directory and a fake lint subprocess.
        """
        monkeypatch.chdir(tmp_path)
        fake = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="missing gpu"
        )
        with patch.object(rt.subprocess, "run", return_value=fake):
            code = rt.main([])
        assert code == 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


class TestArgparse:
    """The argument parser accepts --verbose / -v."""

    def test_main_is_callable(self):
        """main is exposed as a callable so the CLI works as `python3 -m`."""
        assert callable(rt.main)

    def test_main_accepts_verbose_short_flag(self, tmp_path, capsys):
        """main() accepts -v as a synonym for --verbose."""
        cfg = tmp_path / "systeminfo.json"
        cfg.write_text("{}")
        fake = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="gpu key required"
        )
        with patch.object(rt, "create_invalid_systeminfo", return_value=cfg), \
             patch.object(rt.subprocess, "run", return_value=fake):
            code = rt.main(["-v"])
        captured = capsys.readouterr()
        assert code == 0
        assert "[INFO]" in captured.out

    def test_main_rejects_unknown_arg(self, tmp_path):
        """main() returns 2 (caught SystemExit) for unknown CLI args."""
        cfg = tmp_path / "systeminfo.json"
        cfg.write_text("{}")
        with patch.object(rt, "create_invalid_systeminfo", return_value=cfg):
            # argparse calls SystemExit(2) on unknown args, which the broad
            # `except Exception` does NOT catch — so this exits the test
            # process. We assert that by checking SystemExit is raised.
            with pytest.raises(SystemExit):
                rt.main(["--definitely-not-a-flag"])
        # If we got here, SystemExit propagated (i.e. unknown args rejected).
        assert True
