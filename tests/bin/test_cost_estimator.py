#!/usr/bin/env python3
"""Tests for bin/cost_estimator.py — Daily S3 cost report.

Covers:
- calculate_storage_cost (all 6 storage classes + unknown fallback)
- calculate_egress_cost (tiered pricing, 0, negative, > max tier)
- project_lifecycle_stage (boundary days 30/90/180 + current==recommended)
- generate_cost_report (composite output structure)
- print_report (text + JSON output, called with capsys)
- parse_args (defaults, overrides, choices enforcement)
- main() CLI entry point (text + JSON exit code 0, missing required arg exits 2)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.cost_estimator import (  # noqa: E402
    LIFECYCLE_THRESHOLDS,
    STORAGE_PRICING,
    StorageClass,
    StorageMetrics,
    calculate_egress_cost,
    calculate_storage_cost,
    generate_cost_report,
    main,
    parse_args,
    print_report,
    project_lifecycle_stage,
)

# ---------------------------------------------------------------------------
# calculate_storage_cost
# ---------------------------------------------------------------------------


class TestCalculateStorageCost:
    """Tests for the pure cost calculation function."""

    def test_standard_class(self):
        """STANDARD @ $0.023/GB-month × size."""
        assert calculate_storage_cost(100.0, StorageClass.STANDARD) == pytest.approx(2.30)
        assert calculate_storage_cost(0.0, StorageClass.STANDARD) == 0.0

    def test_all_classes_have_pricing(self):
        """Every StorageClass enum member has an entry in STORAGE_PRICING."""
        for sc in StorageClass:
            assert sc in STORAGE_PRICING, f"Missing pricing for {sc}"
            assert STORAGE_PRICING[sc] > 0, f"Non-positive price for {sc}"

    def test_all_classes_multiply(self):
        """Sanity: every class returns size * price."""
        size = 50.0
        for sc in StorageClass:
            expected = size * STORAGE_PRICING[sc]
            assert calculate_storage_cost(size, sc) == pytest.approx(expected)

    def test_unknown_storage_class_falls_back_to_standard(self):
        """Unknown enum member falls back to STANDARD price (no exception)."""

        class _Unknown:
            pass

        fallback = STORAGE_PRICING[StorageClass.STANDARD]
        # Passing a non-enum object: dict.get returns None, the function
        # then uses STORAGE_PRICING[STANDARD] as the default.
        result = calculate_storage_cost(10.0, _Unknown())  # type: ignore[arg-type]
        assert result == pytest.approx(10.0 * fallback)

    def test_deep_archive_cheapest(self):
        """DEEP_ARCHIVE is the lowest cost storage class."""
        size = 1000.0
        costs = {sc: calculate_storage_cost(size, sc) for sc in StorageClass}
        cheapest = min(costs.values())
        assert costs[StorageClass.DEEP_ARCHIVE] == cheapest


# ---------------------------------------------------------------------------
# calculate_egress_cost
# ---------------------------------------------------------------------------


class TestCalculateEgressCost:
    """Tests for the tiered egress pricing function."""

    def test_zero_egress(self):
        """0 GB egress -> 0 cost (no tiers applied)."""
        assert calculate_egress_cost(0) == 0.0

    def test_negative_egress_treated_as_zero(self):
        """Negative egress is clamped to 0 (no refund, no exception)."""
        assert calculate_egress_cost(-10) == 0.0

    def test_first_tier_free(self):
        """Up to 1 GB is free."""
        # The first tier is (1, 0.00), so 0.5 GB is entirely free.
        assert calculate_egress_cost(0.5) == 0.0
        # Exactly 1 GB boundary: still within first tier.
        assert calculate_egress_cost(1.0) == 0.0

    def test_second_tier_pricing(self):
        """From 1 -> 10 GB the price is $0.09/GB."""
        # 5 GB: 1 GB free + 4 GB × $0.09 = $0.36
        assert calculate_egress_cost(5.0) == pytest.approx(0.36)
        # 10 GB: 1 GB free + 9 GB × $0.09 = $0.81
        assert calculate_egress_cost(10.0) == pytest.approx(0.81)

    def test_third_tier_pricing(self):
        """From 10 -> 50 GB the price is $0.085/GB."""
        # 50 GB: 1 free + 9 × 0.09 + 40 × 0.085 = 0.81 + 3.40 = 4.21
        assert calculate_egress_cost(50.0) == pytest.approx(4.21)

    def test_fourth_tier_pricing(self):
        """From 50 -> 150 GB the price is $0.07/GB."""
        # 100 GB: 1 free + 9 × 0.09 + 40 × 0.085 + 50 × 0.07
        # = 0 + 0.81 + 3.40 + 3.50 = 7.71
        assert calculate_egress_cost(100.0) == pytest.approx(7.71)

    def test_top_tier_pricing(self):
        """Above 150 GB the price is $0.05/GB."""
        # 200 GB: 0 free + 9*0.09 + 40*0.085 + 100*0.07 + 50*0.05
        # = 0 + 0.81 + 3.40 + 7.00 + 2.50 = 13.71
        assert calculate_egress_cost(200.0) == pytest.approx(13.71)

    def test_very_large_egress_no_overflow(self):
        """1 TB egress converges to the correct sum across all tiers."""
        # 1024 GB: 1 free + 9*0.09 + 40*0.085 + 100*0.07 + 874*0.05
        # = 0 + 0.81 + 3.40 + 7.00 + 43.70 = 54.91
        assert calculate_egress_cost(1024.0) == pytest.approx(54.91)

    def test_egress_is_monotonic(self):
        """Cost must be non-decreasing as egress grows."""
        prev = -1.0
        for gb in (0, 1, 5, 10, 50, 100, 150, 200, 1000):
            cost = calculate_egress_cost(gb)
            assert cost >= prev, f"Cost dropped at {gb} GB: {cost} < {prev}"
            prev = cost


# ---------------------------------------------------------------------------
# project_lifecycle_stage
# ---------------------------------------------------------------------------


class TestProjectLifecycleStage:
    """Tests for the lifecycle policy recommendation function."""

    def test_age_zero_keeps_current(self):
        """Age 0 days: no lifecycle tier applies, current class retained."""
        m = StorageMetrics(
            bucket_name="b",
            size_gb=10.0,
            object_count=100,
            storage_class=StorageClass.STANDARD,
            avg_object_age_days=0,
        )
        out = project_lifecycle_stage(m)
        assert out["current_class"] == "STANDARD"
        assert out["recommended_class"] == "STANDARD"
        assert out["monthly_savings_usd"] == 0.0

    def test_age_just_above_30_recommends_ia(self):
        """Age > 30 days triggers STANDARD_IA."""
        m = StorageMetrics(
            bucket_name="b",
            size_gb=10.0,
            object_count=100,
            storage_class=StorageClass.STANDARD,
            avg_object_age_days=31,
        )
        out = project_lifecycle_stage(m)
        assert out["recommended_class"] == "STANDARD_IA"
        assert out["monthly_savings_usd"] > 0

    def test_age_just_above_90_recommends_glacier(self):
        """Age > 90 days triggers GLACIER."""
        m = StorageMetrics(
            bucket_name="b",
            size_gb=10.0,
            object_count=100,
            storage_class=StorageClass.STANDARD,
            avg_object_age_days=91,
        )
        out = project_lifecycle_stage(m)
        assert out["recommended_class"] == "GLACIER"

    def test_age_above_180_recommends_deep_archive(self):
        """Age > 180 days triggers DEEP_ARCHIVE."""
        m = StorageMetrics(
            bucket_name="b",
            size_gb=10.0,
            object_count=100,
            storage_class=StorageClass.STANDARD,
            avg_object_age_days=200,
        )
        out = project_lifecycle_stage(m)
        assert out["recommended_class"] == "DEEP_ARCHIVE"
        # 10 GB: STANDARD = 0.23, DEEP_ARCHIVE = 0.0099 -> savings 0.2201
        assert out["monthly_savings_usd"] == pytest.approx(0.2201)

    def test_age_exactly_at_threshold_does_not_trigger(self):
        """The policy uses strictly > threshold (30, 90, 180 are inclusive)."""
        m = StorageMetrics(
            bucket_name="b",
            size_gb=10.0,
            object_count=100,
            storage_class=StorageClass.STANDARD,
            avg_object_age_days=30,
        )
        out = project_lifecycle_stage(m)
        # 30 == threshold, so 30 > 30 is False -> STANDARD remains.
        assert out["recommended_class"] == "STANDARD"

    def test_already_in_cheap_class_no_downgrade(self):
        """If current class is already cheaper than the recommendation, keep it."""
        m = StorageMetrics(
            bucket_name="b",
            size_gb=10.0,
            object_count=100,
            storage_class=StorageClass.GLACIER,
            avg_object_age_days=5,
        )
        out = project_lifecycle_stage(m)
        # Age 5: nothing recommends a change. Current is GLACIER.
        assert out["recommended_class"] == "GLACIER"
        assert out["monthly_savings_usd"] == 0.0

    def test_lifecycle_thresholds_sorted(self):
        """Sanity: thresholds are processed in ascending order of days."""
        keys = list(LIFECYCLE_THRESHOLDS.keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# generate_cost_report
# ---------------------------------------------------------------------------


class TestGenerateCostReport:
    """Tests for the composite report builder."""

    def test_report_keys(self):
        """Top-level keys are stable: report_date, bucket, metrics, cost_breakdown, lifecycle_projection."""
        m = StorageMetrics(
            bucket_name="my-bucket",
            size_gb=10.0,
            object_count=42,
            storage_class=StorageClass.STANDARD,
            egress_gb=2.0,
            avg_object_age_days=15,
        )
        report = generate_cost_report(m)
        assert set(report.keys()) == {
            "report_date",
            "bucket",
            "metrics",
            "cost_breakdown",
            "lifecycle_projection",
        }
        assert report["bucket"] == "my-bucket"

    def test_report_metrics_round_trip(self):
        """Metrics in the report match the input StorageMetrics (with string enum)."""
        m = StorageMetrics(
            bucket_name="b",
            size_gb=7.5,
            object_count=999,
            storage_class=StorageClass.STANDARD_IA,
            egress_gb=12.0,
            avg_object_age_days=45,
        )
        report = generate_cost_report(m)
        rm = report["metrics"]
        assert rm["size_gb"] == 7.5
        assert rm["object_count"] == 999
        assert rm["storage_class"] == "STANDARD_IA"
        assert rm["egress_gb"] == 12.0
        assert rm["avg_object_age_days"] == 45

    def test_cost_breakdown_sums_correctly(self):
        """Total = storage + egress (no rounding-induced drift)."""
        m = StorageMetrics(
            bucket_name="b",
            size_gb=100.0,
            object_count=1,
            storage_class=StorageClass.STANDARD,
            egress_gb=50.0,
        )
        cb = generate_cost_report(m)["cost_breakdown"]
        assert cb["total_monthly_usd"] == pytest.approx(
            cb["storage_monthly_usd"] + cb["egress_monthly_usd"]
        )

    def test_report_date_is_iso_utc(self):
        """report_date is a UTC ISO-8601 string with Z suffix."""
        m = StorageMetrics("b", 1.0, 1)
        date = generate_cost_report(m)["report_date"]
        assert isinstance(date, str)
        assert date.endswith("Z")
        # Should be parseable as ISO-8601 (datetime.fromisoformat strips Z in <3.11
        # but in 3.11+ accepts Z directly; assert the leading Y-M-D shape).
        assert date[4] == "-"
        assert date[7] == "-"
        assert "T" in date

    def test_zero_egress_report(self):
        """Zero egress still produces a numeric (not None) egress cost."""
        m = StorageMetrics("b", 10.0, 1, egress_gb=0.0)
        cb = generate_cost_report(m)["cost_breakdown"]
        assert cb["egress_monthly_usd"] == 0.0
        assert cb["total_monthly_usd"] == pytest.approx(cb["storage_monthly_usd"])


# ---------------------------------------------------------------------------
# print_report
# ---------------------------------------------------------------------------


class TestPrintReport:
    """Tests for print_report text + JSON output modes."""

    def _sample_report(self) -> dict:
        m = StorageMetrics(
            bucket_name="sample-bucket",
            size_gb=12.34,
            object_count=567,
            storage_class=StorageClass.STANDARD,
            egress_gb=3.21,
            avg_object_age_days=10,
        )
        return generate_cost_report(m)

    def test_json_format_writes_valid_json(self, capsys):
        """JSON mode writes a parseable JSON object to stdout."""
        report = self._sample_report()
        print_report(report, output_format="json")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == report

    def test_text_format_contains_bucket_name(self, capsys):
        """Text mode prints the bucket name and dollar values."""
        report = self._sample_report()
        print_report(report, output_format="text")
        captured = capsys.readouterr()
        assert "sample-bucket" in captured.out
        assert "$" in captured.out
        assert "S3 Cost Report" in captured.out

    def test_text_format_includes_lifecycle_section(self, capsys):
        """Text mode shows the lifecycle projection section."""
        report = self._sample_report()
        print_report(report, output_format="text")
        captured = capsys.readouterr()
        assert "Lifecycle Projection" in captured.out
        assert "Recommended Class" in captured.out

    def test_default_format_is_text(self, capsys):
        """Default output_format is 'text' (not 'json')."""
        report = self._sample_report()
        print_report(report)  # no format arg
        captured = capsys.readouterr()
        # Text mode is not valid JSON on its own (has prose)
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.out)
        assert "S3 Cost Report" in captured.out

    def test_unknown_format_falls_through_to_text(self, capsys):
        """Unknown output_format is silently treated as text (no exception)."""
        report = self._sample_report()
        print_report(report, output_format="yaml")  # type: ignore[arg-type]
        captured = capsys.readouterr()
        assert "S3 Cost Report" in captured.out


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Tests for the argparse wrapper."""

    def test_required_args_minimum(self):
        """--bucket, --size-gb, --objects are required."""
        args = parse_args(["--bucket", "b", "--size-gb", "1.0", "--objects", "10"])
        assert args.bucket == "b"
        assert args.size_gb == 1.0
        assert args.objects == 10

    def test_defaults(self):
        """Optional args have sensible defaults."""
        args = parse_args(["--bucket", "b", "--size-gb", "1.0", "--objects", "10"])
        assert args.storage_class == "STANDARD"
        assert args.egress_gb == 0.0
        assert args.avg_age_days == 0
        assert args.format == "text"

    def test_storage_class_choices_enforced(self):
        """Invalid --storage-class is rejected by argparse (SystemExit)."""
        with pytest.raises(SystemExit):
            parse_args(
                [
                    "--bucket",
                    "b",
                    "--size-gb",
                    "1.0",
                    "--objects",
                    "10",
                    "--storage-class",
                    "NOT_A_CLASS",
                ]
            )

    def test_format_choices_enforced(self):
        """Invalid --format is rejected by argparse (SystemExit)."""
        with pytest.raises(SystemExit):
            parse_args(
                [
                    "--bucket",
                    "b",
                    "--size-gb",
                    "1.0",
                    "--objects",
                    "10",
                    "--format",
                    "xml",
                ]
            )

    def test_numeric_args_are_coerced(self):
        """--size-gb and --egress-gb come back as float; --objects as int."""
        args = parse_args(
            [
                "--bucket",
                "b",
                "--size-gb",
                "1.5",
                "--objects",
                "20",
                "--egress-gb",
                "0.5",
                "--avg-age-days",
                "7",
            ]
        )
        assert isinstance(args.size_gb, float)
        assert isinstance(args.egress_gb, float)
        assert isinstance(args.objects, int)
        assert isinstance(args.avg_age_days, int)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() CLI entry point."""

    def test_main_text_exit_zero(self, capsys):
        """main() returns 0 and prints a text report for valid args."""
        rc = main(
            [
                "--bucket",
                "b",
                "--size-gb",
                "5.0",
                "--objects",
                "100",
                "--format",
                "text",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "S3 Cost Report" in captured.out
        assert "b" in captured.out

    def test_main_json_exit_zero(self, capsys):
        """main() returns 0 and emits parseable JSON in --format json."""
        rc = main(
            [
                "--bucket",
                "b",
                "--size-gb",
                "5.0",
                "--objects",
                "100",
                "--format",
                "json",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["bucket"] == "b"
        assert parsed["metrics"]["object_count"] == 100

    def test_main_missing_required_arg_exits_nonzero(self, capsys):
        """main() propagates argparse SystemExit (rc=2) when --bucket is missing."""
        # argparse calls sys.exit(2) for usage errors. main() does not catch
        # this, so SystemExit propagates. We assert the type.
        with pytest.raises(SystemExit) as excinfo:
            main(["--size-gb", "1.0", "--objects", "1"])
        assert excinfo.value.code == 2

    def test_main_uses_lifecycle_recommendation(self, capsys):
        """main() in text mode prints the recommended class derived from --avg-age-days."""
        rc = main(
            [
                "--bucket",
                "b",
                "--size-gb",
                "10.0",
                "--objects",
                "1",
                "--avg-age-days",
                "200",
                "--format",
                "text",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        # DEEP_ARCHIVE is the lifecycle class for age > 180.
        assert "DEEP_ARCHIVE" in captured.out

    def test_main_uses_non_default_storage_class(self, capsys):
        """main() honors --storage-class in the cost calculation."""
        rc = main(
            [
                "--bucket",
                "b",
                "--size-gb",
                "10.0",
                "--objects",
                "1",
                "--storage-class",
                "GLACIER",
                "--format",
                "json",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["metrics"]["storage_class"] == "GLACIER"
        # 10 GB GLACIER @ $0.004 = $0.04
        assert parsed["cost_breakdown"]["storage_monthly_usd"] == pytest.approx(0.04)

    def test_main_default_argv_is_sys_argv(self, monkeypatch, capsys):
        """When called with no argv, main() falls back to sys.argv[1:]."""
        monkeypatch.setattr(sys, "argv", ["cost_estimator.py", "--bucket", "b",
                                          "--size-gb", "2.0", "--objects", "1",
                                          "--format", "json"])
        rc = main()
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["bucket"] == "b"
        assert parsed["metrics"]["size_gb"] == 2.0
