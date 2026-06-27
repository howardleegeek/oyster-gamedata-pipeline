"""Tests for bin/e2e_recorder_backend_audit.py.

Covers:
  - Backend start / wait-ready polling
  - Fixture generation
  - Recorder smoke
  - Gate smoke
  - Verdict checking
  - Backend session counting
  - Graceful shutdown
  - Full audit orchestration (mocked)

All HTTP calls are mocked via ``respx`` / ``unittest.mock`` so no real
server is needed.  Subprocess calls are also mocked.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Repo paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

import bin.e2e_recorder_backend_audit as audit_mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_constants():
    """Reset module-level constants before each test."""
    audit_mod.BACKEND_PORT = 8500
    audit_mod.BACKEND_URL = "http://localhost:8500"
    audit_mod.HEALTH_ENDPOINT = "http://localhost:8500/v1/health"
    audit_mod.HEALTH_TIMEOUT = 5
    audit_mod.TOTAL_TIMEOUT = 30
    audit_mod.SESSION_DIR = "/tmp/e2e_session"
    yield


def _make_backend_proc_mock():
    """Create a mock subprocess.Popen for the backend (no spec to avoid conflicts)."""
    proc = mock.MagicMock()
    proc.pid = 12345
    proc.stdout = mock.MagicMock()
    proc.stderr = mock.MagicMock()
    proc.wait.return_value = 0
    return proc


@pytest.fixture
def mock_httpx_get():
    """Mock httpx.get for health checks."""
    with mock.patch("httpx.get") as mock_get:
        yield mock_get


@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run for all child processes."""
    with mock.patch("subprocess.run") as mock_run:
        yield mock_run


@pytest.fixture
def mock_popen():
    """Mock subprocess.Popen for backend startup."""
    with mock.patch("subprocess.Popen") as mock_popen:
        yield mock_popen


# ---------------------------------------------------------------------------
# _wait_for_backend tests
# ---------------------------------------------------------------------------


class TestWaitForBackend:
    """Tests for the ready-check polling function."""

    def test_backend_ready_on_first_poll(self, mock_httpx_get):
        """Backend responds 200 immediately."""
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_httpx_get.return_value = mock_resp

        result = audit_mod._wait_for_backend(timeout=1.0)

        assert result is True
        mock_httpx_get.assert_called_once()

    def test_backend_ready_after_retries(self, mock_httpx_get):
        """Backend responds 200 after a few connection errors."""
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200

        # First two calls fail, third succeeds
        mock_httpx_get.side_effect = [
            ConnectionError("refused"),
            ConnectionError("refused"),
            mock_resp,
        ]

        result = audit_mod._wait_for_backend(timeout=5.0)

        assert result is True
        assert mock_httpx_get.call_count == 3

    def test_backend_not_ready_within_timeout(self, mock_httpx_get):
        """Backend never responds within timeout."""
        mock_httpx_get.side_effect = ConnectionError("refused")

        result = audit_mod._wait_for_backend(timeout=0.5)

        assert result is False

    def test_backend_returns_500(self, mock_httpx_get):
        """Backend returns non-200 status."""
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 500
        mock_httpx_get.return_value = mock_resp

        result = audit_mod._wait_for_backend(timeout=0.5)

        assert result is False


# ---------------------------------------------------------------------------
# _count_backend_sessions tests
# ---------------------------------------------------------------------------


class TestCountBackendSessions:
    """Tests for backend session counting."""

    def test_returns_count(self):
        """Returns the number of sessions from the backend."""
        with mock.patch("httpx.get") as mock_get:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [{"id": "1"}, {"id": "2"}]
            mock_get.return_value = mock_resp

            count = audit_mod._count_backend_sessions()

            assert count == 2

    def test_returns_zero_on_error(self):
        """Returns 0 when HTTP fails."""
        with mock.patch("httpx.get") as mock_get:
            mock_get.side_effect = ConnectionError("refused")

            count = audit_mod._count_backend_sessions()

            assert count == 0

    def test_returns_zero_on_non_list_response(self):
        """Returns 0 when response is not a list."""
        with mock.patch("httpx.get") as mock_get:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"error": "not found"}
            mock_get.return_value = mock_resp

            count = audit_mod._count_backend_sessions()

            assert count == 0


# ---------------------------------------------------------------------------
# step_start_backend tests
# ---------------------------------------------------------------------------


