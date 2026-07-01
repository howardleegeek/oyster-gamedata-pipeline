#!/usr/bin/env python3
"""Tests for bin/autoresearch_throughput.py — capacity planner for the
autoresearch pipeline (50 / 200 / 1000 vendor scales)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the bin module is importable
BIN_DIR = Path(__file__).parent.parent.parent / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import autoresearch_throughput as throughput  # noqa: E402


class TestIdentifyBottleneck:
    """Tests for _identify_bottleneck() — pure heuristic bucketer."""

    def test_very_high_throughput_is_network_egress(self):
        assert throughput._identify_bottleneck(501) == "network_egress"
        assert throughput._identify_bottleneck(10_000) == "network_egress"

    def test_high_throughput_is_gpu_inference(self):
        assert throughput._identify_bottleneck(101) == "gpu_inference"
        assert throughput._identify_bottleneck(500) == "gpu_inference"

    def test_medium_throughput_is_cpu_preprocess(self):
        assert throughput._identify_bottleneck(21) == "cpu_preprocess"
        assert throughput._identify_bottleneck(100) == "cpu_preprocess"

    def test_low_throughput_is_disk_io(self):
        assert throughput._identify_bottleneck(0) == "disk_io"
        assert throughput._identify_bottleneck(20) == "disk_io"
        assert throughput._identify_bottleneck(0.0001) == "disk_io"


class TestComputePlan:
    """Tests for compute_plan() — pure capacity planner."""

    def test_default_vendor_scales(self):
        plan = throughput.compute_plan(clips_per_vendor=120, processing_hours=16.0)
        scales = [r.vendors for r in plan.results]
        assert scales == [50, 200, 1000]
        assert plan.clips_per_vendor == 120
        assert plan.processing_hours == 16.0

    def test_aggregate_arithmetic(self):
        # 100 vendors * 10 clips = 1000 clips in 2 hours → 500 clips/hr,
        # 500 / 7200 ≈ 0.1389 clips/sec, 7200 / 1000 = 7.2 sec/clip.
        plan = throughput.compute_plan(
            clips_per_vendor=10,
            processing_hours=2.0,
            vendor_scales=[100],
        )
        r = plan.results[0]
        assert r.vendors == 100
        assert r.clips_per_vendor == 10
        assert r.total_clips == 1000
        assert r.processing_hours == 2.0
        assert r.clips_per_hour == pytest.approx(500.0)
        assert r.clips_per_second == pytest.approx(1000.0 / 7200.0)
        assert r.est_processing_sec_per_clip == pytest.approx(7.2)
        # 1000 clips / 7200s ≈ 0.139 cps → falls into "disk_io" bucket.
        assert r.bottleneck == "disk_io"

    def test_zero_processing_hours_does_not_divide_by_zero(self):
        # Should not raise; should produce zero-derived fields and use
        # the disk_io bucket since cps == 0.
        plan = throughput.compute_plan(
            clips_per_vendor=10,
            processing_hours=0.0,
            vendor_scales=[50],
        )
        r = plan.results[0]
        assert r.clips_per_hour == 0.0
        assert r.clips_per_second == 0.0
        assert r.est_processing_sec_per_clip == 0.0
        assert r.bottleneck == "disk_io"

    def test_zero_clips_does_not_divide_by_zero(self):
        # zero clips_per_vendor → total_clips = 0; cph/cps guards trip
        # (0/16 == 0), but sec/clip must be guarded to avoid 0/0.
        plan = throughput.compute_plan(
            clips_per_vendor=0,
            processing_hours=16.0,
            vendor_scales=[200],
        )
        r = plan.results[0]
        assert r.total_clips == 0
        assert r.clips_per_hour == 0.0
        assert r.clips_per_second == 0.0
        assert r.est_processing_sec_per_clip == 0.0
        assert r.bottleneck == "disk_io"

    def test_high_throughput_bucket_appears(self):
        # 1000 vendors * 10000 clips = 10M clips in 1 hour = 2777.78 cps
        # → network_egress bucket.
        plan = throughput.compute_plan(
            clips_per_vendor=10_000,
            processing_hours=1.0,
            vendor_scales=[1000],
        )
        assert plan.results[0].bottleneck == "network_egress"

    def test_results_are_independent_dataclass_instances(self):
        plan = throughput.compute_plan(
            clips_per_vendor=5,
            processing_hours=4.0,
            vendor_scales=[10, 20],
        )
        assert len(plan.results) == 2
        assert plan.results[0].vendors == 10
        assert plan.results[1].vendors == 20
        # ThroughputResult is a frozen dataclass — equality is structural.
        r1 = throughput.compute_plan(5, 4.0, [10]).results[0]
        r2 = throughput.compute_plan(5, 4.0, [10]).results[0]
        assert r1 == r2


class TestCapacityPlanJson:
    """Tests for CapacityPlan.to_json() — round-trip JSON serialisation."""

    def test_to_json_is_valid_json(self):
        plan = throughput.compute_plan(10, 2.0, [50])
        raw = plan.to_json()
        parsed = json.loads(raw)
        assert parsed["clips_per_vendor"] == 10
        assert parsed["processing_hours"] == 2.0
        assert isinstance(parsed["results"], list)
        assert len(parsed["results"]) == 1
        r0 = parsed["results"][0]
        assert r0["vendors"] == 50
        assert r0["clips_per_vendor"] == 10
        assert r0["total_clips"] == 500

    def test_to_json_indent_is_2(self):
        # Indent=2 means the second line starts with two spaces.
        plan = throughput.compute_plan(10, 2.0, [50])
        lines = plan.to_json().splitlines()
        assert lines[1].startswith("  ")
        assert lines[0] == "{"


class TestCapacityPlanSummaryTable:
    """Tests for CapacityPlan.summary_table() — ASCII table rendering."""

    def test_table_contains_all_vendor_scales(self):
        plan = throughput.compute_plan(10, 2.0, [50, 200, 1000])
        table = plan.summary_table()
        # Header + 1 separator + 3 data rows
        assert table.count("\n") == 4
        assert "Vendors" in table
        assert "Bottleneck" in table
        # Bottleneck column populated for every scale
        assert table.count("disk_io") == 3

    def test_table_header_separator(self):
        plan = throughput.compute_plan(10, 2.0, [50])
        lines = plan.summary_table().splitlines()
        # Second line is the dashed separator matching header width.
        assert lines[1].startswith("---")
        assert len(lines[1]) == len(lines[0])


class TestCapacityPlanWriteCsv:
    """Tests for CapacityPlan.write_csv() — CSV output."""

    def test_write_csv_header_and_rows(self, tmp_path: Path):
        plan = throughput.compute_plan(10, 2.0, [50, 200])
        csv_path = tmp_path / "plan.csv"
        plan.write_csv(csv_path)
        contents = csv_path.read_text().splitlines()
        # Header + 2 data rows
        assert len(contents) == 3
        assert contents[0] == (
            "vendors,clips_per_vendor,total_clips,clips_per_hour,"
            "clips_per_second,est_processing_sec_per_clip,bottleneck"
        )
        assert contents[1].startswith("50,10,500,")
        assert contents[2].startswith("200,10,2000,")

    def test_write_csv_bottleneck_column(self, tmp_path: Path):
        plan = throughput.compute_plan(10, 2.0, [50])
        csv_path = tmp_path / "plan.csv"
        plan.write_csv(csv_path)
        # The bottleneck column is the last field in each row.
        assert csv_path.read_text().splitlines()[1].endswith("disk_io")


class TestBuildParser:
    """Tests for _build_parser() — CLI argument shape."""

    def test_default_values(self):
        args = throughput._build_parser().parse_args([])
        assert args.clips_per_vendor == 120
        assert args.hours == 16.0
        assert args.vendor_scales == [50, 200, 1000]
        assert args.output_format == "table"
        assert args.output is None

    def test_overrides(self):
        args = throughput._build_parser().parse_args(
            ["--clips-per-vendor", "5", "--hours", "1", "--format", "json"]
        )
        assert args.clips_per_vendor == 5
        assert args.hours == 1.0
        assert args.output_format == "json"


class TestMain:
    """Tests for main() — CLI entrypoint behaviour."""

    def test_table_output_exits_zero(self, capsys):
        rc = throughput.main(["--clips-per-vendor", "10", "--hours", "2"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Vendors" in captured.out
        assert "Bottleneck" in captured.out

    def test_json_output_exits_zero_and_is_valid_json(self, capsys):
        rc = throughput.main(
            [
                "--clips-per-vendor",
                "10",
                "--hours",
                "2",
                "--format",
                "json",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        parsed = json.loads(captured.out)
        assert parsed["clips_per_vendor"] == 10
        assert len(parsed["results"]) == 3  # default vendor_scales

    def test_csv_output_to_path(self, tmp_path: Path, capsys):
        out_path = tmp_path / "plan.csv"
        rc = throughput.main(
            [
                "--clips-per-vendor",
                "10",
                "--hours",
                "2",
                "--vendor-scales",
                "50",
                "--format",
                "csv",
                "--output",
                str(out_path),
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert out_path.exists()
        assert f"CSV written to {out_path}" in captured.out
        first_line = out_path.read_text().splitlines()[0]
        assert first_line.startswith("vendors,clips_per_vendor,total_clips,")

    def test_csv_output_without_path_uses_tempdir(self, capsys):
        rc = throughput.main(
            [
                "--clips-per-vendor",
                "10",
                "--hours",
                "2",
                "--vendor-scales",
                "50",
                "--format",
                "csv",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        # Should report a tempdir path
        assert captured.out.startswith("CSV written to ")
        assert "autoresearch_throughput_" in captured.out

    def test_text_output_with_output_path_writes_file(self, tmp_path: Path, capsys):
        out_path = tmp_path / "plan.txt"
        rc = throughput.main(
            [
                "--clips-per-vendor",
                "10",
                "--hours",
                "2",
                "--vendor-scales",
                "50",
                "--output",
                str(out_path),
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert out_path.exists()
        assert "Vendors" in out_path.read_text()
        assert f"Output written to {out_path}" in captured.out
