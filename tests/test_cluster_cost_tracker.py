"""Tests for daemon/cluster_cost_tracker.py with mock log files."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from daemon.cluster_cost_tracker import (
    TOKENS_PER_TURN,
    USD_PER_M_TOKEN,
    build_report,
    parse_dispatch_log,
    scan_clusters,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_cluster_dir(tmp_path):
    """Create a temporary cluster directory with a dispatch.log."""
    cluster = tmp_path / "cluster-test-001"
    cluster.mkdir()
    return cluster


def _write_dispatch_log(cluster_dir: Path, content: str) -> Path:
    log_path = cluster_dir / "dispatch.log"
    log_path.write_text(content, encoding="utf-8")
    return log_path


# ── parse_dispatch_log tests ──────────────────────────────────────────────


class TestParseDispatchLog:
    def test_basic_log(self, mock_cluster_dir):
        content = (
            "[aliyun-token-plan] active, will rotate 4 models\n"
            "[S05] Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp/cluster-test)\n"
            "[turn 0] assistant: Hello\n"
            "[turn 1] tool write_file -> OK\n"
            "[turn 2] assistant: Done\n"
            "[S05] TASK RESULT: completed after 3 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result is not None
        assert result["spec"] == "S05"
        assert result["model"] == "qwen3.6-plus"
        assert result["turns"] == 3
        assert result["retries"] == 0
        assert result["estimated_tokens"] == 3 * TOKENS_PER_TURN
        assert result["estimated_usd"] == round(
            3 * TOKENS_PER_TURN * USD_PER_M_TOKEN / 1_000_000, 4
        )

    def test_log_with_429_retries(self, mock_cluster_dir):
        content = (
            "[aliyun-token-plan] active\n"
            "[S10] Starting MiniMax agent (model=glm-5, wd=/tmp/cluster-test)\n"
            "[turn 0] assistant: Starting\n"
            "[S10] HTTP 429 (retry 1/5, sleep 60s): quota exceeded\n"
            "[S10] HTTP 429 (retry 2/5, sleep 60s): quota exceeded\n"
            "[turn 1] assistant: Retrying\n"
            "[S10] TASK RESULT: completed after 2 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result is not None
        assert result["retries"] == 2
        assert result["turns"] == 2

    def test_log_no_task_result(self, mock_cluster_dir):
        """Log without TASK RESULT should still parse with turns=0."""
        content = (
            "[aliyun-token-plan] active\n"
            "[S99] Starting MiniMax agent (model=deepseek-v3.2, wd=/tmp/cluster-test)\n"
            "[turn 0] assistant: Starting\n"
            "[turn 1] assistant: Still going\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result is not None
        assert result["turns"] == 0
        assert result["spec"] == "S99"
        assert result["model"] == "deepseek-v3.2"

    def test_log_no_model_header(self, mock_cluster_dir):
        """Log without model header should return None."""
        content = "[turn 0] assistant: Hello\n[turn 1] assistant: Done\n"
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result is None

    def test_log_empty_file(self, mock_cluster_dir):
        log_path = _write_dispatch_log(mock_cluster_dir, "")
        result = parse_dispatch_log(str(log_path))
        assert result is None

    def test_log_nonexistent_file(self):
        result = parse_dispatch_log("/nonexistent/path/dispatch.log")
        assert result is None

    def test_large_turn_count(self, mock_cluster_dir):
        content = (
            "[aliyun-token-plan] active\n"
            "[S40] Starting MiniMax agent (model=MiniMax-M2.5, wd=/tmp/cluster-test)\n"
            "[S40] TASK RESULT: completed after 40 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result["turns"] == 40
        assert result["estimated_tokens"] == 40 * TOKENS_PER_TURN

    def test_spec_id_fallback(self, mock_cluster_dir):
        """If spec_id can't be extracted from header, fall back to dir name."""
        content = (
            "[aliyun-token-plan] active\n"
            "Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp/cluster-test)\n"
            "TASK RESULT: completed after 5 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result is not None
        assert result["spec"] == mock_cluster_dir.name
        assert result["model"] == "qwen3.6-plus"

    def test_multiple_model_lines_uses_first(self, mock_cluster_dir):
        """If model appears multiple times, use the first one."""
        content = (
            "[aliyun-token-plan] active\n"
            "[S01] Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp/cluster-test)\n"
            "[turn 0] assistant: Starting\n"
            "[S01] Starting MiniMax agent (model=glm-5, wd=/tmp/cluster-test)\n"
            "[S01] TASK RESULT: completed after 10 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result["model"] == "qwen3.6-plus"

    def test_wall_s_non_negative(self, mock_cluster_dir):
        content = (
            "[aliyun-token-plan] active\n"
            "[S01] Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp/cluster-test)\n"
            "[S01] TASK RESULT: completed after 5 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result["wall_s"] >= 0


# ── scan_clusters tests ───────────────────────────────────────────────────


class TestScanClusters:
    def test_scan_multiple_clusters(self, tmp_path):
        """Scan multiple cluster directories."""
        for i in range(3):
            cluster = tmp_path / f"cluster-test-{i:03d}"
            cluster.mkdir()
            log = cluster / "dispatch.log"
            log.write_text(
                f"[aliyun-token-plan] active\n"
                f"[S{i:02d}] Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp)\n"
                f"[S{i:02d}] TASK RESULT: completed after {10 + i} turns\n",
                encoding="utf-8",
            )

        pattern = str(tmp_path / "cluster-test-*" / "dispatch.log")
        results = scan_clusters(pattern)

        assert len(results) == 3
        specs = [r["spec"] for r in results]
        assert specs == ["S00", "S01", "S02"]

    def test_scan_empty_glob(self, tmp_path):
        results = scan_clusters(str(tmp_path / "nonexistent-*" / "dispatch.log"))
        assert results == []

    def test_scan_sorted_by_spec(self, tmp_path):
        """Results should be sorted by spec name."""
        for name in ["S10", "S01", "S05"]:
            cluster = tmp_path / f"cluster-{name}"
            cluster.mkdir()
            log = cluster / "dispatch.log"
            log.write_text(
                f"[aliyun-token-plan] active\n"
                f"[{name}] Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp)\n"
                f"[{name}] TASK RESULT: completed after 5 turns\n",
                encoding="utf-8",
            )

        pattern = str(tmp_path / "cluster-*" / "dispatch.log")
        results = scan_clusters(pattern)

        specs = [r["spec"] for r in results]
        assert specs == sorted(specs)


# ── build_report tests ────────────────────────────────────────────────────


class TestBuildReport:
    def test_empty_per_spec(self):
        report = build_report([])
        assert report["totals"]["specs"] == 0
        assert report["totals"]["turns"] == 0
        assert report["totals"]["retries"] == 0
        assert report["totals"]["wall_s"] == 0
        assert report["totals"]["estimated_usd"] == 0
        assert "updated_at" in report

    def test_single_entry(self):
        entry = {
            "spec": "S05",
            "model": "qwen3.6-plus",
            "turns": 40,
            "retries": 0,
            "wall_s": 312,
            "estimated_tokens": 32000,
            "estimated_usd": 0.096,
        }
        report = build_report([entry])

        assert report["totals"]["specs"] == 1
        assert report["totals"]["turns"] == 40
        assert report["totals"]["retries"] == 0
        assert report["totals"]["wall_s"] == 312
        assert report["totals"]["estimated_usd"] == 0.096
        assert len(report["per_spec"]) == 1

    def test_multiple_entries_totals(self):
        entries = [
            {
                "spec": "S01",
                "model": "qwen3.6-plus",
                "turns": 10,
                "retries": 1,
                "wall_s": 100,
                "estimated_tokens": 8000,
                "estimated_usd": 0.024,
            },
            {
                "spec": "S02",
                "model": "glm-5",
                "turns": 20,
                "retries": 2,
                "wall_s": 200,
                "estimated_tokens": 16000,
                "estimated_usd": 0.048,
            },
        ]
        report = build_report(entries)

        assert report["totals"]["specs"] == 2
        assert report["totals"]["turns"] == 30
        assert report["totals"]["retries"] == 3
        assert report["totals"]["wall_s"] == 300
        assert report["totals"]["estimated_usd"] == 0.072

    def test_updated_at_is_iso8601(self):
        report = build_report([])
        # Should be parseable as ISO8601
        from datetime import datetime

        datetime.fromisoformat(report["updated_at"])


# ── CLI integration tests ─────────────────────────────────────────────────


class TestCLI:
    def test_dry_run_outputs_json(self, tmp_path, capsys):
        cluster = tmp_path / "cluster-test-001"
        cluster.mkdir()
        log = cluster / "dispatch.log"
        log.write_text(
            "[aliyun-token-plan] active\n"
            "[S05] Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp)\n"
            "[S05] TASK RESULT: completed after 5 turns\n",
            encoding="utf-8",
        )

        pattern = str(tmp_path / "cluster-test-*" / "dispatch.log")
        output = str(tmp_path / "output.json")

        from daemon.cluster_cost_tracker import main

        with (
            patch.object(
                __import__("daemon.cluster_cost_tracker", fromlist=["CLUSTER_GLOB"]),
                "CLUSTER_GLOB",
                pattern,
            ),
            patch(
                "sys.argv",
                [
                    "cluster_cost_tracker.py",
                    "--once",
                    "--dry-run",
                    "--glob",
                    pattern,
                    "--output",
                    output,
                ],
            ),
        ):
            main()

        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert "per_spec" in report
        assert "totals" in report
        assert report["totals"]["specs"] == 1

    def test_once_writes_file(self, tmp_path):
        cluster = tmp_path / "cluster-test-001"
        cluster.mkdir()
        log = cluster / "dispatch.log"
        log.write_text(
            "[aliyun-token-plan] active\n"
            "[S05] Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp)\n"
            "[S05] TASK RESULT: completed after 5 turns\n",
            encoding="utf-8",
        )

        pattern = str(tmp_path / "cluster-test-*" / "dispatch.log")
        output = str(tmp_path / "output.json")

        from daemon.cluster_cost_tracker import main

        with patch(
            "sys.argv",
            [
                "cluster_cost_tracker.py",
                "--once",
                "--glob",
                pattern,
                "--output",
                output,
            ],
        ):
            main()

        assert os.path.exists(output)
        with open(output) as f:
            report = json.load(f)
        assert report["totals"]["specs"] == 1

    def test_idempotent(self, tmp_path):
        """Running twice produces same results (except updated_at)."""
        cluster = tmp_path / "cluster-test-001"
        cluster.mkdir()
        log = cluster / "dispatch.log"
        log.write_text(
            "[aliyun-token-plan] active\n"
            "[S05] Starting MiniMax agent (model=qwen3.6-plus, wd=/tmp)\n"
            "[S05] TASK RESULT: completed after 5 turns\n",
            encoding="utf-8",
        )

        pattern = str(tmp_path / "cluster-test-*" / "dispatch.log")
        output = str(tmp_path / "output.json")

        from daemon.cluster_cost_tracker import main

        for _ in range(2):
            with patch(
                "sys.argv",
                [
                    "cluster_cost_tracker.py",
                    "--once",
                    "--glob",
                    pattern,
                    "--output",
                    output,
                ],
            ):
                main()

        with open(output) as f:
            report = json.load(f)
        assert report["totals"]["specs"] == 1
        assert report["totals"]["turns"] == 5


# ── Real log format tests ─────────────────────────────────────────────────


class TestRealLogFormat:
    """Test against patterns found in real dispatch logs."""

    def test_real_batch10_format(self, mock_cluster_dir):
        content = (
            "[aliyun-token-plan] active, will rotate 4 models\n"
            "[batch10] Starting MiniMax agent (model=glm-5, wd=/private/tmp/cluster-2026-05-17-batch10)\n"
            "[turn 0] assistant: I'll implement this spec fully.\n"
            "[turn 0] tool run_cmd(['cmd']) -> exit=0\n"
            "\n"
            "[turn 1] tool write_file(['path', 'content']) -> ERROR: path escapes WORKING_DIR\n"
            "[turn 24] assistant: Done\n"
            "[batch10] TASK RESULT: completed after 24 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result is not None
        assert result["spec"] == "batch10"
        assert result["model"] == "glm-5"
        assert result["turns"] == 24

    def test_real_429_format(self, mock_cluster_dir):
        content = (
            "[aliyun-token-plan] active\n"
            "[deploy07] Starting MiniMax agent (model=deepseek-v3.2, wd=/tmp/cluster-test)\n"
            "[turn 0] assistant: Starting\n"
            '[deploy07] HTTP 429 (retry 1/5, sleep 60s): {"error":{"message":"Allocated quota exceeded"}}\n'
            "[deploy07] TASK RESULT: completed after 33 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result["retries"] == 1
        assert result["turns"] == 33

    def test_real_e2e_orchestrator_format(self, mock_cluster_dir):
        """Test spec_id with hyphens like e2e-orchestrator."""
        content = (
            "[aliyun-token-plan] active\n"
            "[e2e-orchestrator] Starting MiniMax agent (model=MiniMax-M2.5, wd=/tmp/cluster-test)\n"
            "[turn 0] assistant: Starting\n"
            "[e2e-orchestrator] TASK RESULT: completed after 24 turns\n"
        )
        log_path = _write_dispatch_log(mock_cluster_dir, content)
        result = parse_dispatch_log(str(log_path))

        assert result["spec"] == "e2e-orchestrator"
        assert result["model"] == "MiniMax-M2.5"
        assert result["turns"] == 24
