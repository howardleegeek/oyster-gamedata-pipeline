#!/usr/bin/env python3
"""Tests for bin/preflight_check_v2.py — vendor pre-run environment validation.

Covers R041 preflight checks. Each public check function returns a dict
with name/ok/message/details. The CLI main() exits 0 on all-ok and 1 on
any failure.

Coverage:
- check_disk_free: ample free disk → ok=True; min_gb threshold respected;
  exception path → ok=False with error message.
- check_ram: well-formed /proc/meminfo with sufficient total → ok=True;
  malformed /proc/meminfo (no MemTotal line) → ok=False; read failure →
  ok=False.
- check_cpu_cores: os.cpu_count above threshold → ok=True; threshold
  respected; exception path → ok=False.
- check_python_version: current Python (>=3.10) → ok=True; bumped
  threshold above current → ok=False; ok stays bool.
- check_network_latency_ms: socket.connect success with low latency →
  ok=True; unreachable host → ok=False.
- check_port_available: bind succeeds (port free) → ok=True; bind fails
  (port in use) → ok=False.
- check_command_exists: shutil.which finds python3 → ok=True;
  nonexistent command → ok=False.
- run_all_checks: composes expected check list; skip_network=True omits
  network checks; test_port=… adds a port check; summary counts are
  correct.
- main CLI: --json flag emits JSON; default text format; exit code 0
  when all pass (mocked), exit code 1 when any check fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import preflight_check_v2 as m  # noqa: E402

# ---------------------------------------------------------------------------
# check_disk_free
# ---------------------------------------------------------------------------


class TestCheckDiskFree:
    """check_disk_free returns a dict with ok flag and free-gb details."""

    def test_ample_disk_ok(self):
        """1 TB free at /tmp, threshold 100 GB → ok=True with GB formatted."""
        fake_stat = mock.Mock(free=1024**4)  # 1 TB
        with mock.patch.object(m.shutil, "disk_usage", return_value=fake_stat):
            result = m.check_disk_free(path="/tmp", min_gb=100)
        assert result["name"] == "disk_free"
        assert result["ok"] is True
        assert "GB free at /tmp" in result["message"]
        assert result["details"]["path"] == "/tmp"
        assert result["details"]["min_gb"] == 100
        assert result["details"]["free_gb"] >= 100.0

    def test_below_threshold_not_ok(self):
        """10 GB free, threshold 100 GB → ok=False."""
        fake_stat = mock.Mock(free=10 * (1024**3))
        with mock.patch.object(m.shutil, "disk_usage", return_value=fake_stat):
            result = m.check_disk_free(path="/tmp", min_gb=100)
        assert result["ok"] is False

    def test_exception_returns_error(self):
        """shutil.disk_usage raises → ok=False with error message."""
        with mock.patch.object(
            m.shutil, "disk_usage", side_effect=OSError("permission denied")
        ):
            result = m.check_disk_free(path="/nope", min_gb=1)
        assert result["name"] == "disk_free"
        assert result["ok"] is False
        assert "permission denied" in result["message"]


# ---------------------------------------------------------------------------
# check_ram
# ---------------------------------------------------------------------------


class TestCheckRam:
    """check_ram parses /proc/meminfo and checks against min_gb threshold."""

    def test_ample_ram_ok(self, tmp_path: Path):
        """32 GB total, min 16 GB → ok=True with formatted message."""
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(
            "MemTotal:       33554432 kB\n"
            "MemFree:        16777216 kB\n"
        )
        with mock.patch.object(m, "open", mock.mock_open(read_data=meminfo.read_text())):
            result = m.check_ram(min_gb=16)
        assert result["name"] == "ram"
        assert result["ok"] is True
        assert "GB RAM total" in result["message"]
        assert result["details"]["total_gb"] == pytest.approx(32.0, rel=0.01)

    def test_below_threshold_not_ok(self):
        """4 GB total, min 16 GB → ok=False."""
        content = "MemTotal:       4194304 kB\n"
        with mock.patch.object(m, "open", mock.mock_open(read_data=content)):
            result = m.check_ram(min_gb=16)
        assert result["ok"] is False
        assert result["details"]["total_gb"] == pytest.approx(4.0, rel=0.01)

    def test_missing_memtotal_line(self):
        """/proc/meminfo without MemTotal line → ok=False with diagnostic."""
        content = "MemFree:        4194304 kB\n"
        with mock.patch.object(m, "open", mock.mock_open(read_data=content)):
            result = m.check_ram(min_gb=16)
        assert result["ok"] is False
        assert "Could not read meminfo" in result["message"]

    def test_read_exception(self):
        """open() raises → ok=False with error message."""
        with mock.patch.object(m, "open", side_effect=OSError("nope")):
            result = m.check_ram(min_gb=16)
        assert result["ok"] is False
        assert "nope" in result["message"]


# ---------------------------------------------------------------------------
# check_cpu_cores
# ---------------------------------------------------------------------------


class TestCheckCpuCores:
    """check_cpu_cores returns ok based on os.cpu_count vs min_cores."""

    def test_above_threshold_ok(self):
        """8 cores available, min 4 → ok=True."""
        with mock.patch.object(m.os, "cpu_count", return_value=8):
            result = m.check_cpu_cores(min_cores=4)
        assert result["name"] == "cpu_cores"
        assert result["ok"] is True
        assert "8 CPU cores" in result["message"]
        assert result["details"]["cores"] == 8

    def test_below_threshold_not_ok(self):
        """2 cores available, min 4 → ok=False."""
        with mock.patch.object(m.os, "cpu_count", return_value=2):
            result = m.check_cpu_cores(min_cores=4)
        assert result["ok"] is False

    def test_cpu_count_none_falls_back_to_zero(self):
        """os.cpu_count returns None → cores treated as 0, not ok."""
        with mock.patch.object(m.os, "cpu_count", return_value=None):
            result = m.check_cpu_cores(min_cores=4)
        assert result["ok"] is False
        assert result["details"]["cores"] == 0


# ---------------------------------------------------------------------------
# check_python_version
# ---------------------------------------------------------------------------


class TestCheckPythonVersion:
    """check_python_version validates sys.version_info against min."""

    def test_current_python_ok(self):
        """Real sys.version_info → ok=True (current Python is 3.10+)."""
        result = m.check_python_version()
        assert result["name"] == "python_version"
        assert result["ok"] is True
        assert "Python" in result["message"]
        assert "version" in result["details"]

    def test_high_threshold_not_ok(self):
        """Bump min_major to 4 → ok=False for any Python 3.x."""
        result = m.check_python_version(min_major=4, min_minor=0)
        assert result["ok"] is False

    def test_minor_boundary(self):
        """min_minor = current minor → ok=True; current+1 → ok=False."""
        cur_major, cur_minor = sys.version_info.major, sys.version_info.minor
        ok_at = m.check_python_version(min_major=cur_major, min_minor=cur_minor)
        not_ok = m.check_python_version(min_major=cur_major, min_minor=cur_minor + 1)
        assert ok_at["ok"] is True
        assert not_ok["ok"] is False


# ---------------------------------------------------------------------------
# check_network_latency_ms
# ---------------------------------------------------------------------------


class TestCheckNetworkLatencyMs:
    """check_network_latency_ms connects to target:53 and times the round-trip."""

    def _make_socket(self, connect_raises=None):
        sock = mock.Mock()
        if connect_raises is not None:
            sock.connect.side_effect = connect_raises
        return sock

    def test_low_latency_ok(self):
        """connect() succeeds quickly → ok=True with latency in ms."""
        sock = self._make_socket()
        with mock.patch.object(m.socket, "socket", return_value=sock):
            result = m.check_network_latency_ms(max_ms=5000, target="1.2.3.4")
        assert result["name"] == "network_latency"
        assert result["ok"] is True
        assert "latency to 1.2.3.4" in result["message"]
        assert result["details"]["target"] == "1.2.3.4"
        sock.close.assert_called_once()

    def test_unreachable_not_ok(self):
        """socket.error on connect → ok=False with descriptive message."""
        sock = self._make_socket(connect_raises=m.socket.error("timeout"))
        with mock.patch.object(m.socket, "socket", return_value=sock):
            result = m.check_network_latency_ms(max_ms=20, target="0.0.0.0")
        assert result["ok"] is False
        assert "Cannot reach 0.0.0.0" in result["message"]


# ---------------------------------------------------------------------------
# check_port_available
# ---------------------------------------------------------------------------


class TestCheckPortAvailable:
    """check_port_available binds to 127.0.0.1:port to test availability."""

    def test_port_free_ok(self):
        """bind() succeeds → ok=True, port available."""
        sock = mock.Mock()
        with mock.patch.object(m.socket, "socket", return_value=sock):
            result = m.check_port_available(54321)
        assert result["name"] == "port_available"
        assert result["ok"] is True
        assert "54321 is available" in result["message"]
        assert result["details"]["port"] == 54321
        sock.bind.assert_called_once_with(("127.0.0.1", 54321))
        sock.close.assert_called_once()

    def test_port_in_use_not_ok(self):
        """bind() raises OSError → ok=False, port in use."""
        sock = mock.Mock()
        sock.bind.side_effect = OSError("address in use")
        with mock.patch.object(m.socket, "socket", return_value=sock):
            result = m.check_port_available(80)
        assert result["ok"] is False
        assert "80 is in use" in result["message"]


# ---------------------------------------------------------------------------
# check_command_exists
# ---------------------------------------------------------------------------


class TestCheckCommandExists:
    """check_command_exists uses shutil.which to test PATH membership."""

    def test_command_found_ok(self):
        """shutil.which returns path → ok=True."""
        with mock.patch.object(m.shutil, "which", return_value="/usr/bin/python3"):
            result = m.check_command_exists("python3")
        assert result["name"] == "command_exists"
        assert result["ok"] is True
        assert '"python3" found' in result["message"]
        assert result["details"]["command"] == "python3"

    def test_command_missing_not_ok(self):
        """shutil.which returns None → ok=False."""
        with mock.patch.object(m.shutil, "which", return_value=None):
            result = m.check_command_exists("definitely-not-a-real-cmd-xyz")
        assert result["ok"] is False
        assert "not found" in result["message"]


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------


class TestRunAllChecks:
    """run_all_checks composes the full check set and computes summary."""

    def test_skip_network_omits_network_checks(self):
        """skip_network=True → no network_latency or network_upload."""
        captured = []

        def fake_check(name, *args, **kwargs):
            captured.append(name)
            return {"name": name, "ok": True, "message": "", "details": {}}

        with mock.patch.object(m, "check_disk_free", side_effect=lambda *a, **k: fake_check("disk_free")), \
            mock.patch.object(m, "check_ram", side_effect=lambda *a, **k: fake_check("ram")), \
            mock.patch.object(m, "check_cpu_cores", side_effect=lambda *a, **k: fake_check("cpu_cores")), \
            mock.patch.object(m, "check_python_version", side_effect=lambda *a, **k: fake_check("python_version")), \
            mock.patch.object(m, "check_network_latency_ms", side_effect=lambda *a, **k: fake_check("network_latency")), \
            mock.patch.object(m, "check_network_upload_mbps", side_effect=lambda *a, **k: fake_check("network_upload")), \
            mock.patch.object(m, "check_port_available", side_effect=lambda *a, **k: fake_check("port_available")):
            result = m.run_all_checks(skip_network=True)

        assert "network_latency" not in captured
        assert "network_upload" not in captured
        assert result["ok"] is True
        assert result["summary"]["total"] == 4

    def test_with_port_test_adds_port_check(self):
        """test_port=N → port_available check is included."""
        captured = []

        def fake_check(name, *args, **kwargs):
            captured.append(name)
            return {"name": name, "ok": True, "message": "", "details": {}}

        with mock.patch.object(m, "check_disk_free", side_effect=lambda *a, **k: fake_check("disk_free")), \
            mock.patch.object(m, "check_ram", side_effect=lambda *a, **k: fake_check("ram")), \
            mock.patch.object(m, "check_cpu_cores", side_effect=lambda *a, **k: fake_check("cpu_cores")), \
            mock.patch.object(m, "check_python_version", side_effect=lambda *a, **k: fake_check("python_version")), \
            mock.patch.object(m, "check_network_latency_ms", side_effect=lambda *a, **k: fake_check("network_latency")), \
            mock.patch.object(m, "check_network_upload_mbps", side_effect=lambda *a, **k: fake_check("network_upload")), \
            mock.patch.object(m, "check_port_available", side_effect=lambda *a, **k: fake_check("port_available")):
            result = m.run_all_checks(skip_network=False, test_port=12345)

        assert "port_available" in captured
        # default 4 hardware + 2 network + 1 port = 7
        assert result["summary"]["total"] == 7

    def test_summary_counts_failures(self):
        """summary.passed + summary.failed == summary.total; all-ok → ok=True."""
        with mock.patch.object(m, "check_disk_free", return_value={"name": "disk_free", "ok": True, "message": "", "details": {}}), \
            mock.patch.object(m, "check_ram", return_value={"name": "ram", "ok": False, "message": "", "details": {}}), \
            mock.patch.object(m, "check_cpu_cores", return_value={"name": "cpu_cores", "ok": True, "message": "", "details": {}}), \
            mock.patch.object(m, "check_python_version", return_value={"name": "python_version", "ok": True, "message": "", "details": {}}), \
            mock.patch.object(m, "check_network_latency_ms", return_value={"name": "network_latency", "ok": True, "message": "", "details": {}}), \
            mock.patch.object(m, "check_network_upload_mbps", return_value={"name": "network_upload", "ok": True, "message": "", "details": {}}):
            result = m.run_all_checks(skip_network=False)

        assert result["ok"] is False
        assert result["summary"]["total"] == 6
        assert result["summary"]["passed"] == 5
        assert result["summary"]["failed"] == 1


# ---------------------------------------------------------------------------
# main CLI
# ---------------------------------------------------------------------------


class TestMain:
    """main() parses argv, prints results, exits 0/1 based on overall ok."""

    def _stub_result(self, ok):
        return {
            "ok": ok,
            "checks": [
                {"name": "disk_free", "ok": ok, "message": "100.0 GB free", "details": {}},
            ],
            "summary": {"total": 1, "passed": 1 if ok else 0, "failed": 0 if ok else 1},
        }

    def test_json_flag_emits_json(self, capsys):
        """--json flag → stdout is parseable JSON with the result dict."""
        with mock.patch.object(m, "run_all_checks", return_value=self._stub_result(ok=True)):
            exit_code = m.main(["--json", "--no-network"])
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["ok"] is True
        assert "checks" in parsed
        assert exit_code == 0

    def test_text_format_default(self, capsys):
        """No --json → human-readable text with PASS/FAIL markers."""
        with mock.patch.object(m, "run_all_checks", return_value=self._stub_result(ok=True)):
            exit_code = m.main(["--no-network"])
        captured = capsys.readouterr()
        assert "=== Preflight Check Results ===" in captured.out
        assert "[PASS] disk_free" in captured.out
        assert "Summary:" in captured.out
        assert exit_code == 0

    def test_failure_exits_one(self, capsys):
        """Any failing check → exit code 1."""
        with mock.patch.object(m, "run_all_checks", return_value=self._stub_result(ok=False)):
            exit_code = m.main(["--no-network"])
        captured = capsys.readouterr()
        assert "[FAIL] disk_free" in captured.out
        assert exit_code == 1

    def test_port_test_passes_through(self):
        """--port-test N is forwarded to run_all_checks."""
        with mock.patch.object(m, "run_all_checks", return_value=self._stub_result(ok=True)) as rac:
            m.main(["--no-network", "--port-test", "9999"])
        # Inspect last call kwargs
        _, kwargs = rac.call_args
        assert kwargs.get("test_port") == 9999 or rac.call_args.kwargs.get("test_port") == 9999
        # Also verify positional arg form
        args, kws = rac.call_args
        assert kws.get("test_port") == 9999

    def test_no_network_flag_passes_through(self):
        """--no-network → run_all_checks receives skip_network=True."""
        with mock.patch.object(m, "run_all_checks", return_value=self._stub_result(ok=True)) as rac:
            m.main(["--no-network"])
        _, kws = rac.call_args
        assert kws.get("skip_network") is True