class TestStepStartBackend:
    """Tests for backend startup."""

    def test_starts_backend_with_correct_args(self, mock_popen):
        """Popen is called with the right command."""
        mock_proc = _make_backend_proc_mock()
        mock_popen.return_value = mock_proc

        proc = audit_mod.step_start_backend()

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "backend_stub.py" in cmd[1]
        assert "--port" in cmd
        assert "8500" in cmd
        assert proc is mock_proc


# ---------------------------------------------------------------------------
# step_generate_fixture tests
# ---------------------------------------------------------------------------


class TestStepGenerateFixture:
    """Tests for fixture generation."""

    def test_success(self, mock_subprocess_run):
        """Returns True when generate_session_fixture succeeds."""
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_subprocess_run.return_value = mock_result

        result = audit_mod.step_generate_fixture()

        assert result is True
        mock_subprocess_run.assert_called_once()
        call_args = mock_subprocess_run.call_args
        cmd = call_args[0][0]
        assert "generate_session_fixture.py" in cmd[1]

    def test_failure(self, mock_subprocess_run):
        """Returns False when generate_session_fixture fails."""
        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"
        mock_subprocess_run.return_value = mock_result

        result = audit_mod.step_generate_fixture()

        assert result is False


# ---------------------------------------------------------------------------
# step_recorder_smoke tests
# ---------------------------------------------------------------------------


class TestStepRecorderSmoke:
    """Tests for recorder_local_smoke execution."""

    def test_success(self, mock_subprocess_run):
        """Returns result dict with returncode 0."""
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "BUYER_READY\n"
        mock_result.stderr = ""
        mock_subprocess_run.return_value = mock_result

        result = audit_mod.step_recorder_smoke()

        assert result["returncode"] == 0
        assert "BUYER_READY" in result["stdout"]

    def test_failure(self, mock_subprocess_run):
        """Returns result dict with non-zero returncode."""
        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "upload failed"
        mock_subprocess_run.return_value = mock_result

        result = audit_mod.step_recorder_smoke()

        assert result["returncode"] == 1
        assert "upload failed" in result["stderr"]


# ---------------------------------------------------------------------------
# step_gate_smoke tests
# ---------------------------------------------------------------------------


class TestStepGateSmoke:
    """Tests for end_to_end_gate_smoke execution."""

    def test_success_with_verdict(self, mock_subprocess_run):
        """Returns result dict with parsed verdict."""
        gate_output = {
            "session_id": "test",
            "gates": {},
            "summary": {"verdict": "BUYER_READY", "pass": 5, "fail": 0, "skip": 1},
        }
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(gate_output)
        mock_result.stderr = ""
        mock_subprocess_run.return_value = mock_result

        result = audit_mod.step_gate_smoke()

        assert result["returncode"] == 0
        assert result["verdict"] == "BUYER_READY"

    def test_invalid_json_output(self, mock_subprocess_run):
        """Returns None verdict when output is not valid JSON."""
        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "not json"
        mock_result.stderr = "error"
        mock_subprocess_run.return_value = mock_result

        result = audit_mod.step_gate_smoke()

        assert result["verdict"] is None


# ---------------------------------------------------------------------------
# step_check_verdict tests
# ---------------------------------------------------------------------------


class TestStepCheckVerdict:
    """Tests for verdict checking logic."""

    def test_buyer_ready_from_recorder(self):
        """BUYER_READY in recorder stdout is accepted."""
        recorder = {"returncode": 0, "stdout": "BUYER_READY", "stderr": ""}
        gate = {"returncode": 0, "verdict": "FAIL"}

        assert audit_mod.step_check_verdict(recorder, gate) is True

    def test_acceptable_gate_verdict(self):
        """STRICT_GATES_PASS_SYNTHETIC is accepted."""
        recorder = {"returncode": 0, "stdout": "", "stderr": ""}
        gate = {"returncode": 0, "verdict": "STRICT_GATES_PASS_SYNTHETIC"}

        assert audit_mod.step_check_verdict(recorder, gate) is True

    def test_recorder_pass_with_gate_fail(self):
        """Recorder passed but gate failed — still accepted for synthetic."""
        recorder = {"returncode": 0, "stdout": "", "stderr": ""}
        gate = {"returncode": 1, "verdict": "FAIL"}

        # Synthetic fixtures may produce FAIL due to missing real data
        assert audit_mod.step_check_verdict(recorder, gate) is True

    def test_both_fail(self):
        """Both recorder and gate failed — not accepted."""
        recorder = {"returncode": 1, "stdout": "", "stderr": "error"}
        gate = {"returncode": 1, "verdict": "FAIL"}

        assert audit_mod.step_check_verdict(recorder, gate) is False

    def test_no_verdict_no_recorder_output(self):
        """No verdict and no recorder output — not accepted."""
        recorder = {"returncode": 1, "stdout": "", "stderr": ""}
        gate = {"returncode": 1, "verdict": None}

        assert audit_mod.step_check_verdict(recorder, gate) is False


