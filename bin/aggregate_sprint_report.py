#!/usr/bin/env python3
"""Collapse 100-iter logs into a markdown sprint report."""

import argparse
import glob
import json
import os
import statistics


def aggregate(log_dir: str, output_md: str) -> dict:
    """Read iter_*.json files, compute metrics, write markdown report."""
    pattern = os.path.join(log_dir, "iter_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No iter_*.json files found in {log_dir}")

    records = []
    for f in files:
        with open(f) as fh:
            records.append(json.load(fh))

    iter_count = len(records)
    pass_count = sum(1 for r in records if r.get("lint_exit", 1) == 0)
    fail_count = iter_count - pass_count

    total_seconds = [r["total_seconds"] for r in records]
    capture_seconds = [r["capture_seconds"] for r in records]
    adapt_seconds = [r["adapt_seconds"] for r in records]
    records_counts = [r["records"] for r in records]

    ts_sorted = sorted(total_seconds)
    p50 = ts_sorted[iter_count // 2]
    p95 = ts_sorted[int(iter_count * 0.95)]
    ts_mean = statistics.mean(total_seconds)
    ts_stddev = statistics.stdev(total_seconds) if iter_count > 1 else 0.0

    capture_mean = statistics.mean(capture_seconds)
    adapt_mean = statistics.mean(adapt_seconds)
    cumulative_compute_min = sum(total_seconds) / 60.0

    # Distinct values
    distinct_records = len(set(records_counts))
    distinct_total = len(set(total_seconds))

    # Histogram: 1-second buckets
    ts_min = min(total_seconds)
    ts_max = max(total_seconds)
    bucket_counts = {}
    for v in total_seconds:
        bucket_counts[v] = bucket_counts.get(v, 0) + 1

    # Drift check: bucket means for quartiles (only if enough data for 4+ quartiles)
    quartile_size = iter_count // 4
    drift_buckets = {}
    if quartile_size > 0:
        for i, label in enumerate(["1-25", "26-50", "51-75", "76-100"]):
            start = i * quartile_size
            end = start + quartile_size
            chunk = total_seconds[start:end]
            if chunk:  # Guard against empty chunk
                drift_buckets[label] = statistics.mean(chunk)

    # Build markdown
    lines = []
    lines.append(f"# Sprint validation report — {iter_count} iterations\n")
    lines.append("## Top metrics\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Iterations | {iter_count} |")
    lines.append(f"| Passed | {pass_count} |")
    lines.append(f"| Failed | {fail_count} |")
    lines.append(f"| Total seconds (min) | {min(total_seconds)} |")
    lines.append(f"| Total seconds (p50) | {p50} |")
    lines.append(f"| Total seconds (mean) | {ts_mean:.1f} |")
    lines.append(f"| Total seconds (p95) | {p95} |")
    lines.append(f"| Total seconds (max) | {max(total_seconds)} |")
    lines.append(f"| Total seconds (stddev) | {ts_stddev:.1f} |")
    lines.append(f"| Capture seconds (mean) | {capture_mean:.1f} |")
    lines.append(f"| Adapt seconds (mean) | {adapt_mean:.1f} |")
    lines.append(f"| Distinct record counts | {distinct_records} |")
    lines.append(f"| Distinct total_seconds | {distinct_total} |")
    lines.append(f"| Cumulative compute (min) | {cumulative_compute_min:.1f} |")

    lines.append("\n## Distribution histogram (1s buckets)\n")
    max_count = max(bucket_counts.values())
    bar_width = 50
    # Convert to int for range(), clamp to reasonable bounds
    ts_min_int = max(0, int(ts_min))
    ts_max_int = int(ts_max)
    for sec in range(ts_min_int, ts_max_int + 1):
        cnt = bucket_counts.get(sec, 0)
        bar_len = int(cnt / max_count * bar_width) if max_count else 0
        bar = "█" * bar_len
        lines.append(f"  {sec:>4}s | {bar} ({cnt})")

    lines.append("\n## Drift check (quartile means)\n")
    lines.append("| Bucket | Mean total_seconds |")
    lines.append("|---|---|")
    for label, mean_val in drift_buckets.items():
        lines.append(f"| {label} | {mean_val:.1f} |")

    report = "\n".join(lines) + "\n"
    with open(output_md, "w") as fh:
        fh.write(report)

    return {
        "iter_count": iter_count,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "total_seconds_min": min(total_seconds),
        "total_seconds_p50": p50,
        "total_seconds_mean": ts_mean,
        "total_seconds_p95": p95,
        "total_seconds_max": max(total_seconds),
        "total_seconds_stddev": ts_stddev,
        "capture_seconds_mean": capture_mean,
        "adapt_seconds_mean": adapt_mean,
        "distinct_records": distinct_records,
        "distinct_total_seconds": distinct_total,
        "cumulative_compute_minutes": cumulative_compute_min,
        "drift_buckets": drift_buckets,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aggregate sprint iteration logs into a markdown report."
    )
    parser.add_argument("--log-dir", required=True, help="Directory containing iter_*.json files")
    parser.add_argument("--output", required=True, help="Output markdown file path")
    args = parser.parse_args()
    result = aggregate(args.log_dir, args.output)
    print(f"Report written to {args.output}")
    print(
        f"  Iterations: {result['iter_count']}, Pass: {result['pass_count']}, Fail: {result['fail_count']}"
    )
    print(f"  Mean total_seconds: {result['total_seconds_mean']:.1f}s")
    print(f"  Cumulative compute: {result['cumulative_compute_minutes']:.1f} min")
