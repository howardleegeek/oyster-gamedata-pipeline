#!/usr/bin/env python3
"""
Tests for bin/prd_test_route_type_distribution.py

PRD p5 #2: Validate route_type field contains at least 5 distinct types
across 240 clips.
"""

import json

import pytest

from bin.prd_test_route_type_distribution import (
    extract_route_types,
    load_clips,
    validate_distribution,
)


class TestLoadClips:
    """Tests for load_clips function."""

    def test_load_from_clips_file(self, tmp_path):
        """Test loading clips from a single JSON file."""
        clips_file = tmp_path / "clips.json"
        clips_data = [
            {"route_type": "exploration", "id": 1},
            {"route_type": "combat", "id": 2},
            {"route_type": "exploration", "id": 3},
        ]
        clips_file.write_text(json.dumps(clips_data))

        result = load_clips(data_dir=None, clips_file=clips_file)
        assert len(result) == 3

    def test_load_from_data_dir(self, tmp_path):
        """Test loading clips from a directory of JSON files."""
        data_dir = tmp_path / "clips"
        data_dir.mkdir()

        # Create multiple JSON files
        file1 = data_dir / "batch1.json"
        file1.write_text(json.dumps([{"route_type": "exploration", "id": 1}]))

        file2 = data_dir / "batch2.json"
        file2.write_text(json.dumps([{"route_type": "combat", "id": 2}]))

        result = load_clips(data_dir=data_dir, clips_file=None)
        assert len(result) == 2

    def test_load_merges_lists(self, tmp_path):
        """Test that loading from directory merges multiple clip lists."""
        data_dir = tmp_path / "clips"
        data_dir.mkdir()

        file1 = data_dir / "batch1.json"
        file1.write_text(json.dumps([{"route_type": "a", "id": 1}, {"route_type": "b", "id": 2}]))

        file2 = data_dir / "batch2.json"
        file2.write_text(json.dumps([{"route_type": "c", "id": 3}]))

        result = load_clips(data_dir=data_dir, clips_file=None)
        assert len(result) == 3

    def test_file_not_found_raises(self, tmp_path):
        """Test that missing directory raises FileNotFoundError."""
        nonexistent = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            load_clips(data_dir=nonexistent, clips_file=None)

    def test_invalid_json_raises(self, tmp_path):
        """Test that invalid JSON raises JSONDecodeError."""
        clips_file = tmp_path / "bad.json"
        clips_file.write_text("not valid json")

        with pytest.raises(json.JSONDecodeError):
            load_clips(data_dir=None, clips_file=clips_file)


class TestExtractRouteTypes:
    """Tests for extract_route_types function."""

    def test_extract_with_route_type(self):
        """Test extracting route_type from clips."""
        clips = [
            {"route_type": "exploration", "id": 1},
            {"route_type": "combat", "id": 2},
            {"route_type": "exploration", "id": 3},
        ]
        result = extract_route_types(clips)
        assert result == ["exploration", "combat", "exploration"]

    def test_extract_skips_missing_field(self):
        """Test that clips without route_type are skipped."""
        clips = [
            {"route_type": "exploration", "id": 1},
            {"id": 2},  # Missing route_type
            {"route_type": "combat", "id": 3},
        ]
        result = extract_route_types(clips)
        assert result == ["exploration", "combat"]

    def test_extract_empty_list(self):
        """Test extracting from empty list returns empty list."""
        result = extract_route_types([])
        assert result == []

    def test_extract_converts_to_string(self):
        """Test that route_type values are converted to strings."""
        clips = [
            {"route_type": "exploration", "id": 1},
            {"route_type": 123, "id": 2},  # Integer route_type
        ]
        result = extract_route_types(clips)
        assert result == ["exploration", "123"]


class TestValidateDistribution:
    """Tests for validate_distribution function."""

    def test_exactly_min_distinct_and_total(self):
        """Test validation with exactly minimum distinct types and total."""
        route_types = ["a", "b", "c", "d", "e"] * 48  # 240 total, 5 distinct
        success, details = validate_distribution(
            route_types, min_distinct=5, expected_total=240
        )
        assert success is True
        assert details["distinct_types"] == 5
        assert details["total_clips"] == 240

    def test_more_than_min_distinct(self):
        """Test validation passes with more than minimum distinct types."""
        route_types = ["a", "b", "c", "d", "e", "f"] * 40  # 6 distinct types
        success, details = validate_distribution(
            route_types, min_distinct=5, expected_total=240
        )
        assert success is True
        assert details["distinct_types"] == 6

    def test_fewer_than_min_distinct(self):
        """Test validation fails with fewer than minimum distinct types."""
        route_types = ["a", "b"] * 100  # Only 2 distinct
        success, details = validate_distribution(
            route_types, min_distinct=5, expected_total=240
        )
        assert success is False
        assert details["distinct_types"] == 2

    def test_below_90_percent_total(self):
        """Test validation fails when total clips below 90% of expected."""
        route_types = ["a", "b", "c", "d", "e"] * 20  # Only 100 clips, not 216 (90% of 240)
        success, details = validate_distribution(
            route_types, min_distinct=5, expected_total=240
        )
        assert success is False
        assert details["total_clips"] == 100

    def test_exactly_90_percent_total(self):
        """Test validation passes when total is exactly 90% of expected."""
        route_types = ["a", "b", "c", "d", "e"] * 43 + ["a"]  # 216 clips (exactly 90%)
        success, details = validate_distribution(
            route_types, min_distinct=5, expected_total=240
        )
        assert success is True

    def test_empty_route_types(self):
        """Test validation with empty route types list."""
        success, details = validate_distribution([], min_distinct=5, expected_total=240)
        assert success is False
        assert details["distinct_types"] == 0
        assert details["total_clips"] == 0

    def test_custom_min_distinct(self):
        """Test validation with custom min_distinct parameter."""
        route_types = ["a", "b", "c"] * 80  # 3 distinct
        success, details = validate_distribution(
            route_types, min_distinct=3, expected_total=240
        )
        assert success is True

    def test_custom_expected_total(self):
        """Test validation with custom expected_total parameter."""
        route_types = ["a", "b", "c", "d", "e"] * 18  # 90 total
        success, details = validate_distribution(
            route_types, min_distinct=5, expected_total=100
        )
        assert success is True  # 90 >= 90% of 100

    def test_distribution_details(self):
        """Test that distribution details are correctly computed."""
        route_types = ["a", "a", "b", "b", "b", "c"]
        success, details = validate_distribution(
            route_types, min_distinct=3, expected_total=6
        )
        assert details["distribution"] == {"a": 2, "b": 3, "c": 1}
        assert details["type_list"] == ["a", "b", "c"]
