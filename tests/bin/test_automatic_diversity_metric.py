#!/usr/bin/env python3
"""Tests for bin/automatic_diversity_metric.py"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from bin.automatic_diversity_metric import (
    calculate_diversity_score,
    calculate_shannon_entropy,
    extract_attributes,
    format_output,
    load_scene_data,
    normalize_entropy,
    validate_input_file,
)


class TestCalculateShannonEntropy:
    """Tests for calculate_shannon_entropy function."""

    def test_empty_list_returns_zero(self):
        """Empty input should return 0.0 entropy."""
        result = calculate_shannon_entropy([])
        assert result == 0.0

    def test_single_value_returns_zero(self):
        """Single value (100% same) should return 0.0 entropy."""
        result = calculate_shannon_entropy(["forest", "forest", "forest"])
        assert result == 0.0

    def test_two_unique_values_equal_distribution(self):
        """Two equally distributed values should return max entropy for 2 categories."""
        result = calculate_shannon_entropy(["forest", "desert", "forest", "desert"])
        # log2(2) = 1.0, equal distribution = 1.0
        assert abs(result - 1.0) < 0.0001

    def test_three_unique_values_equal_distribution(self):
        """Three equally distributed values."""
        values = ["forest", "desert", "ocean"] * 3
        result = calculate_shannon_entropy(values)
        # log2(3) ≈ 1.585
        assert abs(result - 1.5849625) < 0.0001

    def test_uneven_distribution(self):
        """Uneven distribution should have lower entropy."""
        # 3 forest, 1 desert
        result = calculate_shannon_entropy(["forest", "forest", "forest", "desert"])
        # P(forest)=0.75, P(desert)=0.25
        # H = -0.75*log2(0.75) - 0.25*log2(0.25) ≈ 0.811
        assert 0.8 < result < 0.82

    def test_all_unique_values(self):
        """All unique values should return max entropy."""
        values = ["a", "b", "c", "d", "e"]
        result = calculate_shannon_entropy(values)
        # log2(5) ≈ 2.322
        assert abs(result - 2.321928) < 0.0001


class TestNormalizeEntropy:
    """Tests for normalize_entropy function."""

    def test_single_category_returns_zero(self):
        """Single category should return 0.0 (no diversity possible)."""
        result = normalize_entropy(1.5, 1)
        assert result == 0.0

    def test_two_categories_max_entropy(self):
        """Max entropy with 2 categories normalizes to 1.0."""
        result = normalize_entropy(1.0, 2)
        assert result == 1.0

    def test_zero_entropy_returns_zero(self):
        """Zero entropy normalizes to 0.0."""
        result = normalize_entropy(0.0, 5)
        assert result == 0.0

    def test_partial_entropy(self):
        """Partial entropy normalizes correctly."""
        # Half of max entropy for 4 categories (max = 2.0)
        result = normalize_entropy(1.0, 4)
        assert result == 0.5


class TestCalculateDiversityScore:
    """Tests for calculate_diversity_score function."""

    def test_all_same_biome_time_weather(self):
        """All same values should give 0.0 aggregate score."""
        biome = ["forest"] * 10
        time_of_day = ["day"] * 10
        weather = ["sunny"] * 10

        result = calculate_diversity_score(biome, time_of_day, weather)

        assert result["aggregate_diversity_score"] == 0.0
        assert result["biome_entropy_normalized"] == 0.0
        assert result["time_entropy_normalized"] == 0.0
        assert result["weather_entropy_normalized"] == 0.0

    def test_maximum_diversity(self):
        """Maximum diversity across all categories."""
        biome = ["forest", "desert", "ocean", "taiga"] * 3
        time_of_day = ["dawn", "day", "dusk", "night"] * 3
        weather = ["sunny", "rainy", "snowy", "foggy"] * 3

        result = calculate_diversity_score(biome, time_of_day, weather)

        assert result["aggregate_diversity_score"] == 1.0
        assert result["biome_entropy_normalized"] == 1.0
        assert result["time_entropy_normalized"] == 1.0
        assert result["weather_entropy_normalized"] == 1.0
        assert result["biome_unique_count"] == 4
        assert result["time_unique_count"] == 4
        assert result["weather_unique_count"] == 4

    def test_partial_diversity(self):
        """Partial diversity in each category."""
        biome = ["forest", "forest", "desert"]  # 2/3 forest, 1/3 desert
        time_of_day = ["day", "day", "night", "night"]  # 50/50
        weather = ["sunny"] * 4  # no diversity

        result = calculate_diversity_score(biome, time_of_day, weather)

        # Weather has no diversity
        assert result["weather_entropy_normalized"] == 0.0
        # Time has max diversity for 2 categories
        assert result["time_entropy_normalized"] == 1.0
        # Biome has some diversity
        assert 0 < result["biome_entropy_normalized"] < 1.0
        # Aggregate should be less than 1.0 due to weather
        assert result["aggregate_diversity_score"] < 1.0


class TestLoadSceneData:
    """Tests for load_scene_data function."""

    def test_load_json_list(self):
        """Load JSON file with list of scenes."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(
                [
                    {"biome": "forest", "time_of_day": "day", "weather": "sunny"},
                    {"biome": "desert", "time_of_day": "night", "weather": "rainy"},
                ],
                f,
            )
            f.flush()
            scenes = load_scene_data(Path(f.name))

        assert len(scenes) == 2
        assert scenes[0]["biome"] == "forest"

    def test_load_json_dict_with_scenes(self):
        """Load JSON file with scenes wrapper."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(
                {
                    "scenes": [
                        {"biome": "forest"},
                        {"biome": "desert"},
                    ]
                },
                f,
            )
            f.flush()
            scenes = load_scene_data(Path(f.name))

        assert len(scenes) == 2

    def test_load_json_dict_with_cohort(self):
        """Load JSON file with cohort wrapper."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(
                {
                    "cohort": [
                        {"biome": "forest"},
                    ]
                },
                f,
            )
            f.flush()
            scenes = load_scene_data(Path(f.name))

        assert len(scenes) == 1

    def test_load_json_single_object(self):
        """Load JSON file with single scene object."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"biome": "forest", "time_of_day": "day"}, f)
            f.flush()
            scenes = load_scene_data(Path(f.name))

        assert len(scenes) == 1

    def test_load_yaml_list(self):
        """Load YAML file with list of scenes."""
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not available")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(
                "- biome: forest\n  time_of_day: day\n  weather: sunny\n- biome: desert\n  time_of_day: night\n"
            )
            f.flush()
            scenes = load_scene_data(Path(f.name))

        assert len(scenes) == 2
        assert scenes[0]["biome"] == "forest"

    def test_unsupported_format_raises(self):
        """Unsupported file format should raise ValueError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False
        ) as f:
            f.write("<xml>not valid</xml>")
            f.flush()
            with pytest.raises(ValueError, match="Unsupported file format"):
                load_scene_data(Path(f.name))

    def test_invalid_json_raises(self):
        """Invalid JSON should raise JSONDecodeError (not caught in load_scene_data)."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{invalid json}")
            f.flush()
            with pytest.raises(json.JSONDecodeError):
                load_scene_data(Path(f.name))


class TestExtractAttributes:
    """Tests for extract_attributes function."""

    def test_extract_standard_fields(self):
        """Extract standard biome, time_of_day, weather fields."""
        scenes = [
            {"biome": "forest", "time_of_day": "day", "weather": "sunny"},
            {"biome": "desert", "time_of_day": "night", "weather": "rainy"},
        ]

        biome, time_of_day, weather = extract_attributes(scenes)

        assert biome == ["forest", "desert"]
        assert time_of_day == ["day", "night"]
        assert weather == ["sunny", "rainy"]

    def test_extract_alternative_field_names(self):
        """Extract using alternative field names."""
        scenes = [
            {
                "environment": "forest",
                "time": "day",
                "conditions": "sunny",
            },
            {
                "environment": "desert",
                "time": "night",
                "conditions": "rainy",
            },
        ]

        biome, time_of_day, weather = extract_attributes(scenes)

        assert biome == ["forest", "desert"]
        assert time_of_day == ["day", "night"]
        assert weather == ["sunny", "rainy"]

    def test_extract_mixed_field_names(self):
        """Extract with mixed field name conventions."""
        scenes = [
            {"biome": "forest", "time_of_day": "day", "weather_condition": "sunny"},
            {"environment": "desert", "tod": "night", "conditions": "rainy"},
        ]

        biome, time_of_day, weather = extract_attributes(scenes)

        assert biome == ["forest", "desert"]
        assert time_of_day == ["day", "night"]
        assert weather == ["sunny", "rainy"]

    def test_missing_fields_defaults_to_unknown(self):
        """Missing fields default to 'unknown'."""
        scenes = [
            {"biome": "forest"},  # missing time and weather
            {"time_of_day": "day"},  # missing biome and weather
        ]

        biome, time_of_day, weather = extract_attributes(scenes)

        assert biome == ["forest", "unknown"]
        assert time_of_day == ["unknown", "day"]
        assert weather == ["unknown", "unknown"]


class TestFormatOutput:
    """Tests for format_output function."""

    def test_format_json(self):
        """Format as JSON."""
        scores = {
            "biome_entropy_raw": 1.5,
            "biome_entropy_normalized": 0.75,
            "biome_unique_count": 2,
            "time_entropy_raw": 1.0,
            "time_entropy_normalized": 1.0,
            "time_unique_count": 2,
            "weather_entropy_raw": 0.0,
            "weather_entropy_normalized": 0.0,
            "weather_unique_count": 1,
            "aggregate_diversity_score": 0.5833,
        }

        result = format_output(scores, "json")

        parsed = json.loads(result)
        assert parsed["aggregate_diversity_score"] == 0.5833

    def test_format_csv_without_details(self):
        """Format CSV without details (aggregate only)."""
        scores = {"aggregate_diversity_score": 0.75}

        result = format_output(scores, "csv", include_details=False)

        assert result == "aggregate_diversity_score\n0.75"

    def test_format_csv_with_details(self):
        """Format CSV with details."""
        scores = {
            "biome_entropy_raw": 1.5,
            "aggregate_diversity_score": 0.75,
        }

        result = format_output(scores, "csv", include_details=True)

        lines = result.split("\n")
        assert lines[0].startswith("biome_entropy_raw")
        assert lines[1].startswith("1.5")

    def test_format_text(self):
        """Format as text report."""
        scores = {
            "biome_entropy_raw": 1.0,
            "biome_entropy_normalized": 0.5,
            "biome_unique_count": 2,
            "time_entropy_raw": 1.0,
            "time_entropy_normalized": 1.0,
            "time_unique_count": 2,
            "weather_entropy_raw": 0.0,
            "weather_entropy_normalized": 0.0,
            "weather_unique_count": 1,
            "aggregate_diversity_score": 0.5,
        }

        result = format_output(scores, "text")

        assert "DIVERSITY METRIC REPORT" in result
        assert "AGGREGATE DIVERSITY SCORE: 0.5000" in result
        assert "BIOME DIVERSITY:" in result
        assert "TIME OF DAY DIVERSITY:" in result
        assert "WEATHER DIVERSITY:" in result


class TestValidateInputFile:
    """Tests for validate_input_file function."""

    def test_valid_file_passes(self):
        """Valid existing file should pass."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b"{}")
            f.flush()
            # Should not raise
            validate_input_file(Path(f.name))

    def test_nonexistent_file_raises(self):
        """Nonexistent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            validate_input_file(Path("/nonexistent/file.json"))
