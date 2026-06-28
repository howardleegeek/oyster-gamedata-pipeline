#!/usr/bin/env python3
"""
tests/test_load_test_harness.py — Tests for the load test harness.

Uses a mocked async backend (no real server needed) to verify:
  - Metrics aggregation correctness
  - Percentile calculations
  - Mock session generation
  - Recorder task behavior with mocked aiohttp
  - JSON/Markdown report generation
"""

from __future__ import annotations

import json

# noqa: E402 — sys.path manipulation must come before project imports
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin.load_test_100_recorders import (  # noqa: E402
    LoadTestMetrics,
    RecorderResult,
    aggregate_metrics,
    compute_percentile,
    generate_mock_session,
    run_load_test,
    run_recorder,
    write_json_report,
    write_markdown_report,
)

# ---------------------------------------------------------------------------
# Percentile tests
# ---------------------------------------------------------------------------


class TestComputePercentile:
    def test_empty_list(self):
        assert compute_percentile([], 50) == 0.0

    def test_single_value(self):
        assert compute_percentile([42.0], 50) == 42.0

    def test_p50_of_ten(self):
        # values 1..10, nearest-rank p50: rank = ceil(0.5*10) = 5 → value 5
        values = list(range(1, 11))
        assert compute_percentile(values, 50) == 5.0

    def test_p95_of_hundred(self):
        # values 1..100, nearest-rank p95: rank = ceil(0.95*100) = 95 → value 95
        values = list(range(1, 101))
        assert compute_percentile(values, 95) == 95.0

    def test_p99_of_hundred(self):
        # values 1..100, nearest-rank p99: rank = ceil(0.99*100) = 99 → value 99
        values = list(range(1, 101))
        assert compute_percentile(values, 99) == 99.0

    def test_p99_of_ten(self):
        # values 1..10, nearest-rank p99: rank = ceil(0.99*10) = 10 → value 10
        values = list(range(1, 11))
        assert compute_percentile(values, 99) == 10.0

    def test_unsorted_input(self):
        values = [10, 1, 5, 3, 8]
        # sorted: [1,3,5,8,10], p50: rank=ceil(0.5*5)=3 → value 5
        assert compute_percentile(values, 50) == 5.0


# ---------------------------------------------------------------------------
# Mock session generation tests
# ---------------------------------------------------------------------------


class TestGenerateMockSession:
    def test_returns_bytes(self):
        result = generate_mock_session(1)
        assert isinstance(result, bytes)

    def test_valid_json(self):
        result = generate_mock_session(42)
        data = json.loads(result)
        assert data["recorder_id"] == "recorder-0042"
        assert data["session_type"] == "mock_obs_recorder"
        assert data["duration_sec"] == 30
        assert data["frames"] == 900

    def test_nonzero_size(self):
        result = generate_mock_session(0)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Metrics aggregation tests
# ---------------------------------------------------------------------------


class TestAggregateMetrics:
    def _make_results(self, n=100, fail_count=0):
        results = []
        for i in range(n):
            results.append(
                RecorderResult(
                    recorder_id=i,
                    success=i >= fail_count,
                    latency_ms=10.0 + i * 0.5,
                    upload_size_bytes=256,
                    cpu_percent=1.0 + (i % 5) * 0.1,
                    mem_mb=50.0 + (i % 10) * 0.5,
                )
            )
        return results

    def test_all_success(self):
        results = self._make_results(n=100, fail_count=0)
        metrics = aggregate_metrics(results, 5.0, "http://localhost:8500")

        assert metrics.total_recorders == 100
        assert metrics.successful == 100
        assert metrics.failed == 0
        assert metrics.error_rate == 0.0

    def test_some_failures(self):
        results = self._make_results(n=100, fail_count=5)
        metrics = aggregate_metrics(results, 5.0, "http://localhost:8500")

        assert metrics.successful == 95
        assert metrics.failed == 5
        assert metrics.error_rate == 5.0

    def test_latency_percentiles(self):
        results = self._make_results(n=100, fail_count=0)
        metrics = aggregate_metrics(results, 5.0, "http://localhost:8500")

        # Latencies: 10.0, 10.5, 11.0, ..., 59.5
        assert metrics.p50_latency_ms > 0
        assert metrics.p95_latency_ms >= metrics.p50_latency_ms
        assert metrics.p99_latency_ms >= metrics.p95_latency_ms
        assert metrics.min_latency_ms == 10.0
        assert metrics.max_latency_ms == 59.5

    def test_throughput_calculation(self):
        results = self._make_results(n=10, fail_count=0)
        metrics = aggregate_metrics(results, 2.0, "http://localhost:8500")

        # 10 * 256 bytes / 2 seconds = 1280 bytes/sec
        assert metrics.throughput_bytes_per_sec == 1280.0

    def test_cpu_mem_averages(self):
        results = self._make_results(n=10, fail_count=0)
        metrics = aggregate_metrics(results, 2.0, "http://localhost:8500")

        assert metrics.avg_cpu_percent > 0
        assert metrics.avg_mem_mb > 0
        assert metrics.max_cpu_percent >= metrics.avg_cpu_percent
        assert metrics.max_mem_mb >= metrics.avg_mem_mb

    def test_all_failures(self):
        results = [
            RecorderResult(
                recorder_id=i,
                success=False,
                error="connection refused",
            )
            for i in range(10)
        ]
        metrics = aggregate_metrics(results, 1.0, "http://localhost:8500")

        assert metrics.successful == 0
        assert metrics.failed == 10
        assert metrics.error_rate == 100.0
        assert metrics.p50_latency_ms == 0.0

    def test_empty_results(self):
        metrics = aggregate_metrics([], 0.0, "http://localhost:8500")
        assert metrics.total_recorders == 0
        assert metrics.error_rate == 0.0


