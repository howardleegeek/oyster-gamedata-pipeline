#!/usr/bin/env python3
"""
Batch quality aggregate.

For a batch of N sessions, computes:
  - Percentile ranks for each session
  - Outlier identification (IQR-based)
  - Tier assignment for buyer top-tier delivery
  - Summary statistics

Usage:
    python bin/batch_quality_aggregate.py <quality_scores_dir> \\
        [--config <config.yaml>] [--output <output.json>]
"""

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


def load_weights(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load tunable weights from YAML config."""
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "config", "quality_weights.yaml",
        )
    config_path = os.path.normpath(config_path)
    if not os.path.exists(config_path):
        return {
            "tiers": {
                "top_tier": 0.90,
                "premium": 0.75,
                "standard": 0.50,
            }
        }
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def compute_percentile_ranks(scores: List[float]) -> List[float]:
    """Compute percentile rank for each score in the list."""
    if not scores:
        return []
    n = len(scores)
    ranks = []
    for s in scores:
        below = sum(1 for x in scores if x < s)
        ranks.append(round(below / n, 4))
    return ranks


def identify_outliers(scores: List[float]) -> Tuple[List[int], List[int]]:
    """
    Identify outliers using IQR method.
    Returns (low_outlier_indices, high_outlier_indices).
    """
    if len(scores) < 4:
        return [], []

    sorted_scores = sorted(scores)
    n = len(sorted_scores)

    q1_idx = n // 4
    q3_idx = (3 * n) // 4
    q1 = sorted_scores[q1_idx]
    q3 = sorted_scores[q3_idx]
    iqr = q3 - q1

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    low_outliers = [i for i, s in enumerate(scores) if s < lower_bound]
    high_outliers = [i for i, s in enumerate(scores) if s > upper_bound]

    return low_outliers, high_outliers


def assign_tier(percentile: float, tiers: Dict[str, float]) -> str:
    """Assign a tier label based on percentile rank."""
    if percentile >= tiers.get("top_tier", 0.90):
        return "top_tier"
    elif percentile >= tiers.get("premium", 0.75):
        return "premium"
    elif percentile >= tiers.get("standard", 0.50):
        return "standard"
    else:
        return "below_standard"


def aggregate_batch(
    session_scores: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Aggregate quality scores for a batch of sessions.

    session_scores: list of dicts, each with at least "composite_score"
                    and optionally "components", "session_id", etc.
    """
    if config is None:
        config = load_weights()

    tiers = config.get("tiers", {
        "top_tier": 0.90,
        "premium": 0.75,
        "standard": 0.50,
    })

    n = len(session_scores)
    if n == 0:
        return {
            "batch_size": 0,
            "sessions": [],
            "summary": {},
            "outliers": {"low": [], "high": []},
            "tier_distribution": {},
            "recommendations": [],
        }

    # Extract composite scores
    scores = [s.get("composite_score", 0.0) for s in session_scores]

    # Compute percentile ranks
    percentile_ranks = compute_percentile_ranks(scores)

    # Identify outliers
    low_outliers, high_outliers = identify_outliers(scores)

    # Assign tiers and build enriched session list
    enriched_sessions = []
    tier_counts = {"top_tier": 0, "premium": 0, "standard": 0, "below_standard": 0}

    for i, session in enumerate(session_scores):
        percentile = percentile_ranks[i]
        tier = assign_tier(percentile, tiers)
        tier_counts[tier] += 1

        enriched = {
            "session_id": session.get("session_id", f"session_{i}"),
            "composite_score": session.get("composite_score", 0.0),
            "percentile_rank": percentile,
            "tier": tier,
            "is_outlier": i in low_outliers or i in high_outliers,
            "components": session.get("components", {}),
        }
        enriched_sessions.append(enriched)

    # Summary statistics
    mean_score = sum(scores) / n
    sorted_scores = sorted(scores)
    mid_idx = n // 2
    median_score = (
        sorted_scores[mid_idx]
        if n % 2 == 1
        else (sorted_scores[mid_idx - 1] + sorted_scores[mid_idx]) / 2
    )
    variance = sum((s - mean_score) ** 2 for s in scores) / n
    std_dev = math.sqrt(variance)

    summary = {
        "batch_size": n,
        "mean_score": round(mean_score, 4),
        "median_score": round(median_score, 4),
        "std_dev": round(std_dev, 4),
        "min_score": round(min(scores), 4),
        "max_score": round(max(scores), 4),
        "q1": round(sorted_scores[n // 4], 4),
        "q3": round(sorted_scores[(3 * n) // 4], 4),
    }

    # Recommendations for buyer top-tier delivery
    recommendations = []
    top_tier_sessions = [s for s in enriched_sessions if s["tier"] == "top_tier"]
    if top_tier_sessions:
        top_pct = int((1 - tiers["top_tier"]) * 100)
        recommendations.append({
            "action": "flag_for_top_tier",
            "sessions": [s["session_id"] for s in top_tier_sessions],
            "count": len(top_tier_sessions),
            "rationale": f"{len(top_tier_sessions)} sessions in top {top_pct}% by quality score",
        })

    if low_outliers:
        recommendations.append({
            "action": "review_low_outliers",
            "sessions": [enriched_sessions[i]["session_id"] for i in low_outliers],
            "count": len(low_outliers),
            "rationale": "Sessions with unusually low quality scores - may need re-recording",
        })

    if high_outliers:
        recommendations.append({
            "action": "flag_high_outliers",
            "sessions": [enriched_sessions[i]["session_id"] for i in high_outliers],
            "count": len(high_outliers),
            "rationale": "Exceptionally high quality sessions - premium pricing candidates",
        })

    return {
        "batch_size": n,
        "sessions": enriched_sessions,
        "summary": summary,
        "outliers": {
            "low": [enriched_sessions[i]["session_id"] for i in low_outliers],
            "high": [enriched_sessions[i]["session_id"] for i in high_outliers],
        },
        "tier_distribution": tier_counts,
        "recommendations": recommendations,
    }


def main():
    """
    CLI: aggregate quality scores for a batch of sessions.

    Usage:
        python bin/batch_quality_aggregate.py <scores_dir> \\
            [--config <config.yaml>] [--output <output.json>]
    """
    import argparse

    parser = argparse.ArgumentParser(description="Batch quality aggregate")
    parser.add_argument("scores_dir", help="Directory containing quality_score.json files")
    parser.add_argument("--config", help="Path to quality weights YAML config")
    parser.add_argument("--output", default="batch_quality_aggregate.json", help="Output JSON path")
    args = parser.parse_args()

    # Load config
    config = load_weights(args.config) if args.config else load_weights()

    # Load all quality score files
    scores_dir = Path(args.scores_dir)
    session_scores = []

    for json_file in sorted(scores_dir.glob("*.json")):
        with open(json_file, "r") as f:
            data = json.load(f)
            # Use filename as session_id
            session_id = json_file.stem
            data["session_id"] = session_id
            session_scores.append(data)

    # Aggregate
    result = aggregate_batch(session_scores, config)

    # Write output
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Batch aggregate written to {args.output}")
    print(f"  Sessions: {result['batch_size']}")
    print(f"  Mean score: {result['summary'].get('mean_score', 'N/A')}")
    print(f"  Tier distribution: {result['tier_distribution']}")
    if result["recommendations"]:
        print(f"  Recommendations: {len(result['recommendations'])}")
        for rec in result["recommendations"]:
            print(f"    - {rec['action']}: {rec['count']} sessions")


if __name__ == "__main__":
    main()
