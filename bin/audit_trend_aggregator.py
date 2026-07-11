#!/usr/bin/env python3
"""Cross-Run Trend Aggregator.

Scans a directory of audit JSON reports, builds a time-series of PASS-rate
and QM* numeric values across runs, and outputs a markdown report plus
machine-readable JSON.

Usage:
    python3 audit_trend_aggregator.py <results_dir> [--out TREND-REPORT.md] [--lookback N]
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Sparkline helpers
# ---------------------------------------------------------------------------

SPARKLINE_CHARS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def sparkline(values):
    """Return a Unicode sparkline string for a list of numeric values."""
    if not values:
        return ""
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return "\u2500" * len(values)
    lo, hi = min(finite), max(finite)
    if lo == hi:
        return SPARKLINE_CHARS[4] * len(values)
    chars = []
    for v in values:
        if v is None or not math.isfinite(v):
            chars.append(" ")
        else:
            idx = int((v - lo) / (hi - lo) * (len(SPARKLINE_CHARS) - 1))
            idx = max(0, min(idx, len(SPARKLINE_CHARS) - 1))
            chars.append(SPARKLINE_CHARS[idx])
    return "".join(chars)


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def mean(values):
    """Arithmetic mean of a list of numbers."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def stddev(values):
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / len(values))


