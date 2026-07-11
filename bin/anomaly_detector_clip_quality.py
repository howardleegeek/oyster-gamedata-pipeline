#!/usr/bin/env python3
"""
Anomaly Detector for Clip Quality

Flags low-effort vendor submissions based on:
- Action entropy below threshold
- Camera variance below threshold
- Identical trajectory pattern across N clips (farming detection)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional

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
            raise ImportError("numpy is required") from None
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


def run_detection(
    input_path: Path,
    config: dict[str, Any],
    output_path: Optional[Path] = None,
) -> dict[str, Any]:
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
            all_anomalies.append({
                'clip_id': result['clip_id'],
                'anomalies': result['anomalies'],
                'metrics': result['metrics'],
            })
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Detect low-quality or farmed vendor clip submissions'
    )
    parser.add_argument('input', type=Path, help='Input file (.json, .jsonl) or directory')
    parser.add_argument('-o', '--output', type=Path, help='Output file for results')
    parser.add_argument(
        '--action-entropy-threshold',
        type=float,
        default=2.0,
        help='Min action entropy (default: 2.0)',
    )
    parser.add_argument(
        '--camera-variance-threshold',
        type=float,
        default=0.5,
        help='Min camera variance (default: 0.5)',
    )
    parser.add_argument(
        '--farming-n-clips',
        type=int,
        default=3,
        help='Clips to flag as farming (default: 3)',
    )
    parser.add_argument('-q', '--quiet', action='store_true', help='Suppress output')
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Main entry point."""
    args = parse_args(argv)
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
            print(f"  Farming patterns detected: {results['farming_patterns_detected']}")
            if results['anomalies']:
                print("\nAnomalous clips:")
                for anomaly in results['anomalies']:
                    if 'clip_id' in anomaly:
                        print(f"  - {anomaly['clip_id']}: {anomaly.get('anomalies', [])}")
                    else:
                        cnt = anomaly.get('count')
                        print(f"  - Farming: {cnt} clips with identical trajectory")

    has_anomalies = results['clips_with_anomalies'] > 0 or results['farming_patterns_detected'] > 0
    return 1 if has_anomalies else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
