#!/usr/bin/env python3
"""
bin/load_test_100_recorders.py — Concurrent load test harness.

Simulates N (default 100) recorder clients concurrently uploading
fake sessions to a backend stub. Measures latency, throughput,
error rate, CPU and memory usage per recorder.

Usage
-----
    # Start backend stub first:
    python3 bin/backend_stub.py --port 8500 &

    # Run load test:
    python3 bin/load_test_100_recorders.py --backend-url http://localhost:8500 -n 100

Output
------
    dashboard/load_test_results.json  — Full metrics
    dashboard/load_test_results.md    — Markdown summary table
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import aiohttp
import psutil

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [load-test] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RecorderResult:
    """Result from a single recorder task."""

    recorder_id: int
    success: bool = False
    latency_ms: float = 0.0
    upload_size_bytes: int = 0
    error: str = ""
    cpu_percent: float = 0.0
    mem_mb: float = 0.0
    session_id: str = ""


@dataclass
class LoadTestMetrics:
    """Aggregated metrics from the full load test run."""

    total_recorders: int = 0
    successful: int = 0
    failed: int = 0
    error_rate: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    mean_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    total_bytes_uploaded: int = 0
    throughput_bytes_per_sec: float = 0.0
    total_duration_sec: float = 0.0
    avg_cpu_percent: float = 0.0
    avg_mem_mb: float = 0.0
    max_cpu_percent: float = 0.0
    max_mem_mb: float = 0.0
    recorder_results: list[dict] = field(default_factory=list)
    backend_url: str = ""
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Mock session generator
# ---------------------------------------------------------------------------


def generate_mock_session(recorder_id: int) -> bytes:
    """
    Generate a fake session payload.

    In production this would be a tarball from mock_obs_recorder.
    For load testing we simulate with a small JSON payload.
    """
    import json as _json

    session = {
        "recorder_id": f"recorder-{recorder_id:04d}",
        "session_type": "mock_obs_recorder",
        "duration_sec": 30,  # Simulated 30s session
        "frames": 900,  # 30fps * 30s
        "video_size_mb": 15,
        "metadata": {
            "game": "minecraft",
            "resolution": "1920x1080",
            "fps": 30,
        },
    }
    return _json.dumps(session).encode("utf-8")


# ---------------------------------------------------------------------------
# Single recorder task
# ---------------------------------------------------------------------------


def _current_process_for_metrics() -> psutil.Process | None:
    """Return the current process for optional metrics collection."""
    try:
        return psutil.Process(os.getpid())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
        logger.debug("Process metrics unavailable before upload: %s", exc)
        return None


def _prime_process_metrics(proc: psutil.Process | None) -> None:
    """Prime CPU metrics without letting psutil failures affect uploads."""
    if proc is None:
        return
    try:
        proc.cpu_percent(interval=None)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
        logger.debug("Process metrics prime failed: %s", exc)


def _sample_process_metrics(proc: psutil.Process | None) -> tuple[float, float]:
    """Best-effort CPU and memory sampling for load-test reporting."""
    if proc is None:
        return 0.0, 0.0
    try:
        cpu_percent = round(proc.cpu_percent(interval=None), 2)
        mem_mb = round(proc.memory_info().rss / (1024 * 1024), 2)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
        logger.debug("Process metrics sample failed: %s", exc)
        return 0.0, 0.0
    return cpu_percent, mem_mb


async def run_recorder(
    recorder_id: int,
    backend_url: str,
    session: aiohttp.ClientSession,
) -> RecorderResult:
    """
    Simulate one recorder: generate mock session, upload to backend.

    Uses 1s sleep to simulate the 30s recording session (per spec:
    "不真录 30s — 测试 sleep 1s 模拟").
    """
    result = RecorderResult(recorder_id=recorder_id)

    try:
        proc = _current_process_for_metrics()
        _prime_process_metrics(proc)

        # Simulate 30s recording session with 1s sleep
        await asyncio.sleep(1.0)

        # Generate mock session payload
        payload = generate_mock_session(recorder_id)
        result.upload_size_bytes = len(payload)

        # Upload to backend stub
        url = f"{backend_url}/v1/session/upload"
        data = aiohttp.FormData()
        data.add_field(
            "session_data",
            payload,
            filename=f"session-{recorder_id}.json",
            content_type="application/json",
        )
        data.add_field("recorder_id", f"recorder-{recorder_id:04d}")

        start = time.monotonic()
        async with session.post(url, data=data) as resp:
            elapsed_ms = (time.monotonic() - start) * 1000
            result.latency_ms = round(elapsed_ms, 2)

            if resp.status == 200:
                body = await resp.json()
                result.success = True
                result.session_id = body.get("session_id", "")
            else:
                result.error = f"HTTP {resp.status}"
                logger.warning("Recorder %d upload failed: HTTP %d", recorder_id, resp.status)

        result.cpu_percent, result.mem_mb = _sample_process_metrics(proc)

    except asyncio.CancelledError:
        result.error = "cancelled"
        raise
    except Exception as exc:
        result.error = str(exc)
        logger.error("Recorder %d error: %s", recorder_id, exc)

    return result


# ---------------------------------------------------------------------------
# Metrics aggregation
# ---------------------------------------------------------------------------


def compute_percentile(values: list[float], pct: float) -> float:
    """Compute percentile from a list of values using nearest-rank method."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    # Nearest-rank method: rank = ceil(pct/100 * n)
    rank = int(pct / 100.0 * n + 0.5)
    rank = max(1, min(rank, n))
    return round(sorted_vals[rank - 1], 2)


