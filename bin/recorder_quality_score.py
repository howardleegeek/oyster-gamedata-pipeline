#!/usr/bin/env python3
"""recorder_quality_score.py — Score a recorder clip 0-10 with farming flag.

Wraps :func:`bin.anomaly_detector_clip_quality.analyze_clip` so the recorder
GUI can show a one-glance quality verdict in its post-record banner ("8/10 —
high entropy, no farming detected") instead of dumping raw anomaly arrays.

Input: a clip directory produced by the recorder (post-pipeline applied),
expected to contain at least one of:

  * ``action_camera.json``  — list of {ts, action, x, y, z, ...} records
  * ``trajectory.json``     — optional pre-extracted trajectory

Output: ``qa_score.json`` with the schema::

    {
        "clip_id": str,
        "score":   float,         # 0-10, higher is better
        "farming_detected": bool, # True if low entropy & low variance
        "anomalies": list[str],
        "metrics":  dict[str, Any],
        "scored_at": str          # UTC ISO
    }

Score formula (kept simple by design — buyers want explainability over a
black-box ML classifier):

    score = 10
    score -= 4   if 'low_action_entropy' anomaly
    score -= 3   if 'low_camera_variance' anomaly
    score -= 1   per other anomaly category
    score  = max(0, min(10, score))

Usage:
    python3 bin/recorder_quality_score.py --clip-dir <dir>
    python3 bin/recorder_quality_score.py --clip-dir <dir> --json-only

Exit codes:
    0 — score written, clip looks clean
    1 — clip dir missing or no scorable input
    2 — score written, but farming-detected flag is True
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PERFECT_SCORE: float = 10.0
LOW_ENTROPY_PENALTY: float = 4.0
LOW_VARIANCE_PENALTY: float = 3.0
OTHER_ANOMALY_PENALTY: float = 1.0
MIN_SCORE: float = 0.0


def _load_action_camera(clip_dir: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Load ``action_camera.json`` from ``clip_dir`` and split it into
    action records and camera records.

    Returns a 2-tuple ``(actions, camera)``.  Either list may be empty when
    the recorder dropped placeholders rather than real data.
    """
    path = clip_dir / "action_camera.json"
    if not path.exists():
        return [], []
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not parse %s: %s", path, exc)
        return [], []
    if isinstance(payload, dict):
        actions = payload.get("actions") or []
        camera = payload.get("camera") or []
    elif isinstance(payload, list):
        # Combined shape: each record has both action + camera fields.
        actions = [r for r in payload if "action" in r or "value" in r]
        camera = [r for r in payload if any(k in r for k in ("x", "y", "z"))]
    else:
        return [], []
    return actions, camera


def _load_trajectory(clip_dir: Path) -> List[Dict[str, float]]:
    """Load optional ``trajectory.json`` for farming-pattern detection."""
    path = clip_dir / "trajectory.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def build_clip_data(clip_dir: Path) -> Dict[str, Any]:
    """Assemble the dict consumed by ``analyze_clip``.

    The detector is permissive about missing keys so we deliberately avoid
    fabricating zeros when a feed is absent — leaving a key out makes the
    detector skip its corresponding heuristic, which is the right thing.
    """
    actions, camera = _load_action_camera(clip_dir)
    trajectory = _load_trajectory(clip_dir)
    clip_id = clip_dir.name
    data: Dict[str, Any] = {"clip_id": clip_id}
    if actions:
        data["actions"] = actions
    if camera:
        data["camera"] = camera
    if trajectory:
        data["trajectory"] = trajectory
    return data


