#!/usr/bin/env python3
"""
Trajectory quality scorer (DataMIL-inspired).

Computes a composite 0-100 quality score from multiple components:
  - audit_norm:        Normalized audit score (0-105 → 0-30 points)
  - action_diversity:  Entropy of WASD key distribution (0-10 points)
  - camera_motion:     Bbox volume × angular variance (0-15 points)
  - failure_recovery:  Deaths + recovery within 60s (0-10 points)
  - label_density:     Subgoal/keystep label density (0-15 points)
  - multimodal:        Depth + audio + game_state alignment (0-10 points)
  - antipattern:       Idle/alt-tab/pause penalty (up to -10 points)

Deterministic: same input → same score.
Composable: each component can be inspected separately.
Tunable: weights live in config/quality_weights.yaml.
"""

import json
import math
import os
from typing import Any, Dict, List, Optional

import yaml

CRITICAL_FAILURE_SCORE_CAP = 20.0


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_weights(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load tunable weights from YAML config."""
    if config_path is None:
        # Default: look relative to this script
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "config",
            "quality_weights.yaml",
        )
    config_path = os.path.normpath(config_path)
    if not os.path.exists(config_path):
        # Fallback defaults
        return _default_config()
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _default_config() -> Dict[str, Any]:
    return {
        "components": {
            "audit_norm": {"max_points": 30},
            "action_diversity": {"max_points": 10},
            "camera_motion": {"max_points": 15},
            "failure_recovery": {"max_points": 10},
            "label_density": {"max_points": 15},
            "multimodal": {"max_points": 10},
        },
        "anti_pattern": {"max_penalty": 10},
        "tiers": {
            "top_tier": 0.90,
            "premium": 0.75,
            "standard": 0.50,
        },
    }


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------


def score_audit_norm(audit_score: float, max_points: float = 30.0) -> float:
    """
    Normalize audit score (0-105) to 0-max_points.
    audit_score of 105 → max_points, 0 → 0.
    """
    if audit_score is None:
        audit_score = 0.0
    audit_score = max(0.0, min(105.0, float(audit_score)))
    return round((audit_score / 105.0) * max_points, 4)


def score_action_diversity(
    key_counts: Dict[str, int],
    max_points: float = 10.0,
) -> float:
    """
    Compute Shannon entropy of WASD key distribution.
    Uniform distribution over 4 keys → max_points.
    Single key used → 0.
    """
    if key_counts is None:
        key_counts = {}
    wasd_keys = ["w", "a", "s", "d"]
    counts = [max(key_counts.get(k, 0), 0) for k in wasd_keys]
    total = sum(counts)
    if total == 0:
        return 0.0

    # Shannon entropy
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log2(p)

    # Max entropy for 4 keys = log2(4) = 2.0
    max_entropy = math.log2(len(wasd_keys))
    if max_entropy == 0:
        return 0.0

    normalized = entropy / max_entropy
    return round(normalized * max_points, 4)


def score_camera_motion(
    bbox_volumes: List[float],
    angular_variances: List[float],
    max_points: float = 15.0,
) -> float:
    """
    Score camera motion novelty from bbox volume and angular variance.
    Higher volume + higher variance → more novel camera work.
    """
    if not bbox_volumes or not angular_variances:
        return 0.0

    avg_volume = sum(bbox_volumes) / len(bbox_volumes)
    avg_variance = sum(angular_variances) / len(angular_variances)

    # Normalize: assume max reasonable volume ~1e6, max variance ~pi^2
    norm_volume = min(avg_volume / 1e6, 1.0)
    norm_variance = min(avg_variance / (math.pi**2), 1.0)

    # Combined score: geometric mean for balance
    combined = math.sqrt(norm_volume * norm_variance)
    return round(combined * max_points, 4)


def score_failure_recovery(
    death_events: List[Dict[str, Any]],
    max_points: float = 10.0,
) -> float:
    """
    Score failure-recovery presence.
    Each death with recovery within 60s contributes.
    3+ recoveries → max_points.
    """
    if not death_events:
        return 0.0

    recoveries = 0
    for event in death_events:
        death_time = event.get("death_time", 0)
        recovery_time = event.get("recovery_time", None)
        if recovery_time is not None:
            delta = recovery_time - death_time
            if 0 < delta <= 60:
                recoveries += 1

    # Scale: 0 recoveries → 0, 3+ → max_points
    score = min(recoveries / 3.0, 1.0) * max_points
    return round(score, 4)


def score_label_density(
    labels: List[Dict[str, Any]],
    session_duration: float,
    max_points: float = 15.0,
) -> float:
    """
    Score subgoal/keystep label density.
    Labels per minute, capped at a reasonable max.
    """
    if not labels or session_duration is None or session_duration <= 0:
        return 0.0

    labels_per_minute = len(labels) / (session_duration / 60.0)
    # Assume 5 labels/min is excellent
    normalized = min(labels_per_minute / 5.0, 1.0)
    return round(normalized * max_points, 4)


def score_multimodal(
    has_depth: bool = False,
    has_audio: bool = False,
    has_game_state: bool = False,
    depth_alignment: float = 0.0,
    audio_alignment: float = 0.0,
    game_state_alignment: float = 0.0,
    max_points: float = 10.0,
) -> float:
    """
    Score multi-modal completeness.
    Each modality present and aligned contributes.
    """
    modalities = [
        (has_depth, depth_alignment),
        (has_audio, audio_alignment),
        (has_game_state, game_state_alignment),
    ]

    total = 0.0
    for present, alignment in modalities:
        if present:
            # Alignment is 0-1; weight each modality equally
            total += alignment / len(modalities)

    # Scale to max_points
    return round(total * max_points, 4)


def score_antipattern_penalty(
    idle_seconds: float = 0.0,
    alt_tab_count: int = 0,
    pause_menu_seconds: float = 0.0,
    session_duration: float = 600.0,
    max_penalty: float = 10.0,
) -> float:
    """
    Compute anti-pattern penalty (negative contribution).
    Idle time, alt-tab, pause-menu usage reduce score.
    Returns a negative value (or 0).
    """
    if session_duration is None or session_duration <= 0:
        return 0.0

    # Idle penalty: >10% idle → penalty
    idle_ratio = idle_seconds / session_duration
    idle_penalty = max(0.0, (idle_ratio - 0.10)) * max_penalty * 2

    # Alt-tab penalty: each alt-tab costs
    alt_tab_penalty = alt_tab_count * 1.5

    # Pause menu penalty: >5% in pause → penalty
    pause_ratio = pause_menu_seconds / session_duration
    pause_penalty = max(0.0, (pause_ratio - 0.05)) * max_penalty * 2

    total_penalty = min(idle_penalty + alt_tab_penalty + pause_penalty, max_penalty)
    return round(-total_penalty, 4)


def has_critical_failure(session_data: Dict[str, Any]) -> bool:
    """Return True when upstream audit/quality gates report a critical failure."""
    for key in ("critical_failure", "critical_failed"):
        value = session_data.get(key)
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, list) and value:
            return True

    failures = session_data.get("critical_failures")
    if isinstance(failures, list) and failures:
        return True

    audit = session_data.get("audit")
    if isinstance(audit, dict):
        nested = audit.get("critical_failed") or audit.get("critical_failures")
        if isinstance(nested, list) and nested:
            return True

    verdict = str(session_data.get("audit_verdict") or session_data.get("verdict") or "").upper()
    audit_score = session_data.get("audit_score")
    try:
        audit_score_value = float(audit_score)
    except (TypeError, ValueError):
        audit_score_value = None
    return verdict == "FAIL" and audit_score_value == 0.0


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------


def compute_quality_score(
    session_data: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute composite quality score for a single session.

    session_data expected keys:
      - audit_score: float (0-105)
      - key_counts: dict of WASD counts
      - bbox_volumes: list of float
      - angular_variances: list of float
      - death_events: list of dicts with death_time, recovery_time
      - labels: list of label dicts (from SPEC #03)
      - session_duration: float (seconds)
      - multimodal: dict with has_depth, has_audio, has_game_state,
                    depth_alignment, audio_alignment, game_state_alignment
      - antipatterns: dict with idle_seconds, alt_tab_count, pause_menu_seconds

    Returns dict with composite_score, components, and metadata.
    """
    if config is None:
        config = load_weights()

    components_cfg = config.get("components", {})
    antipattern_cfg = config.get("anti_pattern", {})

    # Extract session data with defaults
    audit_score = session_data.get("audit_score") or 0.0
    key_counts = session_data.get("key_counts") or {}
    bbox_volumes = session_data.get("bbox_volumes") or []
    angular_variances = session_data.get("angular_variances") or []
    death_events = session_data.get("death_events") or []
    labels = session_data.get("labels") or []
    session_duration = session_data.get("session_duration") or 600.0
    multimodal = session_data.get("multimodal") or {}
    antipatterns = session_data.get("antipatterns") or {}

    # Compute each component
    audit_norm = score_audit_norm(
        audit_score,
        components_cfg.get("audit_norm", {}).get("max_points", 30),
    )
    action_diversity = score_action_diversity(
        key_counts,
        components_cfg.get("action_diversity", {}).get("max_points", 10),
    )
    camera_motion = score_camera_motion(
        bbox_volumes,
        angular_variances,
        components_cfg.get("camera_motion", {}).get("max_points", 15),
    )
    failure_recovery = score_failure_recovery(
        death_events,
        components_cfg.get("failure_recovery", {}).get("max_points", 10),
    )
    label_density = score_label_density(
        labels,
        session_duration,
        components_cfg.get("label_density", {}).get("max_points", 15),
    )
    multimodal_score = score_multimodal(
        has_depth=multimodal.get("has_depth", False),
        has_audio=multimodal.get("has_audio", False),
        has_game_state=multimodal.get("has_game_state", False),
        depth_alignment=multimodal.get("depth_alignment", 0.0),
        audio_alignment=multimodal.get("audio_alignment", 0.0),
        game_state_alignment=multimodal.get("game_state_alignment", 0.0),
        max_points=components_cfg.get("multimodal", {}).get("max_points", 10),
    )
    antipattern = score_antipattern_penalty(
        idle_seconds=antipatterns.get("idle_seconds", 0.0),
        alt_tab_count=antipatterns.get("alt_tab_count", 0),
        pause_menu_seconds=antipatterns.get("pause_menu_seconds", 0.0),
        session_duration=session_duration,
        max_penalty=antipattern_cfg.get("max_penalty", 10),
    )

    critical_failure = has_critical_failure(session_data)

    # Composite score
    composite = (
        audit_norm
        + action_diversity
        + camera_motion
        + failure_recovery
        + label_density
        + multimodal_score
        + antipattern
    )
    composite = round(max(0.0, min(100.0, composite)), 4)
    if critical_failure:
        composite = min(composite, CRITICAL_FAILURE_SCORE_CAP)

    return {
        "composite_score": composite,
        "critical_failure": critical_failure,
        "components": {
            "audit_norm": audit_norm,
            "action_diversity": action_diversity,
            "camera_motion": camera_motion,
            "failure_recovery": failure_recovery,
            "label_density": label_density,
            "multimodal": multimodal_score,
            "antipattern": antipattern,
        },
        "session_duration": session_duration,
    }


def compute_percentile_rank(
    scores: List[float],
    target_score: float,
) -> float:
    """
    Compute percentile rank of target_score within a list of scores.
    Returns value in [0, 1].
    """
    if not scores:
        return 0.0
    sorted_scores = sorted(scores)
    n = len(sorted_scores)
    # Count how many scores are strictly below target
    below = sum(1 for s in sorted_scores if s < target_score)
    # Percentile: fraction of scores below target
    return round(below / n, 4)


def score_session_with_percentile(
    session_data: Dict[str, Any],
    batch_scores: Optional[List[float]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Score a session and optionally compute its percentile rank within a batch.
    """
    result = compute_quality_score(session_data, config)

    if batch_scores is not None:
        all_scores = batch_scores + [result["composite_score"]]
        result["rank_percentile_in_batch"] = compute_percentile_rank(
            all_scores, result["composite_score"]
        )
    else:
        result["rank_percentile_in_batch"] = None

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    """
    CLI: score a session from a JSON input file.

    Usage:
        python bin/quality_scorer.py <session_data.json>
            [--batch <batch_scores.json>] [--config <config.yaml>] [--output <output.json>]
    """
    import argparse

    parser = argparse.ArgumentParser(description="Trajectory quality scorer")
    parser.add_argument("session_file", help="Path to session data JSON")
    parser.add_argument("--batch", help="Path to batch scores JSON (list of floats)")
    parser.add_argument("--config", help="Path to quality weights YAML config")
    parser.add_argument("--output", default="quality_score.json", help="Output JSON path")
    args = parser.parse_args()

    # Load session data
    with open(args.session_file, "r") as f:
        session_data = json.load(f)

    # Load config
    config = load_weights(args.config) if args.config else load_weights()

    # Load batch scores if provided
    batch_scores = None
    if args.batch:
        with open(args.batch, "r") as f:
            batch_scores = json.load(f)

    # Compute score
    result = score_session_with_percentile(session_data, batch_scores, config)

    # Write output
    output_path = args.output
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Quality score written to {output_path}")
    print(f"  Composite: {result['composite_score']}")
    print(f"  Components: {json.dumps(result['components'], indent=4)}")
    if result.get("rank_percentile_in_batch") is not None:
        print(f"  Percentile: {result['rank_percentile_in_batch']}")


if __name__ == "__main__":
    main()