# ---------------------------------------------------------------------------
# Recorder task tests (mocked aiohttp)
# ---------------------------------------------------------------------------


class TestRunRecorder:
    @pytest.mark.asyncio
    async def test_successful_upload(self):
        """Test a recorder successfully uploading to backend."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"status": "ok", "session_id": "test-session-123"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)

        result = await run_recorder(
            recorder_id=5,
            backend_url="http://localhost:8500",
            session=mock_session,
        )

        assert result.recorder_id == 5
        assert result.success is True
        assert result.session_id == "test-session-123"
        assert result.latency_ms > 0
        assert result.upload_size_bytes > 0
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_successful_upload_when_process_metrics_unavailable(self):
        """Recorder success should not depend on psutil metrics availability."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"status": "ok", "session_id": "test-session-456"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)

        with patch(
            "bin.load_test_100_recorders.psutil.Process",
            side_effect=psutil.NoSuchProcess(pid=1234),
        ):
            result = await run_recorder(
                recorder_id=6,
                backend_url="http://localhost:8500",
                session=mock_session,
            )

        assert result.recorder_id == 6
        assert result.success is True
        assert result.session_id == "test-session-456"
        assert result.cpu_percent == 0.0
        assert result.mem_mb == 0.0
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_failed_upload_http_error(self):
        """Test a recorder getting an HTTP error."""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)

        result = await run_recorder(
            recorder_id=10,
            backend_url="http://localhost:8500",
            session=mock_session,
        )

        assert result.recorder_id == 10
        assert result.success is False
        assert "HTTP 500" in result.error

    @pytest.mark.asyncio
    async def test_failed_upload_exception(self):
        """Test a recorder getting a connection error."""
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=ConnectionError("Connection refused"))

        result = await run_recorder(
            recorder_id=15,
            backend_url="http://localhost:8500",
            session=mock_session,
        )

        assert result.recorder_id == 15
        assert result.success is False
        assert "Connection refused" in result.error


# ---------------------------------------------------------------------------
# Report generation tests
# ---------------------------------------------------------------------------


