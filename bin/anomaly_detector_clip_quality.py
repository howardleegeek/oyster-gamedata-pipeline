#!/usr/bin/env python3
"""
Anomaly Detector for Clip Quality (G194).

Two modes:

1. **Per-clip analyse** (legacy) — flags low-effort vendor submissions
   based on action entropy, camera variance, and identical trajectory
   patterns across N clips (farming detection).

2. **Batch outlier** (new) — given a *batch* directory containing one
   ``metrics.json`` per clip, computes population mean/σ for each metric
   (``avg_fps``, ``file_size_mb``, ``depth_invalid_ratio``,
   ``action_entropy``) and flags any clip whose metric deviates by more
   than ``--sigma`` (default 3) from the batch baseline. Emits
   ``anomalies.json`` and ``anomalies.csv``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional

# Lazy imports for heavy dependencies
_numpy = None


def _np() -> Any:
    """Lazily import numpy."""
    global _numpy
    if _numpy is None:
        try:
            import numpy
            _numpy = numpy
        except ImportError:
            raise ImportError("numpy is required")
    return _numpy


def _entropy(values: list[float], bins: int = 10) -> float:
    """Compute Shannon entropy of a distribution."""
    if not values:
        return 0.0
    arr = _np().array(values)
    hist, _ = _np().histogram(arr, bins=bins)
    total = hist.sum()
    if total == 0:
        return 0.0
    probs = hist / total
    return sum(-p * math.log2(p) for p in probs if p > 0)


def _variance(values: list[float]) -> float:
    """Compute variance of values."""
    if not values:
        return 0.0
    return float(_np().var(_np().array(values)))


def _norm_trajectory(trajectory: list[dict[str, float]]) -> tuple[tuple[float, ...], ...]:
    """Normalize trajectory for comparison (translation + rotation invariant)."""
    if not trajectory:
        return ()
    np = _np()
    positions = np.array([[t.get('x', 0), t.get('y', 0), t.get('z', 0)] for t in trajectory])
    if len(positions) > 0:
        positions = positions - positions.mean(axis=0)
    if len(positions) > 1:
        diffs = np.diff(positions, axis=0)
        norms = np.linalg.norm(diffs, axis=1, keepdims=True)
        norms = np.where(norms > 1e-6, norms, 1e-6)
        normalized_diffs = diffs / norms
    else:
        normalized_diffs = positions
    return tuple(tuple(float(x) for x in row) for row in normalized_diffs)


def _hash_trajectory(trajectory: list[dict[str, float]], precision: int = 2) -> str:
    """Create hashable representation of trajectory."""
    normalized = _norm_trajectory(trajectory)
    rounded = [tuple(round(v, precision) for v in point) for point in normalized]
    return str(rounded)


def analyze_clip(clip_data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Analyze a single clip for quality anomalies."""
    result = {
        'clip_id': clip_data.get('id', clip_data.get('clip_id', 'unknown')),
        'anomalies': [],
        'metrics': {},
    }
    
    # Check action entropy
    actions = clip_data.get('actions', [])
    if actions:
        action_values = [a.get('value', a.get('action', 0)) for a in actions]
        entropy = _entropy(action_values)
        result['metrics']['action_entropy'] = entropy
        threshold = config.get('action_entropy_threshold', 2.0)
        if entropy < threshold:
            result['anomalies'].append(f'low_action_entropy:{entropy:.3f}<{threshold}')
    
    # Check camera variance
    camera_data = clip_data.get('camera', [])
    if camera_data:
        x_vals = [c.get('x', 0) for c in camera_data]
        y_vals = [c.get('y', 0) for c in camera_data]
        z_vals = [c.get('z', 0) for c in camera_data]
        combined_var = math.sqrt(_variance(x_vals)**2 + _variance(y_vals)**2 + _variance(z_vals)**2)
        result['metrics']['camera_variance'] = combined_var
        threshold = config.get('camera_variance_threshold', 0.5)
        if combined_var < threshold:
            result['anomalies'].append(f'low_camera_variance:{combined_var:.3f}<{threshold}')
    
    # Check trajectory pattern
    trajectory = clip_data.get('trajectory', [])
    if trajectory:
        result['metrics']['trajectory_hash'] = _hash_trajectory(trajectory)
    
    return result


