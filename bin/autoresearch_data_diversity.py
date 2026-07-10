#!/usr/bin/env python3
"""Autoresearch data diversity analyzer.

Computes biome / time-of-day / weather distribution per 1000 clips
and flags undersampled categories that fall below a configurable
threshold.  Designed for the G122 autoresearch pipeline.

Usage:
    python bin/autoresearch_data_diversity.py metadata.csv \
        --biome-col biome --tod-col time_of_day --weather-col weather \
        --threshold 0.02 --per-k 1000
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> List[Dict[str, str]]:
    """Return list of row-dicts from a CSV file."""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _load_json(path: Path) -> List[Dict[str, Any]]:
    """Return list of records from a JSON file (array or object with 'clips' key)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "clips" in data:
        return data["clips"]
    raise ValueError("JSON must be a list or contain a 'clips' key")


def _load_yaml(path: Path) -> List[Dict[str, Any]]:
    """Return list of records from a YAML file."""
    if yaml is None:
        raise RuntimeError("PyYAML is required to read YAML files")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "clips" in data:
        return data["clips"]
    raise ValueError("YAML must be a list or contain a 'clips' key")


def load_records(path: Path) -> List[Dict[str, Any]]:
    """Auto-detect format and load clip metadata records."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(path)
    if suffix in (".json",):
        return _load_json(path)
    if suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    raise ValueError(f"Unsupported file extension: {suffix}")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> str:
    """Coerce a metadata value to a lowercase, stripped string."""
    return str(value).strip().lower()


def compute_distribution(
    records: Sequence[Dict[str, Any]],
    field: str,
    per_k: int = 1000,
) -> Dict[str, float]:
    """Return per-1000-clip frequency for *field* across *records*."""
    counter: Counter[str] = Counter()
    for rec in records:
        val = _normalize(rec.get(field, ""))
        if val:
            counter[val] += 1
    total = sum(counter.values()) or 1
    return {k: (v / total) * per_k for k, v in counter.most_common()}


def flag_undersampled(
    dist: Dict[str, float],
    threshold: float,
    per_k: int = 1000,
) -> List[str]:
    """Return category names whose per-K count falls below *threshold*."""
    return [k for k, v in sorted(dist.items()) if v < threshold * per_k]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(
    dist: Dict[str, float],
    undersampled: List[str],
    field: str,
    per_k: int,
    threshold: float,
) -> None:
    """Print a human-readable distribution table to stdout."""
    print(f"\n{'='*60}")
    print(f"  {field.upper()}  (per {per_k} clips, threshold={threshold:.0%})")
    print(f"{'='*60}")
    for cat, count in dist.items():
        flag = " ⚠ UNDERSAMPLED" if cat in undersampled else ""
        print(f"  {cat:<20s} {count:>8.1f}{flag}")
    if not undersampled:
        print("  (all categories meet threshold)")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    p = argparse.ArgumentParser(
        description="Analyse biome / time-of-day / weather diversity per 1000 clips."
    )
    p.add_argument("metadata", type=Path, help="CSV / JSON / YAML clip metadata file")
    p.add_argument("--biome-col", default="biome", help="Column name for biome (default: biome)")
    p.add_argument("--tod-col", default="time_of_day", help="Column name for time-of-day")
    p.add_argument("--weather-col", default="weather", help="Column name for weather")
    p.add_argument(
        "--per-k",
        type=int,
        default=1000,
        help="Normalise to per-K clips (default: 1000)",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=0.02,
        help="Undersampled threshold as fraction of per-K (default: 0.02 → 20 per 1000)",
    )
    p.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Write machine-readable JSON report to this path",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — parse args, compute distributions, print report."""
    parser = build_parser()
    args = parser.parse_args(argv)

    records = load_records(args.metadata)
    if not records:
        print("ERROR: no records found in metadata file", file=sys.stderr)
        return 1

    fields = {"biome": args.biome_col, "time_of_day": args.tod_col, "weather": args.weather_col}
    report: Dict[str, Any] = {"total_clips": len(records), "fields": {}}

    for label, col in fields.items():
        dist = compute_distribution(records, col, per_k=args.per_k)
        undersampled = flag_undersampled(dist, args.threshold, per_k=args.per_k)
        print_report(dist, undersampled, label, args.per_k, args.threshold)
        report["fields"][label] = {
            "column": col,
            "distribution": dist,
            "undersampled": undersampled,
        }

    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"JSON report written to {args.json_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