# ---------------------------------------------------------------------------
# step_check_backend_sessions tests
# ---------------------------------------------------------------------------


class TestStepCheckBackendSessions:
    """Tests for backend session count assertion."""

    def test_has_sessions(self):
        """Returns True when backend has ≥ 1 session."""
        with mock.patch.object(audit_mod, "_count_backend_sessions", return_value=1):
            assert audit_mod.step_check_backend_sessions() is True

    def test_no_sessions(self):
        """Returns False when backend has 0 sessions."""
        with mock.patch.object(audit_mod, "_count_backend_sessions", return_value=0):
            assert audit_mod.step_check_backend_sessions() is False

    def test_multiple_sessions(self):
        """Returns True when backend has multiple sessions."""
        with mock.patch.object(audit_mod, "_count_backend_sessions", return_value=5):
            assert audit_mod.step_check_backend_sessions() is True


# ---------------------------------------------------------------------------
# step_shutdown_backend tests
# ---------------------------------------------------------------------------


class TestStepShutdownBackend:
    """Tests for graceful backend shutdown."""

    def test_clean_shutdown(self):
        """SIGTERM + wait succeeds."""
        proc = _make_backend_proc_mock()
        audit_mod.step_shutdown_backend(proc)

        proc.send_signal.assert_called_once_with(signal.SIGTERM)
        proc.wait.assert_called_once_with(timeout=5)

    def test_kill_on_timeout(self):
        """SIGKILL sent when SIGTERM times out."""
        proc = _make_backend_proc_mock()
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5),
            0,
        ]

        audit_mod.step_shutdown_backend(proc)

        proc.kill.assert_called_once()

    def test_kill_on_exception(self):
        """SIGKILL sent when send_signal raises."""
        proc = _make_backend_proc_mock()
        proc.send_signal.side_effect = OSError("no such process")
        proc.wait.return_value = 0

        audit_mod.step_shutdown_backend(proc)

        proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# run_audit integration tests (fully mocked)
# ---------------------------------------------------------------------------


