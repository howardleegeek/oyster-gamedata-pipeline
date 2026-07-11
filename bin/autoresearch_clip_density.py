#!/usr/bin/env python3
"""
autoresearch_clip_density.py — Action density across scene types (combat / build / explore).

Computes a CLIP-based diversity metric measuring how densely different action types
populate a scene. Useful for autoresearch pipelines that need to quantify scene
composition balance.

Usage:
    python bin/autoresearch_clip_density.py --input <path> [--scene-types combat build explore]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)
DEFAULT_SCENE_TYPES: Tuple[str, ...] = ("combat", "build", "explore")


def _get_numpy():
    """Lazy-import numpy."""
    import numpy as np  # noqa: F811
    return np


def compute_clip_density(
    embeddings: List[List[float]],
    labels: List[str],
    scene_types: Sequence[str] = DEFAULT_SCENE_TYPES,
) -> Dict[str, Dict[str, float]]:
    """Compute action density per scene type from CLIP embeddings.

    For each scene type calculates count, mean_density (avg cosine sim to centroid),
    variance, and diversity_score (1 - variance, normalised [0,1]).
    """
    np = _get_numpy()
    if len(embeddings) != len(labels):
        raise ValueError("embeddings and labels length must match")
    vecs = np.array(embeddings, dtype=np.float32)
    results: Dict[str, Dict[str, float]] = {}
    for stype in scene_types:
        mask = np.array([lbl == stype for lbl in labels], dtype=bool)
        subset = vecs[mask]
        count = int(mask.sum())
        if count == 0:
            results[stype] = {
                "count": 0, "mean_density": 0.0, "variance": 0.0, "diversity_score": 0.0
            }
            continue
        centroid = subset.mean(axis=0)
        cn = np.linalg.norm(centroid)
        if cn < 1e-8:
            results[stype] = {
                "count": count, "mean_density": 0.0, "variance": 0.0, "diversity_score": 0.0
            }
            continue
        centroid /= cn
        sims = subset @ centroid
        variance = float(sims.var())
        results[stype] = {
            "count": count,
            "mean_density": round(float(sims.mean()), 4),
            "variance": round(variance, 4),
            "diversity_score": round(max(0.0, 1.0 - variance), 4),
        }
    return results


def load_embeddings_from_json(path: str) -> Tuple[List[List[float]], List[str]]:
    """Load embeddings + labels from JSON: {\"embeddings\": [...], \"labels\": [...]}."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("embeddings", []), data.get("labels", [])


def load_embeddings_from_csv(path: str) -> Tuple[List[List[float]], List[str]]:
    """Load embeddings + labels from CSV (header: label,dim0,dim1,...)."""
    embeddings: List[List[float]] = []
    labels: List[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        fh.readline()  # skip header
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) < 2:
                continue
            labels.append(parts[0])
            embeddings.append([float(v) for v in parts[1:]])
    return embeddings, labels


def compute_overall_diversity(per_type: Dict[str, Dict[str, float]]) -> float:
    """Overall diversity: 50% normalised entropy + 50% weighted diversity."""
    np = _get_numpy()
    counts = np.array([v["count"] for v in per_type.values()], dtype=np.float32)
    total = counts.sum()
    if total == 0:
        return 0.0
    probs = counts / total
    nonzero = probs[probs > 0]
    entropy = -float((nonzero * np.log(nonzero)).sum())
    max_entropy = np.log(len(per_type)) if len(per_type) > 1 else 1.0
    entropy_norm = entropy / max_entropy if max_entropy > 0 else 0.0
    weights = np.array([v["diversity_score"] for v in per_type.values()], dtype=np.float32)
    return round(0.5 * entropy_norm + 0.5 * float((probs * weights).sum()), 4)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    p = argparse.ArgumentParser(description="Compute CLIP action density across scene types.")
    p.add_argument("--input", required=True, help="Input JSON/CSV with embeddings + labels.")
    p.add_argument("--scene-types", nargs="+", default=list(DEFAULT_SCENE_TYPES))
    p.add_argument("--output", default=None, help="Output JSON path (stdout if omitted).")
    p.add_argument("--format", choices=["json", "csv"], default=None, help="Force input format.")
    p.add_argument("--verbose", "-v", action="store_true", help="Debug logging.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on error."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        return 1
    fmt = args.format or ("json" if input_path.suffix.lower() == ".json" else "csv")
    logger.info("Loading embeddings from %s (format=%s)", input_path, fmt)
    try:
        loader = load_embeddings_from_json if fmt == "json" else load_embeddings_from_csv
        embeddings, labels = loader(str(input_path))
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.error("Failed to load input: %s", exc)
        return 1
    if not embeddings:
        logger.warning("No embeddings found in input file.")
        return 1
    logger.info("Loaded %d embeddings with %d unique labels", len(embeddings), len(set(labels)))
    per_type = compute_clip_density(embeddings, labels, args.scene_types)
    overall = compute_overall_diversity(per_type)
    output = {
        "scene_types": args.scene_types,
        "per_type": per_type,
        "overall_diversity": overall,
        "total_frames": len(embeddings),
    }
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2)
        logger.info("Results written to %s", out_path)
    else:
        print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
