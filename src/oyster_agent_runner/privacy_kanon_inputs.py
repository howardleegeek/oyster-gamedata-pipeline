#!/usr/bin/env python3
"""
privacy_kanon_inputs.py - k-Anonymity bucketing for mouse/key inter-event intervals.

Implements k-anonymity bucketing on mouse and keyboard inter-event intervals
to defeat keystroke-dynamics re-identification attacks (~99% re-id rates in literature).
Cluster B: Privacy protection for input event timing data.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Sequence

# Lazy import for optional dependencies
try:
    import numpy as np  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


@dataclass
class KanonConfig:
    """Configuration for k-anonymity bucketing."""
    k: int = 5
    min_interval_ms: float = 10.0
    max_interval_ms: float = 5000.0
    num_buckets: int = 20


def compute_intervals(events: Sequence[float]) -> list[float]:
    """Compute inter-event intervals from timestamps in milliseconds."""
    if len(events) < 2:
        return []
    return [events[i] - events[i - 1] for i in range(1, len(events)) if events[i] >= events[i - 1]]


def create_buckets(cfg: KanonConfig) -> list[dict[str, float]]:
    """Create quantization buckets based on configuration."""
    step = (cfg.max_interval_ms - cfg.min_interval_ms) / cfg.num_buckets
    return [{"lower": cfg.min_interval_ms + i * step,
             "upper": cfg.min_interval_ms + (i + 1) * step, "count": 0}
            for i in range(cfg.num_buckets)]


def quantize_interval(interval: float, buckets: list[dict[str, float]]) -> float:
    """Quantize a single interval to its bucket midpoint."""
    for b in buckets:
        if b["lower"] <= interval < b["upper"]:
            return (b["lower"] + b["upper"]) / 2.0
    return buckets[0]["lower"] if interval < buckets[0]["lower"] else buckets[-1]["upper"]


def apply_kanon(intervals: Sequence[float], cfg: KanonConfig) -> tuple[list[float], dict[str, Any]]:
    """Apply k-anonymity bucketing to intervals.

    Returns tuple of (quantized intervals, metadata dict with bucket stats).
    """
    if not intervals:
        return [], {"buckets": [], "k_satisfied": True, "min_bucket_count": 0}

    buckets = create_buckets(cfg)
    # Count intervals per bucket
    for iv in intervals:
        for b in buckets:
            if b["lower"] <= iv < b["upper"]:
                b["count"] += 1
                break
        else:
            buckets[-1 if iv >= buckets[-1]["upper"] else 0]["count"] += 1

    quantized = [quantize_interval(iv, buckets) for iv in intervals]
    counts = [b["count"] for b in buckets if b["count"] > 0]
    min_count = min(counts) if counts else 0

    return quantized, {
        "buckets": buckets,
        "k_satisfied": min_count >= cfg.k,
        "min_bucket_count": min_count,
        "total_intervals": len(intervals),
    }


def process_events(mouse_events: Sequence[float], key_events: Sequence[float],
                   cfg: KanonConfig) -> dict[str, Any]:
    """Process mouse and key events with k-anonymity bucketing."""
    q_mouse, m_meta = apply_kanon(compute_intervals(mouse_events), cfg)
    q_key, k_meta = apply_kanon(compute_intervals(key_events), cfg)
    return {
        "mouse": {"quantized_intervals": q_mouse, "metadata": m_meta},
        "keyboard": {"quantized_intervals": q_key, "metadata": k_meta},
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="k-Anonymity bucketing for mouse/key inter-event intervals")
    parser.add_argument("-k", "--k-anon", type=int, default=5,
                        help="Minimum k for k-anonymity (default: 5)")
    parser.add_argument("--min-interval", type=float, default=10.0,
                        help="Minimum interval in ms (default: 10.0)")
    parser.add_argument("--max-interval", type=float, default=5000.0,
                        help="Maximum interval in ms (default: 5000.0)")
    parser.add_argument("--num-buckets", type=int, default=20,
                        help="Number of quantization buckets (default: 20)")
    parser.add_argument("--input", type=str,
                        help="JSON file with 'mouse' and 'key' event timestamp arrays")
    parser.add_argument("--output", type=str,
                        help="Output JSON file for quantized results")
    args = parser.parse_args(argv)

    cfg = KanonConfig(k=args.k_anon, min_interval_ms=args.min_interval,
                      max_interval_ms=args.max_interval, num_buckets=args.num_buckets)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        mouse_events, key_events = data.get("mouse", []), data.get("key", [])
    else:
        mouse_events = [100.0, 250.0, 400.0, 600.0, 850.0]
        key_events = [50.0, 180.0, 320.0, 500.0, 750.0]

    result = process_events(mouse_events, key_events, cfg)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"Results written to {args.output}")
    else:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())