def detect_farming(clip_results: list[dict[str, Any]], n_clips: int = 3) -> list[dict[str, Any]]:
    """Detect identical trajectory patterns across multiple clips."""
    hash_groups: dict[str, list[dict[str, Any]]] = {}
    for result in clip_results:
        traj_hash = result.get('metrics', {}).get('trajectory_hash')
        if traj_hash:
            hash_groups.setdefault(traj_hash, []).append(result)
    
    farming_patterns = []
    for traj_hash, clips in hash_groups.items():
        if len(clips) >= n_clips:
            farming_patterns.append({
                'trajectory_hash': traj_hash,
                'clip_ids': [c['clip_id'] for c in clips],
                'count': len(clips),
                'anomaly': 'identical_trajectory_pattern',
            })
    return farming_patterns


def load_clips(path: Path) -> list[dict[str, Any]]:
    """Load clip data from file or directory."""
    clips = []
    if path.is_dir():
        for ext in ('.json', '.jsonl'):
            for file_path in path.glob(f'*{ext}'):
                try:
                    with open(file_path, 'r') as f:
                        if ext == '.jsonl':
                            for line in f:
                                if line.strip():
                                    clips.append(json.loads(line))
                        else:
                            data = json.load(f)
                            clips.extend(data if isinstance(data, list) else [data])
                except (json.JSONDecodeError, OSError) as e:
                    print(f"Warning: Could not load {file_path}: {e}", file=sys.stderr)
    else:
        with open(path, 'r') as f:
            if path.suffix == '.jsonl':
                for line in f:
                    if line.strip():
                        clips.append(json.loads(line))
            else:
                data = json.load(f)
                clips.extend(data if isinstance(data, list) else [data])
    return clips


def run_detection(input_path: Path, config: dict[str, Any], output_path: Optional[Path] = None) -> dict[str, Any]:
    """Run full anomaly detection pipeline."""
    clips = load_clips(input_path)
    if not clips:
        return {
            'status': 'error',
            'message': 'No clips found',
            'total_clips': 0,
            'clips_with_anomalies': 0,
            'farming_patterns_detected': 0,
            'anomalies': [],
            'farming_patterns': [],
        }
    
    clip_results = [analyze_clip(clip, config) for clip in clips]
    farming_patterns = detect_farming(clip_results, config.get('farming_n_clips', 3))
    
    all_anomalies = []
    for result in clip_results:
        if result['anomalies']:
            all_anomalies.append({'clip_id': result['clip_id'], 'anomalies': result['anomalies'], 'metrics': result['metrics']})
    all_anomalies.extend(farming_patterns)
    
    results = {
        'status': 'complete',
        'total_clips': len(clips),
        'clips_with_anomalies': len([r for r in clip_results if r['anomalies']]),
        'farming_patterns_detected': len(farming_patterns),
        'anomalies': all_anomalies,
        'farming_patterns': farming_patterns,
    }
    
    if output_path:
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2)
    return results


# ---------------------------------------------------------------------------
# G194 batch-outlier extensions — cross-clip 3σ detection
# ---------------------------------------------------------------------------

DEFAULT_BATCH_METRICS = (
    "avg_fps",
    "file_size_mb",
    "depth_invalid_ratio",
    "action_entropy",
)


