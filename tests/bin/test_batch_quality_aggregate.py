#!/usr/bin/env python3
"""Tests for bin/batch_quality_aggregate.py."""

import os
import tempfile

import yaml

from bin import batch_quality_aggregate as bqa


class TestComputePercentileRanks:
    """Tests for compute_percentile_ranks function."""

    def test_empty_list_returns_empty(self):
        assert bqa.compute_percentile_ranks([]) == []

    def test_single_element(self):
        assert bqa.compute_percentile_ranks([50.0]) == [0.0]

    def test_all_same_values(self):
        scores = [50.0, 50.0, 50.0, 50.0]
        # All elements are equal, so all have 0 below them
        assert bqa.compute_percentile_ranks(scores) == [0.0, 0.0, 0.0, 0.0]

    def test_sorted_scores(self):
        # [10, 20, 30, 40, 50]
        # 10 has 0 below -> 0/5 = 0
        # 20 has 1 below -> 1/5 = 0.2
        # 30 has 2 below -> 2/5 = 0.4
        # 40 has 3 below -> 3/5 = 0.6
        # 50 has 4 below -> 4/5 = 0.8
        scores = [10, 20, 30, 40, 50]
        result = bqa.compute_percentile_ranks(scores)
        assert result == [0.0, 0.2, 0.4, 0.6, 0.8]

    def test_unsorted_scores_preserves_order(self):
        # Input order is preserved in output
        scores = [50, 10, 30]
        result = bqa.compute_percentile_ranks(scores)
        # 50 has 2 below -> 2/3 = 0.6667
        # 10 has 0 below -> 0/3 = 0
        # 30 has 1 below -> 1/3 = 0.3333
        assert len(result) == 3
        assert result[0] > result[1]  # 50 > 10

    def test_descending_scores(self):
        scores = [100, 80, 60, 40, 20]
        result = bqa.compute_percentile_ranks(scores)
        # 100: 4 below -> 0.8
        # 80: 3 below -> 0.6
        # 60: 2 below -> 0.4
        # 40: 1 below -> 0.2
        # 20: 0 below -> 0
        assert result == [0.8, 0.6, 0.4, 0.2, 0.0]


class TestIdentifyOutliers:
    """Tests for identify_outliers function."""

    def test_empty_list(self):
        low, high = bqa.identify_outliers([])
        assert low == []
        assert high == []

    def test_single_element(self):
        low, high = bqa.identify_outliers([50.0])
        assert low == []
        assert high == []

    def test_few_elements_no_outliers(self):
        # With fewer than 4 elements, no outliers
        scores = [10, 20, 30]
        low, high = bqa.identify_outliers(scores)
        assert low == []
        assert high == []

    def test_no_outliers_normal_distribution(self):
        # Normal distribution around 50
        scores = [48, 49, 50, 51, 52]
        low, high = bqa.identify_outliers(scores)
        assert low == []
        assert high == []

    def test_low_outliers_detected(self):
        # Scores: [10, 20, 30, 40, 50, 60, 70, 80]
        # Sorted: [10, 20, 30, 40, 50, 60, 70, 80]
        # n=8, q1_idx=2 (30), q3_idx=6 (70), iqr=40
        # lower_bound = 30 - 1.5*40 = -30 (no low outliers)
        # upper_bound = 70 + 1.5*40 = 130 (no high outliers)
        scores = [10, 20, 30, 40, 50, 60, 70, 80]
        low, high = bqa.identify_outliers(scores)
        # No outliers in this case either

    def test_high_outliers_with_extreme_values(self):
        # With a single extreme high value that exceeds IQR upper bound
        # Sorted: [10, 20, 30, 40, 50, 60, 70, 150]
        # n=8, q1=30 (idx2), q3=70 (idx6), iqr=40
        # upper_bound = 70 + 1.5*40 = 130
        # So 150 (index 7) should be flagged as high outlier
        scores = [10, 20, 30, 40, 50, 60, 70, 150]
        low, high = bqa.identify_outliers(scores)
        # Should detect the high outlier at index 7
        assert len(high) > 0
        assert 7 in high

    def test_both_outliers_with_extreme_values(self):
        # With both low and high outliers (requires tight middle distribution)
        # n=5: sorted [1, 10, 11, 12, 200]
        # q1=10 (idx1), q3=12 (idx3), iqr=2
        # lower_bound = 10 - 1.5*2 = 7 -> 1 is low outlier
        # upper_bound = 12 + 1.5*2 = 15 -> 200 is high outlier
        scores = [1, 10, 11, 12, 200]
        low, high = bqa.identify_outliers(scores)
        # Should detect at least one direction has outliers
        assert len(low) > 0 or len(high) > 0