def linear_slope(values):
    """Simple linear regression slope (least-squares) for a sequence."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = mean(xs)
    y_mean = mean(values)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values, strict=True))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_runs(results_dir):
    """Load all JSON audit reports from *results_dir*, sorted by timestamp."""
    p = Path(results_dir)
    if not p.is_dir():
        print(f"Error: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    runs = []
    for fpath in sorted(p.glob("*.json")):
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: skipping {fpath}: {exc}", file=sys.stderr)
            continue

        # Use explicit timestamp if present, else file mtime
        ts = data.get("timestamp")
        if ts is None:
            mtime = os.path.getmtime(fpath)
            ts = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        data["_timestamp"] = ts
        data["_file"] = str(fpath)
        runs.append(data)

    # Sort by timestamp ascending
    runs.sort(key=lambda r: r["_timestamp"])
    return runs


# ---------------------------------------------------------------------------
# Schema drift detection
# ---------------------------------------------------------------------------

def check_schema_drift(runs):
    """Warn if total_items varies wildly across runs."""
    totals = [r.get("total_items", 0) for r in runs]
    if not totals:
        return
    lo, hi = min(totals), max(totals)
    if hi > 0 and (hi - lo) / hi > 0.25:
        print(
            f"Warning: schema-version drift detected \u2014 total_items ranges "
            f"from {lo} to {hi} across {len(runs)} runs",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Time-series extraction
# ---------------------------------------------------------------------------

def build_timeseries(runs):
    """Build per-item and per-QM time-series from a list of run dicts."""
    # Collect all unique item names across runs
    all_items = set()
    for r in runs:
        for item in r.get("items", []):
            name = item.get("name", item.get("id", "unknown"))
            all_items.add(name)

    # Per-item status series: list of (timestamp, status) per item
    item_series = {name: [] for name in sorted(all_items)}
    for r in runs:
        ts = r["_timestamp"]
        status_map = {}
        for item in r.get("items", []):
            name = item.get("name", item.get("id", "unknown"))
            status_map[name] = item.get("status", "UNKNOWN")
        for name in item_series:
            item_series[name].append((ts, status_map.get(name, "MISSING")))

    # Per-QM numeric series
    qm_keys = sorted(
        k for r in runs for k in r if k.startswith("QM") and isinstance(r[k], (int, float))
    )
    qm_keys = sorted(set(qm_keys))
    qm_series = {k: [] for k in qm_keys}
    for r in runs:
        ts = r["_timestamp"]
        for k in qm_series:
            val = r.get(k)
            if val is not None and isinstance(val, (int, float)):
                qm_series[k].append((ts, float(val)))
            else:
                qm_series[k].append((ts, None))

    # Overall pass-rate series
    pass_rate_series = []
    for r in runs:
        ts = r["_timestamp"]
        total = r.get("total_items", 0)
        passed = r.get("passed", 0)
        rate = (passed / total * 100) if total > 0 else 0.0
        pass_rate_series.append((ts, rate))

    return item_series, qm_series, pass_rate_series


# ---------------------------------------------------------------------------
# Regression / improvement detection
# ---------------------------------------------------------------------------

def detect_transitions(item_series):
    """Find PASS->FAIL (regressions) and FAIL->PASS (improvements)."""
    regressions = []
    improvements = []
    for name, series in item_series.items():
        for i in range(1, len(series)):
            prev_ts, prev_status = series[i - 1]
            curr_ts, curr_status = series[i]
            if prev_status == "PASS" and curr_status == "FAIL":
                regressions.append((curr_ts, name, prev_ts))
            elif prev_status == "FAIL" and curr_status == "PASS":
                improvements.append((curr_ts, name, prev_ts))
    # Sort by recency (most recent first)
    regressions.sort(key=lambda x: x[0], reverse=True)
    improvements.sort(key=lambda x: x[0], reverse=True)
    return regressions, improvements


# ---------------------------------------------------------------------------
# Markdown report generation
# ---------------------------------------------------------------------------

def generate_report(runs, item_series, qm_series, pass_rate_series,
                    regressions, improvements, lookback=10):
    """Generate the TREND-REPORT.md content."""
    lines = []
    lines.append("# Audit Trend Report\n")

    # --- Headline ---
    total_runs = len(runs)
    rates = [v for _, v in pass_rate_series]
    mean_rate = mean(rates) if rates else 0.0
    last_ts = runs[-1]["_timestamp"] if runs else "N/A"

    lines.append("## Summary\n")
    lines.append(f"- **Total runs:** {total_runs}")
    lines.append(f"- **Mean PASS rate:** {mean_rate:.1f}%")
    lines.append(f"- **Last run:** {last_ts}")
    lines.append("")

    if total_runs == 0:
        lines.append("*No runs found.*\n")
        return "\n".join(lines)

    # --- Pass-rate sparkline ---
    rate_values = [v for _, v in pass_rate_series]
    lines.append(f"**PASS rate trend:** {sparkline(rate_values)}\n")
    lines.append("")

    # --- Per-item time-series table (last N runs) ---
    lines.append("## Per-Item Time Series\n")
    lines.append(f"Showing last {lookback} runs.\n")

    # Determine which runs to show
    display_runs = runs[-lookback:] if len(runs) > lookback else runs
    display_timestamps = [r["_timestamp"] for r in display_runs]

    # Header
    header = "| Item | Status Trend | "
    header += " | ".join(f"R{i+1}" for i in range(len(display_runs)))
    header += " |"
    lines.append(header)
    sep = "|------|--------------|" + "|".join("---" for _ in display_runs) + "|"
    lines.append(sep)

    for name in sorted(item_series.keys()):
        series = item_series[name]
        # Align to display runs
        all_ts = [ts for ts, _ in series]
        statuses = [s for _, s in series]

        # Find indices matching display_runs
        row_statuses = []
        for dts in display_timestamps:
            if dts in all_ts:
                idx = all_ts.index(dts)
                row_statuses.append(statuses[idx])
            else:
                row_statuses.append("\u2014")

        # Sparkline: 1 for PASS, 0 for FAIL
        spark_vals = []
        for s in row_statuses:
            if s == "PASS":
                spark_vals.append(1.0)
            elif s == "FAIL":
                spark_vals.append(0.0)
            else:
                spark_vals.append(None)

        status_str = " | ".join(
            "\u2705" if s == "PASS" else ("\u274c" if s == "FAIL" else "\u2796")
            for s in row_statuses
        )
        sl = sparkline(spark_vals)
        lines.append(f"| {name} | {sl} | {status_str} |")

    lines.append("")

    # --- Regressions ---
    lines.append("## Regressions (PASS -> FAIL)\n")
    if regressions:
        lines.append("| Timestamp | Item | Previous Run |")
        lines.append("|-----------|------|-------------|")
        for ts, name, prev_ts in regressions:
            lines.append(f"| {ts} | {name} | {prev_ts} |")
    else:
        lines.append("*No regressions detected.*")
    lines.append("")

    # --- Improvements ---
    lines.append("## Improvements (FAIL -> PASS)\n")
    if improvements:
        lines.append("| Timestamp | Item | Previous Run |")
        lines.append("|-----------|------|-------------|")
        for ts, name, prev_ts in improvements:
            lines.append(f"| {ts} | {name} | {prev_ts} |")
    else:
        lines.append("*No improvements detected.*")
    lines.append("")

    # --- Quality dimension trends ---
    lines.append("## Quality Dimension Trends\n")
    if qm_series:
        lines.append("| Dimension | Mean +/- StdDev | Slope | Trend |")
        lines.append("|-----------|--------------|-------|-------|")
        for qm_key in sorted(qm_series.keys()):
            vals = [v for _, v in qm_series[qm_key] if v is not None]
            if not vals:
                continue
            m = mean(vals)
            sd = stddev(vals)
            slope = linear_slope(vals)
            direction = "\u2191" if slope > 0.001 else ("\u2193" if slope < -0.001 else "\u2192")
            lines.append(f"| {qm_key} | {m:.2f} +/- {sd:.2f} | {slope:+.4f} | {direction} |")
    else:
        lines.append("*No quality dimension data found.*")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Machine-readable JSON output
# ---------------------------------------------------------------------------

def build_trend_data(runs, item_series, qm_series, pass_rate_series,
                     regressions, improvements):
    """Build a dict for trend-data.json."""
    return {
        "total_runs": len(runs),
        "runs": [
            {
                "timestamp": r["_timestamp"],
                "file": r["_file"],
                "total_items": r.get("total_items", 0),
                "passed": r.get("passed", 0),
                "failed": r.get("failed", 0),
            }
            for r in runs
        ],
        "pass_rate_series": [
            {"timestamp": ts, "rate": rate} for ts, rate in pass_rate_series
        ],
        "item_series": {
            name: [
                {"timestamp": ts, "status": status}
                for ts, status in series
            ]
            for name, series in item_series.items()
        },
        "qm_series": {
            k: [
                {"timestamp": ts, "value": v}
                for ts, v in series
            ]
            for k, series in qm_series.items()
        },
        "regressions": [
            {"timestamp": ts, "item": name, "previous_run": prev_ts}
            for ts, name, prev_ts in regressions
        ],
        "improvements": [
            {"timestamp": ts, "item": name, "previous_run": prev_ts}
            for ts, name, prev_ts in improvements
        ],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Cross-Run Trend Aggregator for audit JSON reports"
    )
    parser.add_argument(
        "results_dir",
        help="Directory containing audit JSON reports (*.json)",
    )
    parser.add_argument(
        "--out",
        default="TREND-REPORT.md",
        help="Output markdown file (default: TREND-REPORT.md)",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=10,
        help="Number of recent runs to show in per-item table (default: 10)",
    )
    args = parser.parse_args()

    results_dir = args.results_dir
    out_path = args.out
    lookback = args.lookback

    # Load runs
    runs = load_runs(results_dir)

    if not runs:
        print("No runs found - 0 runs loaded.", file=sys.stderr)
        # Still produce empty outputs
        report = generate_report(runs, {}, {}, [], [], [], lookback)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(report)
        trend_data = build_trend_data(runs, {}, {}, [], [], [])
        trend_json_path = os.path.join(os.path.dirname(out_path) or ".", "trend-data.json")
        with open(trend_json_path, "w", encoding="utf-8") as fh:
            json.dump(trend_data, fh, indent=2)
        print(f"Report written to {out_path}")
        print(f"Trend data written to {trend_json_path}")
        return

    # Schema drift check
    check_schema_drift(runs)

    # Build time-series
    item_series, qm_series, pass_rate_series = build_timeseries(runs)

    # Detect transitions
    regressions, improvements = detect_transitions(item_series)

    # Generate report
    report = generate_report(
        runs, item_series, qm_series, pass_rate_series,
        regressions, improvements, lookback,
    )

    # Write markdown
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"Report written to {out_path}")

    # Write machine-readable JSON
    trend_data = build_trend_data(
        runs, item_series, qm_series, pass_rate_series,
        regressions, improvements,
    )
    trend_json_path = os.path.join(os.path.dirname(out_path) or ".", "trend-data.json")
    with open(trend_json_path, "w", encoding="utf-8") as fh:
        json.dump(trend_data, fh, indent=2)
    print(f"Trend data written to {trend_json_path}")


if __name__ == "__main__":
    main()
