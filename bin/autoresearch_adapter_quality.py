#!/usr/bin/env python3
"""
Autoresearch Adapter Quality Metrics

Computes coverage and recall by comparing golden-corpus diffs
against hand-labeled mineflayer scenes.

Author: G115 Production Engineering
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    """Load and parse a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_corpus(path: Path, key: str) -> dict[str, set[str]]:
    """Load corpus from directory or file. Returns scene_id -> entity set."""
    corpus: dict[str, set[str]] = {}

    if path.is_file():
        data = load_json(path)
        for sid, ents in data.items():
            corpus[sid] = set(ents) if isinstance(ents, list) else {ents}
    else:
        for jf in sorted(path.glob("*.json")):
            data = load_json(jf)
            sid = jf.stem
            if isinstance(data, dict):
                ents = data.get(key, data.get("entities", []))
            else:
                ents = data if isinstance(data, list) else [data]
            corpus[sid] = set(ents)

    return corpus


def compute_metrics(
    golden: dict[str, set[str]], predicted: dict[str, set[str]]
) -> dict[str, float]:
    """Compute coverage, recall, precision, and F1 score."""
    g_ids = set(golden.keys())
    p_ids = set(predicted.keys())
    matched = g_ids & p_ids

    # Coverage: fraction of golden scenes found in predictions
    coverage = len(matched) / len(g_ids) if g_ids else 0.0

    # Entity-level recall and precision
    total_g, total_p, correct = 0, 0, 0
    for sid in matched:
        g_ents = golden[sid]
        p_ents = predicted.get(sid, set())
        total_g += len(g_ents)
        total_p += len(p_ents)
        correct += len(g_ents & p_ents)

    recall = correct / total_g if total_g else 0.0
    precision = correct / total_p if total_p else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "coverage": coverage,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "golden_count": len(golden),
        "predicted_count": len(predicted),
        "matched_count": len(matched),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for adapter quality metrics."""
    parser = argparse.ArgumentParser(
        description="Compute coverage and recall metrics for autoresearch adapter."
    )
    parser.add_argument(
        "--golden", type=Path, required=True, help="Path to golden corpus (dir or JSON)"
    )
    parser.add_argument(
        "--predicted", type=Path, required=True, help="Path to predicted scenes (dir or JSON)"
    )
    parser.add_argument("--output", type=Path, default=None, help="Output JSON file (optional)")
    parser.add_argument("--verbose", action="store_true", help="Print report to stdout")

    args = parser.parse_args(argv)

    # Validate paths
    for p, name in [(args.golden, "Golden"), (args.predicted, "Predicted")]:
        if not p.exists():
            print(f"Error: {name} path does not exist: {p}", file=sys.stderr)
            return 1

    try:
        golden = load_corpus(args.golden, "entities")
        predicted = load_corpus(args.predicted, "labels")
    except Exception as e:
        print(f"Error loading data: {e}", file=sys.stderr)
        return 1

    metrics = compute_metrics(golden, predicted)

    if args.verbose:
        print("=" * 40)
        print("Autoresearch Adapter Quality Report")
        print("=" * 40)
        print(f"Golden scenes:     {metrics['golden_count']}")
        print(f"Predicted scenes:  {metrics['predicted_count']}")
        print(f"Matched scenes:    {metrics['matched_count']}")
        print("-" * 40)
        print(f"Coverage:   {metrics['coverage']:.4f}")
        print(f"Recall:     {metrics['recall']:.4f}")
        print(f"Precision:  {metrics['precision']:.4f}")
        print(f"F1 Score:   {metrics['f1']:.4f}")
        print("=" * 40)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(metrics, f, indent=2)
        except Exception as e:
            print(f"Error writing output: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
