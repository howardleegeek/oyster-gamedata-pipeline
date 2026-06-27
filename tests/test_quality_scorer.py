#!/usr/bin/env python3
"""
Tests for the trajectory quality scorer.

Verifies:
  1. Scorer is deterministic (same input → same score)
  2. Percentile ranking is stable across batch sizes
  3. Components don't double-count (audit score not also rewarded in failure_recovery)
  4. All component scorers produce valid outputs
  5. Composite score is bounded [0, 100]
  6. Anti-pattern penalty is negative or zero
  7. Config loading works with defaults
"""

import math
import os
import sys

import pytest

# Add bin/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

from batch_quality_aggregate import (
    aggregate_batch,
)
from quality_scorer import (
    compute_percentile_rank,
    compute_quality_score,
    load_weights,
    score_action_diversity,
    score_antipattern_penalty,
    score_audit_norm,
    score_camera_motion,
    score_failure_recovery,
    score_label_density,
    score_multimodal,
    score_session_with_percentile,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def perfect_session():
    """A session with perfect quality across all components."""
    return {
        "audit_score": 105.0,
        "key_counts": {"w": 100, "a": 100, "s": 100, "d": 100},
        "bbox_volumes": [1e6, 1e6, 1e6],
        "angular_variances": [math.pi**2, math.pi**2, math.pi**2],
        "death_events": [
            {"death_time": 10, "recovery_time": 30},
            {"death_time": 100, "recovery_time": 120},
            {"death_time": 200, "recovery_time": 250},
        ],
        "labels": [{"id": i, "type": "subgoal"} for i in range(50)],
        "session_duration": 600.0,
        "multimodal": {
            "has_depth": True,
            "has_audio": True,
            "has_game_state": True,
            "depth_alignment": 1.0,
            "audio_alignment": 1.0,
            "game_state_alignment": 1.0,
        },
        "antipatterns": {
            "idle_seconds": 0,
            "alt_tab_count": 0,
            "pause_menu_seconds": 0,
        },
    }


@pytest.fixture
def poor_session():
    """A session with poor quality across all components."""
    return {
        "audit_score": 0.0,
        "key_counts": {"w": 100, "a": 0, "s": 0, "d": 0},
        "bbox_volumes": [0, 0, 0],
        "angular_variances": [0, 0, 0],
        "death_events": [
            {"death_time": 10, "recovery_time": None},
        ],
        "labels": [],
        "session_duration": 600.0,
        "multimodal": {
            "has_depth": False,
            "has_audio": False,
            "has_game_state": False,
            "depth_alignment": 0.0,
            "audio_alignment": 0.0,
            "game_state_alignment": 0.0,
        },
        "antipatterns": {
            "idle_seconds": 300,
            "alt_tab_count": 5,
            "pause_menu_seconds": 120,
        },
    }


@pytest.fixture
def typical_session():
    """A typical session with moderate quality."""
    return {
        "audit_score": 73.5,
        "key_counts": {"w": 80, "a": 40, "s": 30, "d": 50},
        "bbox_volumes": [5e5, 3e5, 7e5],
        "angular_variances": [1.5, 2.0, 1.0],
        "death_events": [
            {"death_time": 50, "recovery_time": 80},
            {"death_time": 200, "recovery_time": 210},
        ],
        "labels": [{"id": i, "type": "keystep"} for i in range(15)],
        "session_duration": 600.0,
        "multimodal": {
            "has_depth": True,
            "has_audio": False,
            "has_game_state": True,
            "depth_alignment": 0.8,
            "audio_alignment": 0.0,
            "game_state_alignment": 0.9,
        },
        "antipatterns": {
            "idle_seconds": 30,
            "alt_tab_count": 1,
            "pause_menu_seconds": 10,
        },
    }


# ---------------------------------------------------------------------------
# Test 1: Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Verify scorer is deterministic: same input → same score."""

    def test_perfect_session_deterministic(self, perfect_session):
        result1 = compute_quality_score(perfect_session)
        result2 = compute_quality_score(perfect_session)
        assert result1["composite_score"] == result2["composite_score"]
        assert result1["components"] == result2["components"]

    def test_poor_session_deterministic(self, poor_session):
        result1 = compute_quality_score(poor_session)
        result2 = compute_quality_score(poor_session)
        assert result1["composite_score"] == result2["composite_score"]

    def test_typical_session_deterministic(self, typical_session):
        result1 = compute_quality_score(typical_session)
        result2 = compute_quality_score(typical_session)
        assert result1["composite_score"] == result2["composite_score"]

    def test_multiple_runs_identical(self, typical_session):
        scores = [compute_quality_score(typical_session)["composite_score"] for _ in range(100)]
        assert len(set(scores)) == 1, "All 100 runs should produce identical scores"

    def test_component_values_deterministic(self, typical_session):
        result1 = compute_quality_score(typical_session)
        result2 = compute_quality_score(typical_session)
        for key in result1["components"]:
            assert result1["components"][key] == result2["components"][key], (
                f"Component {key} is not deterministic"
            )


# ---------------------------------------------------------------------------
# Test 2: Percentile ranking stability
# ---------------------------------------------------------------------------


class TestPercentileStability:
    """Verify percentile ranking is stable across batch sizes."""

    def test_single_item_percentile(self):
        """Single item should be at 0th percentile (nothing below it)."""
        rank = compute_percentile_rank([50.0], 50.0)
        assert rank == 0.0

    def test_two_items_equal(self):
        """Two equal items: each at 0th percentile."""
        rank = compute_percentile_rank([50.0, 50.0], 50.0)
        assert rank == 0.0

    def test_two_items_different(self):
        """Higher item should be at 50th percentile."""
        rank = compute_percentile_rank([40.0, 60.0], 60.0)
        assert rank == 0.5

    def test_percentile_stable_across_batch_sizes(self):
        """
        Adding more items below a score should not change its relative rank
        among the original items.
        """
        base_scores = [30.0, 50.0, 70.0]
        target = 50.0

        rank_base = compute_percentile_rank(base_scores, target)

        # Add more low scores
        extended_scores = base_scores + [10.0, 20.0, 15.0]
        rank_extended = compute_percentile_rank(extended_scores, target)

        # The rank should change predictably (more items below = higher percentile)
        assert rank_extended >= rank_base

    def test_percentile_monotonic(self):
        """Higher scores should have higher or equal percentiles."""
        scores = [10.0, 20.0, 30.0, 40.0, 50.0]
        ranks = [compute_percentile_rank(scores, s) for s in scores]
        for i in range(len(ranks) - 1):
            assert ranks[i] <= ranks[i + 1], (
                f"Percentile not monotonic: {ranks[i]} > {ranks[i + 1]}"
            )

    def test_batch_aggregate_percentile_consistency(self):
        """Batch aggregate should produce consistent percentile ranks."""
        sessions = [
            {"composite_score": 30.0, "session_id": "s1"},
            {"composite_score": 50.0, "session_id": "s2"},
            {"composite_score": 70.0, "session_id": "s3"},
            {"composite_score": 90.0, "session_id": "s4"},
        ]
        result = aggregate_batch(sessions)
        percentiles = [s["percentile_rank"] for s in result["sessions"]]
        # Should be monotonically increasing
        for i in range(len(percentiles) - 1):
            assert percentiles[i] <= percentiles[i + 1]


# ---------------------------------------------------------------------------
# Test 3: No double-counting
# ---------------------------------------------------------------------------


class TestNoDoubleCounting:
    """Verify components don't double-count."""

    def test_audit_not_in_failure_recovery(self):
        """
        Audit score should not also be rewarded in failure_recovery.
        A session with high audit but no recoveries should have 0 failure_recovery.
        """
        session = {
            "audit_score": 105.0,  # Perfect audit
            "key_counts": {},
            "bbox_volumes": [],
            "angular_variances": [],
            "death_events": [
                {"death_time": 10, "recovery_time": None},  # No recovery
                {"death_time": 50, "recovery_time": 200},  # Recovery > 60s
            ],
            "labels": [],
            "session_duration": 600.0,
            "multimodal": {},
            "antipatterns": {},
        }
        result = compute_quality_score(session)
        assert result["components"]["audit_norm"] > 0, "Audit norm should be high"
        assert result["components"]["failure_recovery"] == 0.0, (
            "Failure recovery should be 0 when no valid recoveries exist"
        )

    def test_audit_and_failure_recovery_independent(self):
        """
        Changing audit score should not affect failure_recovery score.
        """
        base_session = {
            "audit_score": 50.0,
            "key_counts": {},
            "bbox_volumes": [],
            "angular_variances": [],
            "death_events": [
                {"death_time": 10, "recovery_time": 40},
            ],
            "labels": [],
            "session_duration": 600.0,
            "multimodal": {},
            "antipatterns": {},
        }

        result_low_audit = compute_quality_score({**base_session, "audit_score": 10.0})
        result_high_audit = compute_quality_score({**base_session, "audit_score": 100.0})

        assert (
            result_low_audit["components"]["failure_recovery"]
            == result_high_audit["components"]["failure_recovery"]
        ), "Failure recovery should be independent of audit score"

    def test_action_diversity_independent_of_camera(self):
        """Action diversity should not depend on camera motion data."""
        session_base = {
            "audit_score": 50.0,
            "key_counts": {"w": 50, "a": 50, "s": 50, "d": 50},
            "bbox_volumes": [],
            "angular_variances": [],
            "death_events": [],
            "labels": [],
            "session_duration": 600.0,
            "multimodal": {},
            "antipatterns": {},
        }

        result_no_camera = compute_quality_score(session_base)
        result_with_camera = compute_quality_score(
            {
                **session_base,
                "bbox_volumes": [1e6],
                "angular_variances": [math.pi**2],
            }
        )

        assert (
            result_no_camera["components"]["action_diversity"]
            == result_with_camera["components"]["action_diversity"]
        ), "Action diversity should be independent of camera motion"

    def test_multimodal_independent_of_labels(self):
        """Multimodal score should not depend on label density."""
        session_base = {
            "audit_score": 50.0,
            "key_counts": {},
            "bbox_volumes": [],
            "angular_variances": [],
            "death_events": [],
            "labels": [],
            "session_duration": 600.0,
            "multimodal": {
                "has_depth": True,
                "has_audio": True,
                "has_game_state": True,
                "depth_alignment": 0.9,
                "audio_alignment": 0.9,
                "game_state_alignment": 0.9,
            },
            "antipatterns": {},
        }

        result_no_labels = compute_quality_score(session_base)
        result_with_labels = compute_quality_score(
            {
                **session_base,
                "labels": [{"id": i} for i in range(100)],
            }
        )

        assert (
            result_no_labels["components"]["multimodal"]
            == result_with_labels["components"]["multimodal"]
        ), "Multimodal should be independent of label density"


# ---------------------------------------------------------------------------
# Test 4: Component scorer validity
# ---------------------------------------------------------------------------


class TestComponentScorers:
    """Verify each component scorer produces valid outputs."""

    def test_audit_norm_bounds(self):
        assert 0 <= score_audit_norm(0) <= 30
        assert 0 <= score_audit_norm(52.5) <= 30
        assert 0 <= score_audit_norm(105) <= 30
        assert score_audit_norm(0) == 0.0
        assert score_audit_norm(105) == 30.0

    def test_audit_norm_clamping(self):
        """Audit score outside [0, 105] should be clamped."""
        assert score_audit_norm(-10) == 0.0
        assert score_audit_norm(200) == 30.0

    def test_action_diversity_bounds(self):
        assert 0 <= score_action_diversity({}) <= 10
        assert 0 <= score_action_diversity({"w": 100}) <= 10
        assert 0 <= score_action_diversity({"w": 25, "a": 25, "s": 25, "d": 25}) <= 10

    def test_action_diversity_uniform_max(self):
        """Uniform WASD distribution should give max score."""
        score = score_action_diversity({"w": 100, "a": 100, "s": 100, "d": 100})
        assert score == 10.0

    def test_action_diversity_single_key_zero(self):
        """Single key usage should give 0."""
        score = score_action_diversity({"w": 100, "a": 0, "s": 0, "d": 0})
        assert score == 0.0

    def test_camera_motion_bounds(self):
        assert 0 <= score_camera_motion([], []) <= 15
        assert 0 <= score_camera_motion([1e6], [math.pi**2]) <= 15

    def test_camera_motion_empty(self):
        assert score_camera_motion([], []) == 0.0

    def test_failure_recovery_bounds(self):
        assert 0 <= score_failure_recovery([]) <= 10
        assert 0 <= score_failure_recovery([{"death_time": 10, "recovery_time": 40}]) <= 10

    def test_failure_recovery_no_recovery(self):
        """Deaths without recovery should score 0."""
        events = [
            {"death_time": 10, "recovery_time": None},
            {"death_time": 50, "recovery_time": 200},  # > 60s
        ]
        assert score_failure_recovery(events) == 0.0

    def test_failure_recovery_max(self):
        """3+ recoveries within 60s should give max score."""
        events = [
            {"death_time": 10, "recovery_time": 30},
            {"death_time": 100, "recovery_time": 120},
            {"death_time": 200, "recovery_time": 250},
        ]
        assert score_failure_recovery(events) == 10.0

    def test_label_density_bounds(self):
        assert 0 <= score_label_density([], 600) <= 15
        assert 0 <= score_label_density([{"id": i} for i in range(50)], 600) <= 15

    def test_label_density_zero_duration(self):
        assert score_label_density([{"id": 1}], 0) == 0.0

    def test_multimodal_bounds(self):
        assert 0 <= score_multimodal() <= 10
        assert (
            0
            <= score_multimodal(
                has_depth=True,
                has_audio=True,
                has_game_state=True,
                depth_alignment=1.0,
                audio_alignment=1.0,
                game_state_alignment=1.0,
            )
            <= 10
        )

    def test_multimodal_all_present_max(self):
        score = score_multimodal(
            has_depth=True,
            has_audio=True,
            has_game_state=True,
            depth_alignment=1.0,
            audio_alignment=1.0,
            game_state_alignment=1.0,
        )
        assert score == 10.0

    def test_multimodal_none_present_zero(self):
        score = score_multimodal()
        assert score == 0.0

    def test_antipattern_penalty_negative_or_zero(self):
        penalty = score_antipattern_penalty()
        assert penalty <= 0

    def test_antipattern_penalty_max(self):
        """Heavy anti-patterns should give max penalty."""
        penalty = score_antipattern_penalty(
            idle_seconds=500,
            alt_tab_count=10,
            pause_menu_seconds=300,
            session_duration=600,
        )
        assert penalty == -10.0

    def test_antipattern_penalty_zero(self):
        """No anti-patterns should give 0 penalty."""
        penalty = score_antipattern_penalty(
            idle_seconds=0,
            alt_tab_count=0,
            pause_menu_seconds=0,
            session_duration=600,
        )
        assert penalty == 0.0


# ---------------------------------------------------------------------------
# Test 5: Composite score bounds
# ---------------------------------------------------------------------------


class TestCompositeBounds:
    """Verify composite score is bounded [0, 100]."""

    def test_perfect_session_near_max(self, perfect_session):
        result = compute_quality_score(perfect_session)
        assert 0 <= result["composite_score"] <= 100
        # Perfect session should be close to 100 (minus any rounding)
        assert result["composite_score"] >= 90

    def test_poor_session_near_min(self, poor_session):
        result = compute_quality_score(poor_session)
        assert 0 <= result["composite_score"] <= 100
        # Poor session should be close to 0
        assert result["composite_score"] <= 10

    def test_composite_never_exceeds_100(self):
        """Even with perfect inputs, composite should not exceed 100."""
        session = {
            "audit_score": 105.0,
            "key_counts": {"w": 1000, "a": 1000, "s": 1000, "d": 1000},
            "bbox_volumes": [1e9, 1e9],
            "angular_variances": [1e9, 1e9],
            "death_events": [
                {"death_time": i * 100, "recovery_time": i * 100 + 30} for i in range(10)
            ],
            "labels": [{"id": i} for i in range(1000)],
            "session_duration": 600.0,
            "multimodal": {
                "has_depth": True,
                "has_audio": True,
                "has_game_state": True,
                "depth_alignment": 1.0,
                "audio_alignment": 1.0,
                "game_state_alignment": 1.0,
            },
            "antipatterns": {"idle_seconds": 0, "alt_tab_count": 0, "pause_menu_seconds": 0},
        }
        result = compute_quality_score(session)
        assert result["composite_score"] <= 100

    def test_composite_never_below_0(self):
        """Even with worst inputs, composite should not go below 0."""
        session = {
            "audit_score": 0.0,
            "key_counts": {},
            "bbox_volumes": [],
            "angular_variances": [],
            "death_events": [],
            "labels": [],
            "session_duration": 600.0,
            "multimodal": {},
            "antipatterns": {
                "idle_seconds": 10000,
                "alt_tab_count": 100,
                "pause_menu_seconds": 10000,
            },
        }
        result = compute_quality_score(session)
        assert result["composite_score"] >= 0

    def test_critical_failure_caps_composite_score(self, perfect_session):
        """Critical audit failures must dominate rich side metrics."""
        result = compute_quality_score(
            {
                **perfect_session,
                "audit_verdict": "FAIL",
                "audit_score": 0.0,
                "critical_failures": [{"id": "B8", "status": "FAIL"}],
            }
        )

        assert result["critical_failure"] is True
        assert result["composite_score"] <= 20.0


# ---------------------------------------------------------------------------
# Test 6: Components sum sanity
# ---------------------------------------------------------------------------


class TestComponentsSum:
    """Verify components sum approximately matches composite."""

    def test_components_sum_matches_composite(self, typical_session):
        result = compute_quality_score(typical_session)
        component_sum = sum(result["components"].values())
        assert abs(component_sum - result["composite_score"]) < 0.01

    def test_components_sum_matches_composite_perfect(self, perfect_session):
        result = compute_quality_score(perfect_session)
        component_sum = sum(result["components"].values())
        assert abs(component_sum - result["composite_score"]) < 0.01


# ---------------------------------------------------------------------------
# Test 7: Config loading
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Verify config loading works."""

    def test_default_config(self):
        config = load_weights()
        assert "components" in config
        assert "anti_pattern" in config
        assert config["components"]["audit_norm"]["max_points"] == 30

    def test_custom_config(self):
        """Test loading a custom config file."""
        custom_config = {
            "components": {
                "audit_norm": {"max_points": 25},
                "action_diversity": {"max_points": 15},
                "camera_motion": {"max_points": 10},
                "failure_recovery": {"max_points": 10},
                "label_density": {"max_points": 20},
                "multimodal": {"max_points": 20},
            },
            "anti_pattern": {"max_penalty": 5},
            "tiers": {"top_tier": 0.95, "premium": 0.80, "standard": 0.60},
        }
        result = compute_quality_score(
            {
                "audit_score": 105,
                "key_counts": {},
                "bbox_volumes": [],
                "angular_variances": [],
                "death_events": [],
                "labels": [],
                "session_duration": 600,
                "multimodal": {},
                "antipatterns": {},
            },
            config=custom_config,
        )
        # With custom config, audit_norm max is 25
        assert result["components"]["audit_norm"] == 25.0


# ---------------------------------------------------------------------------
# Test 8: Batch aggregate
# ---------------------------------------------------------------------------


class TestBatchAggregate:
    """Verify batch aggregation works correctly."""

    def test_empty_batch(self):
        result = aggregate_batch([])
        assert result["batch_size"] == 0

    def test_single_session(self):
        sessions = [{"composite_score": 75.0, "session_id": "s1"}]
        result = aggregate_batch(sessions)
        assert result["batch_size"] == 1
        assert result["sessions"][0]["percentile_rank"] == 0.0

    def test_tier_assignment(self):
        sessions = [
            {"composite_score": 95.0, "session_id": "s1"},
            {"composite_score": 80.0, "session_id": "s2"},
            {"composite_score": 60.0, "session_id": "s3"},
            {"composite_score": 30.0, "session_id": "s4"},
        ]
        result = aggregate_batch(sessions)
        tiers = {s["session_id"]: s["tier"] for s in result["sessions"]}
        # With 4 sessions, percentiles are 0.0, 0.25, 0.5, 0.75
        # s4 (30.0) → 0.0 → below_standard
        # s3 (60.0) → 0.25 → below_standard
        # s2 (80.0) → 0.5 → standard
        # s1 (95.0) → 0.75 → premium
        assert tiers["s4"] == "below_standard"
        assert tiers["s3"] == "below_standard"
        assert tiers["s2"] == "standard"
        assert tiers["s1"] == "premium"

    def test_outlier_detection(self):
        sessions = [{"composite_score": 50.0, "session_id": f"s{i}"} for i in range(20)]
        # Add a clear outlier
        sessions.append({"composite_score": 5.0, "session_id": "outlier_low"})
        sessions.append({"composite_score": 99.0, "session_id": "outlier_high"})

        result = aggregate_batch(sessions)
        assert (
            "outlier_low" in result["outliers"]["low"]
            or "outlier_high" in result["outliers"]["high"]
        )

    def test_summary_statistics(self):
        sessions = [
            {"composite_score": s, "session_id": f"s{i}"}
            for i, s in enumerate([40.0, 50.0, 60.0, 70.0, 80.0])
        ]
        result = aggregate_batch(sessions)
        assert result["summary"]["mean_score"] == 60.0
        assert result["summary"]["min_score"] == 40.0
        assert result["summary"]["max_score"] == 80.0


# ---------------------------------------------------------------------------
# Test 9: Output format
# ---------------------------------------------------------------------------


class TestOutputFormat:
    """Verify output format matches spec."""

    def test_output_has_required_fields(self, typical_session):
        result = compute_quality_score(typical_session)
        assert "composite_score" in result
        assert "components" in result
        assert "audit_norm" in result["components"]
        assert "action_diversity" in result["components"]
        assert "camera_motion" in result["components"]
        assert "failure_recovery" in result["components"]
        assert "label_density" in result["components"]
        assert "multimodal" in result["components"]
        assert "antipattern" in result["components"]

    def test_output_with_percentile(self, typical_session):
        result = score_session_with_percentile(
            typical_session,
            batch_scores=[30.0, 50.0, 70.0, 90.0],
        )
        assert "rank_percentile_in_batch" in result
        assert result["rank_percentile_in_batch"] is not None

    def test_output_without_percentile(self, typical_session):
        result = score_session_with_percentile(typical_session)
        assert result["rank_percentile_in_batch"] is None


# ---------------------------------------------------------------------------
# Test 10: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_session(self):
        """Empty session data should not crash."""
        result = compute_quality_score({})
        assert 0 <= result["composite_score"] <= 100

    def test_session_with_none_values(self):
        """Session with None values should handle gracefully."""
        session = {
            "audit_score": None,
            "key_counts": None,
            "bbox_volumes": None,
            "angular_variances": None,
            "death_events": None,
            "labels": None,
            "session_duration": None,
            "multimodal": None,
            "antipatterns": None,
        }
        # Should not crash
        result = compute_quality_score(session)
        assert 0 <= result["composite_score"] <= 100

    def test_very_long_session(self):
        """Very long session should still produce valid scores."""
        session = {
            "audit_score": 50.0,
            "key_counts": {"w": 10000, "a": 10000, "s": 10000, "d": 10000},
            "bbox_volumes": [1e6] * 1000,
            "angular_variances": [math.pi**2] * 1000,
            "death_events": [],
            "labels": [{"id": i} for i in range(500)],
            "session_duration": 36000.0,  # 10 hours
            "multimodal": {
                "has_depth": True,
                "has_audio": True,
                "has_game_state": True,
                "depth_alignment": 0.5,
                "audio_alignment": 0.5,
                "game_state_alignment": 0.5,
            },
            "antipatterns": {"idle_seconds": 1000, "alt_tab_count": 2, "pause_menu_seconds": 500},
        }
        result = compute_quality_score(session)
        assert 0 <= result["composite_score"] <= 100
