#!/usr/bin/env python3
"""
observability_metrics_emitter.py

Production utility that emits Prometheus-style counters and histograms to stdout.
Designed for adapter / lint / upload pipeline stages.

Usage:
    python observability_metrics_emitter.py --type counter --name adapter_requests_total --value 1 --labels stage=adapter
    python observability_metrics_emitter.py --type histogram --name upload_duration_seconds --value 1.234 --labels operation=upload
"""

import argparse
import sys
import time
from typing import Dict, List, Optional

# Prometheus metric type constants
METRIC_TYPE_COUNTER = "counter"
METRIC_TYPE_GAUGE = "gauge"
METRIC_TYPE_HISTOGRAM = "histogram"

# Standard histogram buckets (in seconds, for latency metrics)
DEFAULT_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]


def parse_labels(label_string: Optional[str]) -> Dict[str, str]:
    """
    Parse a comma-separated key=value string into a dictionary.

    Args:
        label_string: String like "stage=adapter,status=success"

    Returns:
        Dictionary of label key-value pairs

    Raises:
        ValueError: If label format is invalid
    """
    if not label_string:
        return {}

    labels = {}
    for raw_part in label_string.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Invalid label format: {part}. Expected key=value")
        key, value = part.split("=", 1)
        labels[key.strip()] = value.strip()

    return labels


def format_labels(labels: Dict[str, str]) -> str:
    """Format labels for Prometheus output."""
    if not labels:
        return ""
    label_parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(label_parts) + "}"


def emit_counter(name: str, value: float, labels: Dict[str, str], help_text: str = "") -> None:
    """
    Emit a Prometheus counter metric.

    Counters can only increase (or be reset to zero).
    """
    if help_text:
        print(f"# HELP {name} {help_text}")
    print(f"# TYPE {name} counter")
    print(f"{name}{format_labels(labels)} {value}")


def emit_gauge(name: str, value: float, labels: Dict[str, str], help_text: str = "") -> None:
    """
    Emit a Prometheus gauge metric.

    Gauges can go up and down.
    """
    if help_text:
        print(f"# HELP {name} {help_text}")
    print(f"# TYPE {name} gauge")
    print(f"{name}{format_labels(labels)} {value}")


def emit_histogram(
    name: str,
    value: float,
    labels: Dict[str, str],
    buckets: Optional[List[float]] = None,
    help_text: str = "",
) -> None:
    """
    Emit a Prometheus histogram metric.

    Histograms track distributions and automatically calculate
    quantiles and bucket counts.
    """
    if buckets is None:
        buckets = DEFAULT_BUCKETS

    if help_text:
        print(f"# HELP {name} {help_text}")
    print(f"# TYPE {name} histogram")

    # Emit bucket counts (cumulative)
    sorted_buckets = sorted(buckets)
    cumulative_count = 0
    for bucket_value in sorted_buckets:
        if value >= bucket_value:
            cumulative_count += 1
        bucket_labels = dict(labels)
        bucket_labels["le"] = str(bucket_value)
        print(f"{name}_bucket{format_labels(bucket_labels)} {cumulative_count}")

    # +Inf bucket always gets the total count
    inf_labels = dict(labels)
    inf_labels["le"] = "+Inf"
    print(f"{name}_bucket{format_labels(inf_labels)} {cumulative_count + 1}")

    # Emit sum and count
    print(f"{name}_sum{format_labels(labels)} {value}")
    print(f"{name}_count{format_labels(labels)} {cumulative_count + 1}")


def emit_timestamp(name: str, labels: Dict[str, str]) -> None:
    """Emit a timestamp metric for correlation."""
    now_ms = int(time.time() * 1000)
    print(f"{name}_timestamp_ms{format_labels(labels)} {now_ms}")


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for the observability metrics emitter.

    Args:
        argv: Command line arguments (defaults to sys.argv)

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    parser = argparse.ArgumentParser(
        description="Emit Prometheus-style metrics to stdout",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Emit a counter for adapter requests
  %(prog)s --type counter --name adapter_requests_total --value 1 --labels stage=adapter

  # Emit a histogram for upload duration
  %(prog)s --type histogram --name upload_duration_seconds --value 2.5 --labels operation=upload

  # Emit a gauge for current queue depth
  %(prog)s --type gauge --name queue_depth --value 42 --labels queue=pending
        """,
    )

    parser.add_argument(
        "--type",
        choices=[METRIC_TYPE_COUNTER, METRIC_TYPE_GAUGE, METRIC_TYPE_HISTOGRAM],
        required=True,
        help="Type of metric to emit",
    )

    parser.add_argument(
        "--name",
        required=True,
        help="Metric name (e.g., adapter_requests_total)",
    )

    parser.add_argument(
        "--value",
        type=float,
        required=True,
        help="Metric value",
    )

    parser.add_argument(
        "--labels",
        help="Comma-separated labels (e.g., stage=adapter,status=success)",
    )

    parser.add_argument(
        "--help-text",
        default="",
        help="Help text for the metric",
    )

    parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Include timestamp with the metric",
    )

    parser.add_argument(
        "--buckets",
        help="Comma-separated histogram bucket values (for histogram type only)",
    )

    args = parser.parse_args(argv)

    try:
        labels = parse_labels(args.labels)
    except ValueError as e:
        print(f"Error parsing labels: {e}", file=sys.stderr)
        return 1

    # Parse custom buckets if provided
    buckets = None
    if args.buckets:
        try:
            buckets = [float(b.strip()) for b in args.buckets.split(",")]
        except ValueError as e:
            print(f"Error parsing buckets: {e}", file=sys.stderr)
            return 1

    # Emit the metric based on type
    if args.type == METRIC_TYPE_COUNTER:
        emit_counter(args.name, args.value, labels, args.help_text)
    elif args.type == METRIC_TYPE_GAUGE:
        emit_gauge(args.name, args.value, labels, args.help_text)
    elif args.type == METRIC_TYPE_HISTOGRAM:
        emit_histogram(args.name, args.value, labels, buckets, args.help_text)
    else:
        print(f"Unknown metric type: {args.type}", file=sys.stderr)
        return 1

    # Optionally emit timestamp
    if args.timestamp:
        emit_timestamp(args.name, labels)

    return 0


if __name__ == "__main__":
    sys.exit(main())
