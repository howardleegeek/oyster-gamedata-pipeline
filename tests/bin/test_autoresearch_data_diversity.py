"""Tests for bin/autoresearch_data_diversity.py"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import bin.autoresearch_data_diversity as diversity


class TestNormalize:
    """Tests for _normalize function."""

    def test_normalize_string(self):
        assert diversity._normalize("  Hello  ") == "hello"

    def test_normalize_int(self):
        assert diversity._normalize(42) == "42"

    def test_normalize_float(self):
        assert diversity._normalize(3.14) == "3.14"

    def test_normalize_none(self):
        assert diversity._normalize(None) == "none"

    def test_normalize_empty_string(self):
        assert diversity._normalize("") == ""


class TestComputeDistribution:
    """Tests for compute_distribution function."""

    def test_single_category(self):
        records = [{"biome": "forest"}, {"biome": "forest"}, {"biome": "forest"}]
        result = diversity.compute_distribution(records, "biome", per_k=1000)
        assert result["forest"] == 1000.0

    def test_multiple_categories(self):
        records = [
            {"biome": "forest"},
            {"biome": "forest"},
            {"biome": "desert"},
        ]
        result = diversity.compute_distribution(records, "biome", per_k=1000)
        assert result["forest"] == pytest.approx(666.67, rel=0.01)
        assert result["desert"] == pytest.approx(333.33, rel=0.01)

    def test_case_insensitive(self):
        records = [{"biome": "Forest"}, {"biome": "FOREST"}, {"biome": "desert"}]
        result = diversity.compute_distribution(records, "biome", per_k=1000)
        assert result["forest"] == pytest.approx(666.67, rel=0.01)
        assert result["desert"] == pytest.approx(333.33, rel=0.01)

    def test_whitespace_stripped(self):
        records = [{"biome": " forest "}, {"biome": "forest"}, {"biome": "desert"}]
        result = diversity.compute_distribution(records, "biome", per_k=1000)
        assert result["forest"] == pytest.approx(666.67, rel=0.01)

    def test_missing_field(self):
        records = [{"biome": "forest"}, {"other": "value"}]
        result = diversity.compute_distribution(records, "biome", per_k=1000)
        assert result["forest"] == 1000.0

    def test_custom_per_k(self):
        records = [{"biome": "forest"}, {"biome": "forest"}, {"biome": "desert"}]
        result = diversity.compute_distribution(records, "biome", per_k=100)
        assert result["forest"] == pytest.approx(66.67, rel=0.01)
        assert result["desert"] == pytest.approx(33.33, rel=0.01)


class TestFlagUndersampled:
    """Tests for flag_undersampled function."""

    def test_all_above_threshold(self):
        dist = {"forest": 50.0, "desert": 30.0, "ocean": 20.0}
        result = diversity.flag_undersampled(dist, threshold=0.02, per_k=1000)
        assert result == []

    def test_some_below_threshold(self):
        dist = {"forest": 50.0, "desert": 1.0, "ocean": 20.0}
        result = diversity.flag_undersampled(dist, threshold=0.02, per_k=1000)
        assert result == ["desert"]

    def test_all_below_threshold(self):
        dist = {"forest": 1.0, "desert": 1.0}
        result = diversity.flag_undersampled(dist, threshold=0.02, per_k=1000)
        assert result == ["desert", "forest"]

    def test_sorted_output(self):
        dist = {"zebra": 1.0, "alpha": 1.0, "middle": 1.0}
        result = diversity.flag_undersampled(dist, threshold=0.02, per_k=1000)
        assert result == ["alpha", "middle", "zebra"]

    def test_custom_threshold(self):
        # threshold=0.10 means < 10% of per_k = < 100
        # Both forest (50) and desert (5) are below 100
        dist = {"forest": 50.0, "desert": 5.0}
        result = diversity.flag_undersampled(dist, threshold=0.10, per_k=1000)
        assert result == ["desert", "forest"]


class TestLoadCsv:
    """Tests for _load_csv function."""

    def test_load_simple_csv(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write("biome,time_of_day\n")
            f.write("forest,morning\n")
            f.write("desert,evening\n")
            path = Path(f.name)

        try:
            result = diversity._load_csv(path)
            assert len(result) == 2
            assert result[0]["biome"] == "forest"
            assert result[1]["time_of_day"] == "evening"
        finally:
            path.unlink()

    def test_load_csv_with_quotes(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write('biome,notes\n')
            f.write('forest,"has trees"\n')
            path = Path(f.name)

        try:
            result = diversity._load_csv(path)
            assert result[0]["notes"] == "has trees"
        finally:
            path.unlink()


class TestLoadJson:
    """Tests for _load_json function."""

    def test_load_json_list(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump([{"biome": "forest"}, {"biome": "desert"}], f)
            path = Path(f.name)

        try:
            result = diversity._load_json(path)
            assert len(result) == 2
            assert result[0]["biome"] == "forest"
        finally:
            path.unlink()

    def test_load_json_with_clips_key(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(
                {"clips": [{"biome": "forest"}, {"biome": "desert"}]}, f
            )
            path = Path(f.name)

        try:
            result = diversity._load_json(path)
            assert len(result) == 2
            assert result[0]["biome"] == "forest"
        finally:
            path.unlink()

    def test_load_json_invalid(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"other": "value"}, f)
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="JSON must be a list"):
                diversity._load_json(path)
        finally:
            path.unlink()


class TestLoadYaml:
    """Tests for _load_yaml function."""

    def test_load_yaml_list(self):
        with mock.patch.object(diversity, "yaml") as mock_yaml:
            mock_yaml.safe_load.return_value = [{"biome": "forest"}, {"biome": "desert"}]
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                f.write("dummy")  # Content doesn't matter, mocked
                path = Path(f.name)

            try:
                result = diversity._load_yaml(path)
                assert len(result) == 2
                assert result[0]["biome"] == "forest"
            finally:
                path.unlink()

    def test_load_yaml_with_clips_key(self):
        with mock.patch.object(diversity, "yaml") as mock_yaml:
            mock_yaml.safe_load.return_value = {
                "clips": [{"biome": "forest"}, {"biome": "desert"}]
            }
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                f.write("dummy")
                path = Path(f.name)

            try:
                result = diversity._load_yaml(path)
                assert len(result) == 2
                assert result[0]["biome"] == "forest"
            finally:
                path.unlink()

    def test_load_yaml_missing_yaml(self):
        with mock.patch.object(diversity, "yaml", None):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                f.write("dummy")
                path = Path(f.name)

            try:
                with pytest.raises(RuntimeError, match="PyYAML is required"):
                    diversity._load_yaml(path)
            finally:
                path.unlink()


class TestLoadRecords:
    """Tests for load_records auto-detection."""

    def test_load_csv(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write("biome\n")
            f.write("forest\n")
            path = Path(f.name)

        try:
            result = diversity.load_records(path)
            assert len(result) == 1
            assert result[0]["biome"] == "forest"
        finally:
            path.unlink()

    def test_load_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump([{"biome": "forest"}], f)
            path = Path(f.name)

        try:
            result = diversity.load_records(path)
            assert len(result) == 1
            assert result[0]["biome"] == "forest"
        finally:
            path.unlink()

    def test_load_yaml(self):
        with mock.patch.object(diversity, "yaml") as mock_yaml:
            mock_yaml.safe_load.return_value = [{"biome": "forest"}]
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                f.write("dummy")
                path = Path(f.name)

            try:
                result = diversity.load_records(path)
                assert len(result) == 1
                assert result[0]["biome"] == "forest"
            finally:
                path.unlink()

    def test_unsupported_extension(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write("dummy")
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="Unsupported file extension"):
                diversity.load_records(path)
        finally:
            path.unlink()


class TestBuildParser:
    """Tests for build_parser function."""

    def test_default_values(self):
        parser = diversity.build_parser()
        args = parser.parse_args(["metadata.csv"])
        assert args.metadata == Path("metadata.csv")
        assert args.biome_col == "biome"
        assert args.tod_col == "time_of_day"
        assert args.weather_col == "weather"
        assert args.per_k == 1000
        assert args.threshold == 0.02
        assert args.json_output is None

    def test_custom_columns(self):
        parser = diversity.build_parser()
        args = parser.parse_args([
            "metadata.csv",
            "--biome-col", "habitat",
            "--tod-col", "time",
            "--weather-col", "conditions",
        ])
        assert args.biome_col == "habitat"
        assert args.tod_col == "time"
        assert args.weather_col == "conditions"

    def test_custom_threshold_and_per_k(self):
        parser = diversity.build_parser()
        args = parser.parse_args([
            "metadata.csv",
            "--threshold", "0.05",
            "--per-k", "500",
        ])
        assert args.threshold == 0.05
        assert args.per_k == 500

    def test_json_output(self):
        parser = diversity.build_parser()
        args = parser.parse_args([
            "metadata.csv",
            "--json-output", "report.json",
        ])
        assert args.json_output == Path("report.json")


class TestMain:
    """Tests for main entry point."""

    def test_main_success(self, tmp_path):
        # Create a test CSV file
        csv_file = tmp_path / "metadata.csv"
        csv_file.write_text("biome,time_of_day,weather\nforest,morning,clear\n")

        with mock.patch.object(diversity, "print_report"):
            result = diversity.main([str(csv_file)])
            assert result == 0

    def test_main_empty_file(self, tmp_path):
        csv_file = tmp_path / "metadata.csv"
        csv_file.write_text("biome,time_of_day,weather\n")

        result = diversity.main([str(csv_file)])
        assert result == 1

    def test_main_no_records(self, tmp_path):
        csv_file = tmp_path / "metadata.csv"
        csv_file.write_text("")

        result = diversity.main([str(csv_file)])
        assert result == 1

    def test_main_json_output(self, tmp_path):
        csv_file = tmp_path / "metadata.csv"
        csv_file.write_text("biome,time_of_day,weather\nforest,morning,clear\n")

        json_output = tmp_path / "report.json"
        result = diversity.main([
            str(csv_file),
            "--json-output", str(json_output),
        ])
        assert result == 0
        assert json_output.exists()

        # Verify JSON content
        data = json.loads(json_output.read_text())
        assert "total_clips" in data
        assert "fields" in data
        assert data["total_clips"] == 1


class TestPrintReport:
    """Tests for print_report function (smoke tests)."""

    def test_print_report_with_undersampled(self, capsys):
        dist = {"forest": 50.0, "desert": 1.0}
        undersampled = ["desert"]
        diversity.print_report(dist, undersampled, "biome", 1000, 0.02)
        captured = capsys.readouterr()
        assert "UNDERSAMPLED" in captured.out
        assert "desert" in captured.out

    def test_print_report_all_sufficient(self, capsys):
        dist = {"forest": 50.0, "desert": 30.0}
        undersampled = []
        diversity.print_report(dist, undersampled, "biome", 1000, 0.02)
        captured = capsys.readouterr()
        assert "meet threshold" in captured.out