class TestRunAudit:
    """Tests for the full audit orchestration."""

    def test_full_success(self, mock_popen):
        """All steps pass → exit 0."""
        mock_proc = _make_backend_proc_mock()
        mock_popen.return_value = mock_proc

        with (
            mock.patch.object(audit_mod, "step_wait_ready", return_value=True),
            mock.patch.object(audit_mod, "step_generate_fixture", return_value=True),
            mock.patch.object(
                audit_mod,
                "step_recorder_smoke",
                return_value={"returncode": 0, "stdout": "BUYER_READY", "stderr": ""},
            ),
            mock.patch.object(
                audit_mod,
                "step_gate_smoke",
                return_value={
                    "returncode": 0,
                    "stdout": "{}",
                    "stderr": "",
                    "verdict": "BUYER_READY",
                },
            ),
            mock.patch.object(audit_mod, "step_check_verdict", return_value=True),
            mock.patch.object(audit_mod, "step_check_backend_sessions", return_value=True),
            mock.patch.object(audit_mod, "step_shutdown_backend"),
        ):
            rc = audit_mod.run_audit()

            assert rc == 0

    def test_backend_not_ready(self, mock_popen):
        """Backend never ready → exit 1."""
        mock_proc = _make_backend_proc_mock()
        mock_popen.return_value = mock_proc

        with (
            mock.patch.object(audit_mod, "step_wait_ready", return_value=False),
            mock.patch.object(audit_mod, "step_shutdown_backend"),
        ):
            rc = audit_mod.run_audit()

            assert rc == 1

    def test_fixture_generation_fails(self, mock_popen):
        """Fixture generation fails → exit 1."""
        mock_proc = _make_backend_proc_mock()
        mock_popen.return_value = mock_proc

        with (
            mock.patch.object(audit_mod, "step_wait_ready", return_value=True),
            mock.patch.object(audit_mod, "step_generate_fixture", return_value=False),
            mock.patch.object(audit_mod, "step_shutdown_backend"),
        ):
            rc = audit_mod.run_audit()

            assert rc == 1

    def test_recorder_fails_but_gate_passes(self, mock_popen):
        """Recorder fails → verdict check fails → exit 1."""
        mock_proc = _make_backend_proc_mock()
        mock_popen.return_value = mock_proc

        with (
            mock.patch.object(audit_mod, "step_wait_ready", return_value=True),
            mock.patch.object(audit_mod, "step_generate_fixture", return_value=True),
            mock.patch.object(
                audit_mod,
                "step_recorder_smoke",
                return_value={"returncode": 1, "stdout": "", "stderr": "upload failed"},
            ),
            mock.patch.object(
                audit_mod,
                "step_gate_smoke",
                return_value={
                    "returncode": 0,
                    "stdout": "{}",
                    "stderr": "",
                    "verdict": "BUYER_READY",
                },
            ),
            mock.patch.object(audit_mod, "step_check_verdict", return_value=False),
            mock.patch.object(audit_mod, "step_check_backend_sessions", return_value=True),
            mock.patch.object(audit_mod, "step_shutdown_backend"),
        ):
            rc = audit_mod.run_audit()

            assert rc == 1

    def test_no_backend_sessions(self, mock_popen):
        """Backend received 0 sessions → exit 1."""
        mock_proc = _make_backend_proc_mock()
        mock_popen.return_value = mock_proc

        with (
            mock.patch.object(audit_mod, "step_wait_ready", return_value=True),
            mock.patch.object(audit_mod, "step_generate_fixture", return_value=True),
            mock.patch.object(
                audit_mod,
                "step_recorder_smoke",
                return_value={"returncode": 0, "stdout": "BUYER_READY", "stderr": ""},
            ),
            mock.patch.object(
                audit_mod,
                "step_gate_smoke",
                return_value={
                    "returncode": 0,
                    "stdout": "{}",
                    "stderr": "",
                    "verdict": "BUYER_READY",
                },
            ),
            mock.patch.object(audit_mod, "step_check_verdict", return_value=True),
            mock.patch.object(audit_mod, "step_check_backend_sessions", return_value=False),
            mock.patch.object(audit_mod, "step_shutdown_backend"),
        ):
            rc = audit_mod.run_audit()

            assert rc == 1

    def test_exception_during_audit(self, mock_popen):
        """Unexpected exception → exit 1, backend still shut down."""
        mock_proc = _make_backend_proc_mock()
        mock_popen.return_value = mock_proc

        with (
            mock.patch.object(audit_mod, "step_wait_ready", side_effect=RuntimeError("boom")),
            mock.patch.object(audit_mod, "step_shutdown_backend"),
        ):
            rc = audit_mod.run_audit()

            assert rc == 1
            # Shutdown should still be called
            audit_mod.step_shutdown_backend.assert_called_once()

    def test_shutdown_called_on_failure(self, mock_popen):
        """Backend is shut down even when audit fails."""
        mock_proc = _make_backend_proc_mock()
        mock_popen.return_value = mock_proc

        with (
            mock.patch.object(audit_mod, "step_wait_ready", return_value=False),
            mock.patch.object(audit_mod, "step_shutdown_backend") as mock_shutdown,
        ):
            audit_mod.run_audit()

            mock_shutdown.assert_called_once_with(mock_proc)


# ---------------------------------------------------------------------------
# parse_args tests
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_default_args(self):
        """Default: verbose=False."""
        args = audit_mod.parse_args([])
        assert args.verbose is False

    def test_verbose_flag(self):
        """--verbose sets verbose=True."""
        args = audit_mod.parse_args(["--verbose"])
        assert args.verbose is True


# ---------------------------------------------------------------------------
# main tests
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() entry point."""

    def test_returns_exit_code(self):
        """main() returns the exit code from run_audit()."""
        with mock.patch.object(audit_mod, "run_audit", return_value=0) as mock_run:
            rc = audit_mod.main([])
            assert rc == 0
            mock_run.assert_called_once()

    def test_returns_1_on_failure(self):
        """main() returns 1 when run_audit fails."""
        with mock.patch.object(audit_mod, "run_audit", return_value=1):
            rc = audit_mod.main([])
            assert rc == 1