def aggregate_metrics(
    results: list[RecorderResult],
    total_duration_sec: float,
    backend_url: str,
) -> LoadTestMetrics:
    """Aggregate individual recorder results into summary metrics."""
    latencies = [r.latency_ms for r in results if r.success]
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    total_bytes = sum(r.upload_size_bytes for r in results)
    throughput = total_bytes / total_duration_sec if total_duration_sec > 0 else 0.0

    cpu_values = [r.cpu_percent for r in results if r.cpu_percent > 0]
    mem_values = [r.mem_mb for r in results if r.mem_mb > 0]

    metrics = LoadTestMetrics(
        total_recorders=len(results),
        successful=len(successful),
        failed=len(failed),
        error_rate=round(len(failed) / len(results) * 100, 2) if results else 0.0,
        p50_latency_ms=compute_percentile(latencies, 50),
        p95_latency_ms=compute_percentile(latencies, 95),
        p99_latency_ms=compute_percentile(latencies, 99),
        min_latency_ms=round(min(latencies), 2) if latencies else 0.0,
        max_latency_ms=round(max(latencies), 2) if latencies else 0.0,
        mean_latency_ms=round(statistics.mean(latencies), 2) if latencies else 0.0,
        median_latency_ms=round(statistics.median(latencies), 2) if latencies else 0.0,
        total_bytes_uploaded=total_bytes,
        throughput_bytes_per_sec=round(throughput, 2),
        total_duration_sec=round(total_duration_sec, 2),
        avg_cpu_percent=round(statistics.mean(cpu_values), 2) if cpu_values else 0.0,
        avg_mem_mb=round(statistics.mean(mem_values), 2) if mem_values else 0.0,
        max_cpu_percent=round(max(cpu_values), 2) if cpu_values else 0.0,
        max_mem_mb=round(max(mem_values), 2) if mem_values else 0.0,
        recorder_results=[asdict(r) for r in results],
        backend_url=backend_url,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    return metrics


# ---------------------------------------------------------------------------
# Output generation
# ---------------------------------------------------------------------------


def write_json_report(metrics: LoadTestMetrics, output_path: Path) -> None:
    """Write metrics to JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(asdict(metrics), f, indent=2, default=str)
    logger.info("JSON report written to %s", output_path)


def write_markdown_report(metrics: LoadTestMetrics, output_path: Path) -> None:
    """Write metrics to Markdown summary table."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Load Test Results",
        "",
        f"**Timestamp:** {metrics.timestamp}",
        f"**Backend:** {metrics.backend_url}",
        f"**Duration:** {metrics.total_duration_sec:.2f}s",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Recorders | {metrics.total_recorders} |",
        f"| Successful | {metrics.successful} |",
        f"| Failed | {metrics.failed} |",
        f"| Error Rate | {metrics.error_rate}% |",
        f"| p50 Latency | {metrics.p50_latency_ms}ms |",
        f"| p95 Latency | {metrics.p95_latency_ms}ms |",
        f"| p99 Latency | {metrics.p99_latency_ms}ms |",
        f"| Min Latency | {metrics.min_latency_ms}ms |",
        f"| Max Latency | {metrics.max_latency_ms}ms |",
        f"| Mean Latency | {metrics.mean_latency_ms}ms |",
        f"| Total Uploaded | {metrics.total_bytes_uploaded / 1024:.1f} KB |",
        f"| Throughput | {metrics.throughput_bytes_per_sec / 1024:.1f} KB/s |",
        f"| Avg CPU | {metrics.avg_cpu_percent}% |",
        f"| Avg Memory | {metrics.avg_mem_mb:.1f} MB |",
        "",
        "## Per-Recorder Results (first 20)",
        "",
        "| ID | Success | Latency (ms) | Size (bytes) | CPU% | Mem (MB) | Error |",
        "|----|---------|-------------|-------------|------|----------|-------|",
    ]

    for r in metrics.recorder_results[:20]:
        lines.append(
            f"| {r['recorder_id']} | {r['success']} | {r['latency_ms']} | "
            f"{r['upload_size_bytes']} | {r['cpu_percent']} | {r['mem_mb']} | "
            f"{r['error']} |"
        )

    if len(metrics.recorder_results) > 20:
        lines.append(f"\n*... and {len(metrics.recorder_results) - 20} more (see JSON)*")

    lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Markdown report written to %s", output_path)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def run_load_test(
    backend_url: str,
    num_recorders: int,
) -> LoadTestMetrics:
    """
    Run the full load test with N concurrent recorders.

    Args:
        backend_url: URL of the backend stub (e.g. http://localhost:8500)
        num_recorders: Number of concurrent recorder tasks

    Returns:
        Aggregated LoadTestMetrics
    """
    logger.info("Starting load test: %d recorders → %s", num_recorders, backend_url)

    # Verify backend is reachable
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{backend_url}/health") as resp:
                if resp.status != 200:
                    logger.error("Backend health check failed: HTTP %d", resp.status)
                    raise RuntimeError(f"Backend not healthy: HTTP {resp.status}")
                health = await resp.json()
                logger.info("Backend healthy: %s", health)
        except aiohttp.ClientError as exc:
            logger.error("Cannot connect to backend at %s: %s", backend_url, exc)
            raise RuntimeError(f"Backend unreachable: {exc}") from exc

    # Run all recorders concurrently
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        start = time.monotonic()

        tasks = [
            asyncio.create_task(run_recorder(i, backend_url, session)) for i in range(num_recorders)
        ]

        results: list[RecorderResult] = await asyncio.gather(*tasks, return_exceptions=False)

        total_duration = time.monotonic() - start

    # Aggregate and report
    metrics = aggregate_metrics(results, total_duration, backend_url)

    logger.info(
        "Load test complete: %d/%d success, p95=%.1fms, error_rate=%.1f%%, " "duration=%.1fs",
        metrics.successful,
        metrics.total_recorders,
        metrics.p95_latency_ms,
        metrics.error_rate,
        metrics.total_duration_sec,
    )

    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Concurrent load test for gamedata pipeline backend",
    )
    parser.add_argument(
        "--backend-url",
        required=True,
        help="Backend stub URL (e.g. http://localhost:8500)",
    )
    parser.add_argument(
        "-n",
        "--num-recorders",
        type=int,
        default=100,
        help="Number of concurrent recorder tasks (default: 100)",
    )
    parser.add_argument(
        "--output-dir",
        default="dashboard",
        help="Output directory for reports (default: dashboard)",
    )
    args = parser.parse_args(argv)

    # Resolve output dir relative to project root
    project_root = Path(__file__).resolve().parent.parent
    output_dir = project_root / args.output_dir

    try:
        metrics = asyncio.run(run_load_test(args.backend_url, args.num_recorders))

        # Write reports
        json_path = output_dir / "load_test_results.json"
        md_path = output_dir / "load_test_results.md"
        write_json_report(metrics, json_path)
        write_markdown_report(metrics, md_path)

        # Print summary
        print("\n" + "=" * 60)
        print("LOAD TEST RESULTS")
        print("=" * 60)
        print(f"  Recorders:      {metrics.total_recorders}")
        print(f"  Success:        {metrics.successful}")
        print(f"  Failed:         {metrics.failed}")
        print(f"  Error Rate:     {metrics.error_rate}%")
        print(f"  p50 Latency:    {metrics.p50_latency_ms}ms")
        print(f"  p95 Latency:    {metrics.p95_latency_ms}ms")
        print(f"  p99 Latency:    {metrics.p99_latency_ms}ms")
        print(f"  Duration:       {metrics.total_duration_sec:.2f}s")
        print(f"  Throughput:     {metrics.throughput_bytes_per_sec / 1024:.1f} KB/s")
        print("=" * 60)
        print(f"  JSON: {json_path}")
        print(f"  MD:   {md_path}")
        print("=" * 60 + "\n")

        return 0

    except Exception as exc:
        logger.error("Load test failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
