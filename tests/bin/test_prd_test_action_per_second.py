#!/usr/bin/env python3
"""
Tests for bin/prd_test_action_per_second.py

PRD p6 #6: Validate median actions-per-second is within 0.5 to 5.0 range.
"""

import json
import tempfile
from pathlib import Path

import pytest

from bin.prd_test_action_per_second import (
    analyze_capture_quality,
    calculate_median_actions_per_second,
    is_quality_acceptable,
    load_actions_from_file,
)


class TestCalculateMedianActionsPerSecond:
    """Tests for calculate_median_actions_per_second function."""

    def test_odd_number_of_values(self):
        """Test median calculation with odd number of values."""
        actions = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = calculate_median_actions_per_second(actions)
        assert result == 3.0

    def test_even_number_of_values(self):
        """Test median calculation with even number of values."""
        actions = [1.0, 2.0, 3.0, 4.0]
        result = calculate_median_actions_per_second(actions)
        assert result == 2.5

    def test_single_value(self):
        """Test median with single value."""
        actions = [2.5]
        result = calculate_median_actions_per_second(actions)
        assert result == 2.5

    def test_empty_list_raises_error(self):
        """Test that empty list raises ValueError."""
        with pytest.raises(ValueError, match="Actions list cannot be empty"):
            calculate_median_actions_per_second([])

    def test_float_values(self):
        """Test with float values."""
        actions = [0.5, 1.5, 2.5, 3.5, 4.5]
        result = calculate_median_actions_per_second(actions)
        assert result == 2.5


class TestIsQualityAcceptable:
    """Tests for is_quality_acceptable function."""

    def test_boundary_min_acceptable(self):
        """Test min boundary (0.5) is acceptable."""
        assert is_quality_acceptable(0.5) is True

    def test_boundary_max_acceptable(self):
        """Test max boundary (5.0) is acceptable."""
        assert is_quality_acceptable(5.0) is True

    def test_within_range(self):
        """Test value within range is acceptable."""
        assert is_quality_acceptable(2.5) is True
        assert is_quality_acceptable(1.0) is True
        assert is_quality_acceptable(4.0) is True

    def test_below_range(self):
        """Test value below range is not acceptable."""
        assert is_quality_acceptable(0.4) is False
        assert is_quality_acceptable(0.0) is False
        assert is_quality_acceptable(-1.0) is False

    def test_above_range(self):
        """Test value above range is not acceptable."""
        assert is_quality_acceptable(5.1) is False
        assert is_quality_acceptable(10.0) is False


class TestAnalyzeCaptureQuality:
    """Tests for analyze_capture_quality function."""

    def test_acceptable_quality(self):
        """Test analysis with acceptable quality."""
        actions = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = analyze_capture_quality(actions)

        assert result["median_actions_per_second"] == 3.0
        assert result["min_actions_per_second"] == 1.0
        assert result["max_actions_per_second"] == 5.0
        assert result["sample_count"] == 5
        assert result["quality_status"] == "acceptable"
        assert result["in_range"] is True

    def test_low_quality_below_range(self):
        """Test analysis with low quality (below range)."""
        actions = [0.1, 0.2, 0.3]
        result = analyze_capture_quality(actions)

        assert result["median_actions_per_second"] == 0.2
        assert result["quality_status"] == "low-quality"
        assert result["in_range"] is False

    def test_low_quality_above_range(self):
        """Test analysis with low quality (above range)."""
        actions = [6.0, 7.0, 8.0]
        result = analyze_capture_quality(actions)

        assert result["median_actions_per_second"] == 7.0
        assert result["quality_status"] == "low-quality"
        assert result["in_range"] is False


class TestLoadActionsFromFile:
    """Tests for load_actions_from_file function."""

    def test_load_json_array(self):
        """Test loading from JSON array file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump([1.0, 2.0, 3.0], f)
            filepath = Path(f.name)

        try:
            result = load_actions_from_file(filepath)
            assert result == [1.0, 2.0, 3.0]
        finally:
            filepath.unlink()

    def test_load_text_file(self):
        """Test loading from plain text file (one per line)."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write("1.0\n2.0\n3.0\n")
            filepath = Path(f.name)

        try:
            result = load_actions_from_file(filepath)
            assert result == [1.0, 2.0, 3.0]
        finally:
            filepath.unlink()

    def test_load_text_file_with_empty_lines(self):
        """Test loading from text file with empty lines."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write("1.0\n\n2.0\n  \n3.0\n")
            filepath = Path(f.name)

        try:
            result = load_actions_from_file(filepath)
            assert result == [1.0, 2.0, 3.0]
        finally:
            filepath.unlink()

    def test_file_not_found(self):
        """Test that non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_actions_from_file(Path("/nonexistent/file.json"))

    def test_invalid_json_format(self):
        """Test that invalid JSON raises ValueError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write('{"not": "a list"}')
            filepath = Path(f.name)

        try:
            with pytest.raises(ValueError, match="JSON file must contain a list"):
                load_actions_from_file(filepath)
        finally:
            filepath.unlink()
