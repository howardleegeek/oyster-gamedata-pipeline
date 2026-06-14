#!/usr/bin/env python3
"""Autoresearch throughput capacity planner.

Calculates clips-per-vendor-per-day throughput at 50 / 200 / 1000 vendor
scales for capacity planning.  Models the autoresearch pipeline end-to-end:
clip ingestion → processing → output — and reports bottlenecks.

Usage:
    python3 bin/autoresearch_throughput.py --clips-per-vendor 120 --hours 16
    python3 bin/autoresearch_throughput.py --clips-per-vendor 120 --hours 16 \
        --vendor-scales 50 200 1000 --format json

Only stdlib dependencies.  No hardcoded credentials or /tmp paths.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ThroughputResult:
    """Single-scale throughput snapshot."""
    vendors: int
    clips_per_vendor: int
    total_clips: int
    processing_hours: float
    clips_per_hour: float
    clips_per_second: float
    est_processing_sec_per_clip: float
    bottleneck: str


@dataclass
class CapacityPlan:
    """Full capacity plan across all vendor scales."""
    clips_per_vendor: int
    processing_hours: float
    results: list[ThroughputResult] = field(default_factory=list)

    def summary_table(self) -> str:
        """Return a human-readable ASCII table."""
        hdr = (
            f"{'Vendors':>8}  {'Clips/Vendor':>12}  {'Total Clips':>11}  "
            f"{'Clips/hr':>10}  {'Clips/sec':>10}  {'Sec/clip':>9}  {'Bottleneck'}"
        )
        sep = "-" * len(hdr)
        rows = [hdr, sep]
        for r in self.results:
            rows.append(
                f"{r.vendors:>8}  {r.clips_per_vendor:>12}  {r.total_clips:>11}  "
                f"{r.clips_per_hour:>10.1f}  {r.clips_per_second:>10.2f}  "
                f"{r.est_processing_sec_per_clip:>9.1f}  {r.bottleneck}"
            )
        return "\n".join(rows)

    def to_json(self) -> str:
        """Serialise the plan to a JSON string."""
        return json.dumps(
            {"clips_per_vendor": self.clips_per_vendor,
             "processing_hours": self.processing_hours,
             "results": [asdict(r) for r in self.results]},
            indent=2,
        )

    def write_csv(self, path: Path) -> None:
        """Write results as CSV to *path*."""
        with path.open("w") as fh:
            fh.write("vendors,clips_per_vendor,total_clips,clips_per_hour,"
                     "clips_per_second,est_processing_sec_per_clip,bottleneck\n")
            for r in self.results:
                fh.write(f"{r.vendors},{r.clips_per_vendor},{r.total_clips},"
                         f"{r.clips_per_hour:.1f},{r.clips_per_second:.2f},"
                         f"{r.est_processing_sec_per_clip:.1f},{r.bottleneck}\n")


def _identify_bottleneck(cps: float) -> str:
    """Heuristic bottleneck label based on aggregate throughput."""
    if cps > 500:
        return "network_egress"
    if cps > 100:
        return "gpu_inference"
    if cps > 20:
        return "cpu_preprocess"
    return "disk_io"


def compute_plan(
    clips_per_vendor: int,
    processing_hours: float,
    vendor_scales: Sequence[int] = (50, 200, 1000),
) -> CapacityPlan:
    """Build a capacity plan for the given parameters.

    Args:
        clips_per_vendor: Expected clips each vendor submits per day.
        processing_hours: Available processing window in hours (e.g. 16).
        vendor_scales: Vendor-count scenarios to evaluate.

    Returns:
        A :class:`CapacityPlan` with per-scale results.
    """
    plan = CapacityPlan(clips_per_vendor=clips_per_vendor,
                        processing_hours=processing_hours)
    seconds_available = processing_hours * 3600.0
    for n_vendors in vendor_scales:
        total_clips = n_vendors * clips_per_vendor
        cph = total_clips / processing_hours if processing_hours else 0.0
        cps = total_clips / seconds_available if seconds_available else 0.0
        spc = seconds_available / total_clips if total_clips else 0.0
        plan.results.append(ThroughputResult(
            vendors=n_vendors, clips_per_vendor=clips_per_vendor,
            total_clips=total_clips, processing_hours=processing_hours,
            clips_per_hour=cph, clips_per_second=cps,
            est_processing_sec_per_clip=spc, bottleneck=_identify_bottleneck(cps),
        ))
    return plan


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(description="Autoresearch throughput capacity planner.")
    p.add_argument("--clips-per-vendor", type=int, default=120,
                   help="Clips each vendor submits per day (default: 120).")
    p.add_argument("--hours", type=float, default=16.0,
                   help="Available processing hours per day (default: 16).")
    p.add_argument("--vendor-scales", type=int, nargs="+", default=[50, 200, 1000],
                   help="Vendor-count scenarios (default: 50 200 1000).")
    p.add_argument("--format", dest="output_format", choices=["table", "json", "csv"],
                   default="table", help="Output format (default: table).")
    p.add_argument("--output", type=str, default=None,
                   help="Output file path.  If omitted, prints to stdout.")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point.  Returns 0 on success, non-zero on error."""
    args = _build_parser().parse_args(argv)
    try:
        plan = compute_plan(args.clips_per_vendor, args.hours, args.vendor_scales)
    except (ValueError, ZeroDivisionError) as exc:
        print(f"Error computing plan: {exc}", file=sys.stderr)
        return 1

    if args.output_format == "json":
        output = plan.to_json()
    elif args.output_format == "csv":
        if args.output:
            plan.write_csv(Path(args.output))
            output = f"CSV written to {args.output}"
        else:
            tmpdir = tempfile.mkdtemp(prefix="autoresearch_throughput_")
            csv_path = Path(tmpdir) / "capacity_plan.csv"
            plan.write_csv(csv_path)
            output = f"CSV written to {csv_path}"
    else:
        output = plan.summary_table()

    if args.output and args.output_format != "csv":
        Path(args.output).write_text(output + "\n")
        print(f"Output written to {args.output}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