class TestReportGeneration:
    def test_write_json_report(self):
        """Test JSON report is written correctly."""
        metrics = LoadTestMetrics(
            total_recorders=100,
            successful=99,
            failed=1,
            error_rate=1.0,
            p50_latency_ms=15.0,
            p95_latency_ms=45.0,
            p99_latency_ms=80.0,
            backend_url="http://localhost:8500",
            timestamp="2026-01-01T00:00:00Z",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_results.json"
            write_json_report(metrics, output_path)

            assert output_path.exists()
            with open(output_path) as f:
                data = json.load(f)

            assert data["total_recorders"] == 100
            assert data["successful"] == 99
            assert data["p95_latency_ms"] == 45.0
            assert data["backend_url"] == "http://localhost:8500"

    def test_write_markdown_report(self):
        """Test Markdown report is written correctly."""
        metrics = LoadTestMetrics(
            total_recorders=50,
            successful=50,
            failed=0,
            error_rate=0.0,
            p50_latency_ms=12.0,
            p95_latency_ms=35.0,
            p99_latency_ms=60.0,
            min_latency_ms=5.0,
            max_latency_ms=70.0,
            mean_latency_ms=18.0,
            median_latency_ms=12.0,
            total_bytes_uploaded=12800,
            throughput_bytes_per_sec=6400.0,
            total_duration_sec=2.0,
            avg_cpu_percent=2.5,
            avg_mem_mb=55.0,
            backend_url="http://localhost:8500",
            timestamp="2026-01-01T00:00:00Z",
            recorder_results=[
                {
                    "recorder_id": i,
                    "success": True,
                    "latency_ms": 10.0 + i,
                    "upload_size_bytes": 256,
                    "cpu_percent": 1.0,
                    "mem_mb": 50.0,
                    "error": "",
                }
                for i in range(50)
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_results.md"
            write_markdown_report(metrics, output_path)

            assert output_path.exists()
            content = output_path.read_text()

            assert "# Load Test Results" in content
            assert "| Metric | Value |" in content
            assert "p95 Latency" in content
            assert "35.0ms" in content
            assert "Per-Recorder Results" in content


# ---------------------------------------------------------------------------
# Full load test with mocked backend
# ---------------------------------------------------------------------------


def _make_mock_response(status=200, json_body=None):
    """Helper to create a proper async context manager mock for aiohttp responses."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_body or {})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)
    return mock_resp


class TestRunLoadTest:
    @pytest.mark.asyncio
    async def test_load_test_with_mocked_backend(self):
        """Test full load test flow with mocked aiohttp responses."""
        call_count = 0

        def mock_get(*args, **kwargs):
            # Returns an async context manager directly (like aiohttp does)
            return _make_mock_response(
                status=200,
                json_body={"status": "ok", "sessions_received": 0},
            )

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_mock_response(
                status=200,
                json_body={"status": "ok", "session_id": f"session-{call_count}"},
            )

        # Create a mock session that works as async context manager
        mock_session = MagicMock()
        mock_session.get = mock_get
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            metrics = await run_load_test(
                backend_url="http://localhost:8500",
                num_recorders=10,
            )

            assert metrics.total_recorders == 10
            assert metrics.successful == 10
            assert metrics.failed == 0
            assert metrics.error_rate == 0.0
            assert metrics.p95_latency_ms < 1000  # Should be well under 1s with mock

    @pytest.mark.asyncio
    async def test_load_test_backend_unreachable(self):
        """Test load test fails gracefully when backend is unreachable."""
        mock_session = MagicMock()
        mock_session.get = lambda *a, **kw: _make_mock_response(status=503)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            pytest.raises(RuntimeError, match="Backend not healthy")
        ):
            await run_load_test(
                    backend_url="http://localhost:8500",
                    num_recorders=10,
                )


# ---------------------------------------------------------------------------
# RecorderResult dataclass tests
# ---------------------------------------------------------------------------


class TestRecorderResult:
    def test_default_values(self):
        r = RecorderResult(recorder_id=0)
        assert r.success is False
        assert r.latency_ms == 0.0
        assert r.error == ""

    def test_asdict(self):
        r = RecorderResult(
            recorder_id=1,
            success=True,
            latency_ms=15.5,
            upload_size_bytes=512,
        )
        d = asdict(r)
        assert d["recorder_id"] == 1
        assert d["success"] is True
        assert d["latency_ms"] == 15.5


# ---------------------------------------------------------------------------
# Integration: verify JSON output meets acceptance criteria
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    def test_p95_under_1000ms_with_mock_data(self):
        """Verify p95 latency is under 1000ms threshold with stub-like data."""
        # Simulate stub latencies (5-50ms range)
        results = [
            RecorderResult(
                recorder_id=i,
                success=True,
                latency_ms=5.0 + (i % 50) * 0.9,
                upload_size_bytes=256,
            )
            for i in range(100)
        ]
        metrics = aggregate_metrics(results, 3.0, "http://localhost:8500")
        assert metrics.p95_latency_ms < 1000

    def test_error_rate_under_1_percent(self):
        """Verify error rate is under 1% with mostly successful results."""
        results = [
            RecorderResult(
                recorder_id=i,
                success=True,
                latency_ms=10.0,
                upload_size_bytes=256,
            )
            for i in range(100)
        ]
        metrics = aggregate_metrics(results, 3.0, "http://localhost:8500")
        assert metrics.error_rate < 1.0

    def test_json_report_has_required_fields(self):
        """Verify JSON report contains all required fields."""
        results = [
            RecorderResult(
                recorder_id=i,
                success=True,
                latency_ms=10.0 + i * 0.1,
                upload_size_bytes=256,
                cpu_percent=1.5,
                mem_mb=50.0,
            )
            for i in range(10)
        ]
        metrics = aggregate_metrics(results, 2.0, "http://localhost:8500")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "results.json"
            write_json_report(metrics, output_path)

            with open(output_path) as f:
                data = json.load(f)

            required_fields = [
                "p50_latency_ms",
                "p95_latency_ms",
                "p99_latency_ms",
                "error_rate",
                "total_recorders",
                "successful",
                "failed",
                "throughput_bytes_per_sec",
                "avg_cpu_percent",
                "avg_mem_mb",
                "recorder_results",
                "backend_url",
                "timestamp",
            ]
            for field_name in required_fields:
                assert field_name in data, f"Missing field: {field_name}"