def _load_json_object(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _critical_failures(clip_dir: Path, clip_data: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    if not any(clip_data.get(key) for key in ("actions", "camera", "trajectory")):
        failures.append("missing_scorable_input")

    raw_quality = _load_json_object(clip_dir / "raw_quality.json")
    if raw_quality:
        if raw_quality.get("verdict") == "FAIL":
            failures.append("raw_quality_fail")
        if raw_quality.get("video_live") is False:
            failures.append("video_not_live")
        if raw_quality.get("game_state_live") is False:
            failures.append("game_state_not_live")
        if raw_quality.get("frozen_while_moving") is True:
            failures.append("frozen_while_moving")

    metadata = _load_json_object(clip_dir / "metadata.json")
    if metadata.get("video_frozen") is True:
        failures.append("metadata_video_frozen")
    video_capture = metadata.get("video_capture")
    if isinstance(video_capture, dict):
        if video_capture.get("video_frozen") is True:
            failures.append("metadata_video_capture_frozen")
        if video_capture.get("validation_passed") is False:
            failures.append("metadata_video_capture_validation_failed")

    return sorted(set(failures))


def compute_score(
    analysis: Dict[str, Any], critical_failures: Optional[List[str]] = None
) -> Tuple[float, bool]:
    """Convert an ``analyze_clip`` result into ``(score, farming_flag)``.

    Args:
        analysis: The dict returned by ``anomaly_detector_clip_quality``.

    Returns:
        ``(score, farming_detected)`` where score ∈ [0, 10].
    """
    anomalies = list(analysis.get("anomalies", []))
    critical_failures = critical_failures or []
    score = PERFECT_SCORE
    low_entropy = any(a.startswith("low_action_entropy") for a in anomalies)
    low_variance = any(a.startswith("low_camera_variance") for a in anomalies)
    critical = bool(critical_failures) or any(a.startswith("critical_failure") for a in anomalies)
    other_count = sum(
        1
        for a in anomalies
        if not a.startswith("low_action_entropy")
        and not a.startswith("low_camera_variance")
        and not a.startswith("critical_failure")
    )
    if critical:
        farming = low_entropy and low_variance
        return MIN_SCORE, farming
    if low_entropy:
        score -= LOW_ENTROPY_PENALTY
    if low_variance:
        score -= LOW_VARIANCE_PENALTY
    score -= OTHER_ANOMALY_PENALTY * other_count
    score = max(MIN_SCORE, min(PERFECT_SCORE, score))
    farming = low_entropy and low_variance
    return score, farming


def _import_detector() -> Any:
    """Import the anomaly detector under whichever name is reachable.

    The recorder is run as a flat script with ``bin/`` on ``sys.path``,
    while the test harness adds the repo root and treats ``bin`` as a
    package.  Both styles must work without sys.path mutation.
    """
    for name in ("anomaly_detector_clip_quality", "bin.anomaly_detector_clip_quality"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise ImportError("anomaly_detector_clip_quality not importable from any path")


def score_clip(clip_dir: Path, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute the qa_score.json payload for ``clip_dir``.

    Args:
        clip_dir: Path to a recorder clip directory.
        config: Optional config dict passed through to ``analyze_clip``.

    Raises:
        ImportError: if ``anomaly_detector_clip_quality`` is not importable.
    """
    detector = _import_detector()
    clip_data = build_clip_data(clip_dir)
    analysis = detector.analyze_clip(clip_data, config or {})
    critical_failures = _critical_failures(clip_dir, clip_data)
    if critical_failures:
        anomalies = list(analysis.get("anomalies", []))
        anomalies.extend(f"critical_failure:{failure}" for failure in critical_failures)
        analysis["anomalies"] = anomalies
    score, farming = compute_score(analysis, critical_failures=critical_failures)
    payload: Dict[str, Any] = {
        "clip_id": clip_data.get("clip_id", clip_dir.name),
        "score": round(score, 2),
        "farming_detected": farming,
        "critical_failures": critical_failures,
        "anomalies": list(analysis.get("anomalies", [])),
        "metrics": analysis.get("metrics", {}),
        "scored_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    }
    return payload


def write_score(clip_dir: Path, payload: Dict[str, Any]) -> Path:
    """Write the score payload to ``<clip_dir>/qa_score.json``."""
    out = clip_dir / "qa_score.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return out


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--clip-dir", type=Path, required=True, help="Recorder clip directory to score"
    )
    parser.add_argument(
        "--json-only", action="store_true", help="Only print the qa_score.json content"
    )
    parser.add_argument(
        "--config-json", type=Path, default=None, help="Optional JSON file with detector overrides"
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    clip_dir: Path = args.clip_dir.resolve()
    if not clip_dir.exists() or not clip_dir.is_dir():
        logger.error("Clip dir not found: %s", clip_dir)
        return 1
    config: Optional[Dict[str, Any]] = None
    if args.config_json and args.config_json.exists():
        try:
            config = json.loads(args.config_json.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Bad config json: %s", exc)
    try:
        payload = score_clip(clip_dir, config=config)
    except ImportError as exc:
        logger.error("anomaly_detector_clip_quality missing: %s", exc)
        return 1
    out = write_score(clip_dir, payload)
    if args.json_only:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        logger.info(
            "Wrote %s — score=%s farming=%s",
            out,
            payload["score"],
            payload["farming_detected"],
        )
    if payload["critical_failures"]:
        return 1
    return 2 if payload["farming_detected"] else 0


if __name__ == "__main__":
    sys.exit(main())