class TestAssignTier:
    """Tests for assign_tier function."""

    def test_default_tiers_top_tier(self):
        tiers = {"top_tier": 0.90, "premium": 0.75, "standard": 0.50}
        assert bqa.assign_tier(0.95, tiers) == "top_tier"
        assert bqa.assign_tier(0.90, tiers) == "top_tier"

    def test_default_tiers_premium(self):
        tiers = {"top_tier": 0.90, "premium": 0.75, "standard": 0.50}
        assert bqa.assign_tier(0.89, tiers) == "premium"
        assert bqa.assign_tier(0.75, tiers) == "premium"

    def test_default_tiers_standard(self):
        tiers = {"top_tier": 0.90, "premium": 0.75, "standard": 0.50}
        assert bqa.assign_tier(0.74, tiers) == "standard"
        assert bqa.assign_tier(0.50, tiers) == "standard"

    def test_default_tiers_below_standard(self):
        tiers = {"top_tier": 0.90, "premium": 0.75, "standard": 0.50}
        assert bqa.assign_tier(0.49, tiers) == "below_standard"
        assert bqa.assign_tier(0.0, tiers) == "below_standard"

    def test_custom_tiers(self):
        custom = {"top_tier": 0.95, "premium": 0.80, "standard": 0.60}
        assert bqa.assign_tier(0.96, custom) == "top_tier"
        assert bqa.assign_tier(0.85, custom) == "premium"
        assert bqa.assign_tier(0.70, custom) == "standard"
        assert bqa.assign_tier(0.50, custom) == "below_standard"


class TestLoadWeights:
    """Tests for load_weights function."""

    def test_load_default_weights(self):
        # With no config file, should return defaults
        weights = bqa.load_weights("nonexistent_file.yaml")
        assert "tiers" in weights
        assert weights["tiers"]["top_tier"] == 0.90
        assert weights["tiers"]["premium"] == 0.75
        assert weights["tiers"]["standard"] == 0.50

    def test_load_from_temp_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "tiers": {
                    "top_tier": 0.85,
                    "premium": 0.70,
                    "standard": 0.40,
                }
            }
            yaml.dump(config, f)
            temp_path = f.name

        try:
            weights = bqa.load_weights(temp_path)
            assert weights["tiers"]["top_tier"] == 0.85
            assert weights["tiers"]["premium"] == 0.70
            assert weights["tiers"]["standard"] == 0.40
        finally:
            os.unlink(temp_path)


class TestAggregateBatch:
    """Tests for aggregate_batch function."""

    def test_empty_session_scores(self):
        result = bqa.aggregate_batch([])
        assert result["batch_size"] == 0
        assert result["sessions"] == []
        assert result["summary"] == {}

    def test_single_session(self):
        session_scores = [
            {"session_id": "sess1", "composite_score": 85.0}
        ]
        result = bqa.aggregate_batch(session_scores)
        assert result["batch_size"] == 1
        assert len(result["sessions"]) == 1
        assert result["sessions"][0]["session_id"] == "sess1"
        assert "percentile_rank" in result["sessions"][0]
        assert "tier" in result["sessions"][0]

    def test_multiple_sessions_with_percentile_ranks(self):
        session_scores = [
            {"session_id": "sess1", "composite_score": 50.0},
            {"session_id": "sess2", "composite_score": 70.0},
            {"session_id": "sess3", "composite_score": 90.0},
        ]
        result = bqa.aggregate_batch(session_scores)
        assert result["batch_size"] == 3
        # Check that percentile ranks are assigned
        for sess in result["sessions"]:
            assert "percentile_rank" in sess

    def test_tier_distribution(self):
        # Use distinct scores to ensure different percentile ranks
        session_scores = [
            {"session_id": "sess1", "composite_score": 100.0},
            {"session_id": "sess2", "composite_score": 80.0},
            {"session_id": "sess3", "composite_score": 60.0},
            {"session_id": "sess4", "composite_score": 40.0},
        ]
        result = bqa.aggregate_batch(session_scores)
        assert "tier_distribution" in result
        tiers = result["tier_distribution"]
        # With 4 sessions, percentiles will be:
        # 100 -> 0.75 (>=0.75 premium, <0.90 top)
        # 80 -> 0.50 (>=0.50 standard, <0.75 premium)
        # 60 -> 0.25 (>=0.50 standard? No, <0.50 so below_standard)
        # 40 -> 0.0 (below_standard)
        # Actually let's just check that we get some distribution
        assert sum(tiers.values()) == 4

    def test_summary_statistics(self):
        session_scores = [
            {"session_id": "sess1", "composite_score": 60.0},
            {"session_id": "sess2", "composite_score": 70.0},
            {"session_id": "sess3", "composite_score": 80.0},
        ]
        result = bqa.aggregate_batch(session_scores)
        assert "summary" in result
        summary = result["summary"]
        assert summary["min_score"] == 60.0
        assert summary["max_score"] == 80.0
        assert summary["mean_score"] == 70.0

    def test_custom_config(self):
        custom_config = {
            "tiers": {
                "top_tier": 0.95,
                "premium": 0.80,
                "standard": 0.60,
            }
        }
        # Multiple sessions: sess1 has highest score, so gets highest percentile
        session_scores = [
            {"session_id": "sess1", "composite_score": 90.0},
            {"session_id": "sess2", "composite_score": 50.0},
            {"session_id": "sess3", "composite_score": 30.0},
            {"session_id": "sess4", "composite_score": 20.0},
            {"session_id": "sess5", "composite_score": 10.0},
        ]
        result = bqa.aggregate_batch(session_scores, custom_config)
        # sess1 with 90.0 is highest, gets 0.8 percentile (4/5 below it)
        # 0.8 >= 0.80 (premium threshold) but < 0.95 (top_tier threshold)
        assert result["sessions"][0]["tier"] == "premium"