def _load_clip_metrics(path: Path) -> list[dict[str, Any]]:
    """Load per-clip metric snapshots from a batch directory or single JSON.

    Accepts either:
    * a directory of ``<clip_id>.json`` files (each holding
      ``{"clip_id": ..., "metrics": {...}}``);
    * a single JSON list of such records.
    """
    clips: list[dict[str, Any]] = []
    if path.is_dir():
        for fp in sorted(path.glob("*.json")):
            try:
                payload = json.loads(fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"warn: skipping {fp}: {exc}", file=sys.stderr)
                continue
            if isinstance(payload, list):
                clips.extend(payload)
            elif isinstance(payload, dict):
                # auto-fill clip_id from filename if missing
                payload.setdefault("clip_id", fp.stem)
                clips.append(payload)
    elif path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        clips = payload if isinstance(payload, list) else [payload]
    else:
        raise FileNotFoundError(path)
    return clips


def batch_outliers(
    clips: Iterable[dict[str, Any]],
    sigma: float = 3.0,
    metric_names: Iterable[str] = DEFAULT_BATCH_METRICS,
) -> list[dict[str, Any]]:
    """Flag clips whose metrics deviate by more than ``sigma`` from the
    batch population mean.

    Returns a list of records::

        {
          "clip_id": "...",
          "outlier_metrics": {
            "<metric>": {"value": ..., "mean": ..., "std": ..., "z_score": ...},
          }
        }

    Metrics with zero population variance are skipped silently (avoids
    division by zero — no anomaly when everyone agrees).
    """
    clip_list = list(clips)
    if not clip_list:
        return []
    np = _np()

    # Collect metric value series.
    series: dict[str, list[float]] = {m: [] for m in metric_names}
    for c in clip_list:
        metrics = c.get("metrics", {})
        for m in metric_names:
            if m in metrics:
                try:
                    series[m].append(float(metrics[m]))
                except (TypeError, ValueError):
                    series[m].append(float("nan"))

    # Compute mean / std per metric (drop empty / single-sample series).
    stats: dict[str, tuple[float, float]] = {}
    for m, vals in series.items():
        arr = np.array([v for v in vals if not math.isnan(v)])
        if arr.size < 2:
            continue
        std = float(np.std(arr, ddof=1))
        if std == 0.0:
            continue
        stats[m] = (float(np.mean(arr)), std)

    outliers: list[dict[str, Any]] = []
    for c in clip_list:
        clip_id = c.get("clip_id", c.get("id", "?"))
        flagged: dict[str, dict[str, float]] = {}
        for m, (mean, std) in stats.items():
            metrics = c.get("metrics", {})
            if m not in metrics:
                continue
            try:
                val = float(metrics[m])
            except (TypeError, ValueError):
                continue
            z = (val - mean) / std
            if abs(z) > sigma:
                flagged[m] = {
                    "value": round(val, 6),
                    "mean": round(mean, 6),
                    "std": round(std, 6),
                    "z_score": round(z, 4),
                }
        if flagged:
            outliers.append({"clip_id": clip_id, "outlier_metrics": flagged})
    return outliers


def run_batch(
    batch_dir: Path,
    output_json: Optional[Path] = None,
    output_csv: Optional[Path] = None,
    sigma: float = 3.0,
    metric_names: Iterable[str] = DEFAULT_BATCH_METRICS,
) -> int:
    """Run batch-outlier detection and emit JSON + CSV reports.

    Returns 0 when no outliers were found, 1 otherwise (suitable for CI).
    """
    clips = _load_clip_metrics(batch_dir)
    outliers = batch_outliers(clips, sigma=sigma, metric_names=metric_names)
    payload = {
        "tool": "anomaly_detector_clip_quality",
        "mode": "batch_outlier",
        "batch_dir": str(batch_dir),
        "clip_count": len(clips),
        "outlier_count": len(outliers),
        "sigma": sigma,
        "metrics": list(metric_names),
        "outliers": outliers,
    }
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if output_csv:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["clip_id", "metric", "value", "mean", "std", "z_score"])
            for o in outliers:
                for m, stats in o["outlier_metrics"].items():
                    writer.writerow([
                        o["clip_id"], m,
                        stats["value"], stats["mean"], stats["std"], stats["z_score"],
                    ])
    return 1 if outliers else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments — supports legacy and batch modes."""
    parser = argparse.ArgumentParser(
        description='Detect low-quality / farmed clips (per-clip) and 3σ '
                    'batch outliers (cross-clip).')
    parser.add_argument('input', type=Path, nargs='?',
                        help='Legacy: input file (.json/.jsonl) or dir.')
    parser.add_argument('-o', '--output', type=Path,
                        help='Legacy: output JSON for per-clip results.')
    parser.add_argument('--action-entropy-threshold', type=float, default=2.0,
                        help='Min action entropy (default: 2.0)')
    parser.add_argument('--camera-variance-threshold', type=float, default=0.5,
                        help='Min camera variance (default: 0.5)')
    parser.add_argument('--farming-n-clips', type=int, default=3,
                        help='Clips to flag as farming (default: 3)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Suppress output')
    # batch-outlier mode
    parser.add_argument('--batch', type=Path,
                        help='Batch outlier mode: directory of per-clip '
                             'metrics JSONs.')
    parser.add_argument('--output-json', type=Path,
                        help='Batch mode JSON report path '
                             '(default: ./anomalies.json).')
    parser.add_argument('--output-csv', type=Path,
                        help='Batch mode CSV report path '
                             '(default: ./anomalies.csv).')
    parser.add_argument('--sigma', type=float, default=3.0,
                        help='Z-score threshold for batch outliers (default 3).')
    parser.add_argument('--metric', action='append',
                        help='Metric name to include in batch analysis '
                             '(default set: avg_fps, file_size_mb, '
                             'depth_invalid_ratio, action_entropy).')
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Main entry point. Dispatches to batch or legacy mode."""
    args = parse_args(argv)

    # ---- batch mode ---------------------------------------------------
    if args.batch is not None:
        if not args.batch.exists():
            print(f"Error: batch dir not found: {args.batch}", file=sys.stderr)
            return 1
        out_json = args.output_json or Path("anomalies.json")
        out_csv = args.output_csv or Path("anomalies.csv")
        metrics = tuple(args.metric) if args.metric else DEFAULT_BATCH_METRICS
        rc = run_batch(args.batch, out_json, out_csv, sigma=args.sigma,
                       metric_names=metrics)
        if not args.quiet:
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            print(f"batch outlier analysis: "
                  f"{payload['outlier_count']} / {payload['clip_count']} clips "
                  f"flagged at {args.sigma}σ — "
                  f"json={out_json} csv={out_csv}")
        return rc

    # ---- legacy mode --------------------------------------------------
    if args.input is None:
        print("Error: provide INPUT or --batch <dir>", file=sys.stderr)
        return 1
    config = {
        'action_entropy_threshold': args.action_entropy_threshold,
        'camera_variance_threshold': args.camera_variance_threshold,
        'farming_n_clips': args.farming_n_clips,
    }
    if not args.input.exists():
        print(f"Error: Input path does not exist: {args.input}", file=sys.stderr)
        return 1
    try:
        results = run_detection(args.input, config, args.output)
    except ImportError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error during analysis: {e}", file=sys.stderr)
        return 1
    if not args.quiet:
        if results['status'] == 'error':
            print(f"Error: {results.get('message', 'Unknown error')}")
        else:
            print("Analysis complete:")
            print(f"  Total clips: {results['total_clips']}")
            print(f"  Clips with anomalies: {results['clips_with_anomalies']}")
            print(f"  Farming patterns detected: "
                  f"{results['farming_patterns_detected']}")
            if results['anomalies']:
                print("\nAnomalous clips:")
                for anomaly in results['anomalies']:
                    if 'clip_id' in anomaly:
                        print(f"  - {anomaly['clip_id']}: "
                              f"{anomaly.get('anomalies', [])}")
                    else:
                        print(f"  - Farming: {anomaly.get('count')} clips "
                              f"with identical trajectory")
    return 1 if (results['clips_with_anomalies'] > 0
                 or results['farming_patterns_detected'] > 0) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
