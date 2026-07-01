#!/usr/bin/env python3
"""
Tests for bin/autoresearch_clip_density.py

Coverage:
- compute_clip_density: empty inputs, mismatched lengths, zero count, zero centroid, normal cases
- load_embeddings_from_json: valid JSON, empty data
- load_embeddings_from_csv: valid CSV, empty data
- main CLI: various arguments
"""

import json
import tempfile
from pathlib import Path

import pytest

import bin.autoresearch_clip_density as clip_density


class TestComputeClipDensity:
    """Tests for compute_clip_density function."""

    def test_empty_inputs(self):
        """Test with empty embeddings and labels returns default scene types."""
        result = clip_density.compute_clip_density([], [])
        # Returns default scene types with zero values
        assert "combat" in result
        assert "build" in result
        assert "explore" in result
        assert result["combat"]["count"] == 0

    def test_mismatched_lengths_raises(self):
        """Test that mismatched embeddings/labels raises ValueError."""
        embeddings = [[1.0, 2.0], [3.0, 4.0]]
        labels = ["combat"]
        with pytest.raises(ValueError, match="length must match"):
            clip_density.compute_clip_density(embeddings, labels)

    def test_zero_count_scene_type(self):
        """Test scene type with zero count returns zeros."""
        embeddings = [[1.0, 0.0], [0.0, 1.0]]
        labels = ["combat", "combat"]
        result = clip_density.compute_clip_density(
            embeddings, labels, scene_types=("combat", "build", "explore")
        )
        assert result["build"]["count"] == 0
        assert result["build"]["mean_density"] == 0.0
        assert result["build"]["diversity_score"] == 0.0

    def test_zero_centroid(self):
        """Test that zero vector centroid returns zeros."""
        # All zeros embeddings
        embeddings = [[0.0, 0.0], [0.0, 0.0]]
        labels = ["combat", "combat"]
        result = clip_density.compute_clip_density(embeddings, labels)
        assert result["combat"]["mean_density"] == 0.0

    def test_single_embedding(self):
        """Test with single embedding returns identity similarity."""
        embeddings = [[1.0, 0.0]]
        labels = ["combat"]
        result = clip_density.compute_clip_density(embeddings, labels)
        # Single vector has variance 0, so diversity_score = 1.0
        assert result["combat"]["count"] == 1
        assert result["combat"]["diversity_score"] == 1.0

    def test_multiple_scene_types(self):
        """Test multiple scene types with actual data."""
        embeddings = [
            [1.0, 0.0],  # combat
            [0.9, 0.1],  # combat
            [0.0, 1.0],  # build
            [0.1, 0.9],  # build
            [0.5, 0.5],  # explore
        ]
        labels = ["combat", "combat", "build", "build", "explore"]
        result = clip_density.compute_clip_density(
            embeddings, labels, scene_types=("combat", "build", "explore")
        )
        assert result["combat"]["count"] == 2
        assert result["build"]["count"] == 2
        assert result["explore"]["count"] == 1

    def test_diversity_score_bounds(self):
        """Test that diversity_score is always in [0, 1]."""
        # High variance case
        embeddings = [[1.0, 0.0], [-1.0, 0.0], [0.0, 1.0], [0.0, -1.0]]
        labels = ["combat", "combat", "combat", "combat"]
        result = clip_density.compute_clip_density(embeddings, labels)
        assert 0.0 <= result["combat"]["diversity_score"] <= 1.0


class TestLoadEmbeddingsFromJson:
    """Tests for load_embeddings_from_json function."""

    def test_valid_json(self):
        """Test loading valid JSON with embeddings and labels."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "embeddings": [[1.0, 2.0], [3.0, 4.0]],
                    "labels": ["combat", "build"],
                },
                f,
            )
            f.flush()
            embeddings, labels = clip_density.load_embeddings_from_json(f.name)
            assert embeddings == [[1.0, 2.0], [3.0, 4.0]]
            assert labels == ["combat", "build"]
            Path(f.name).unlink()

    def test_empty_data(self):
        """Test loading JSON with empty arrays."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"embeddings": [], "labels": []}, f)
            f.flush()
            embeddings, labels = clip_density.load_embeddings_from_json(f.name)
            assert embeddings == []
            assert labels == []
            Path(f.name).unlink()


class TestLoadEmbeddingsFromCsv:
    """Tests for load_embeddings_from_csv function."""

    def test_valid_csv(self):
        """Test loading valid CSV."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("label,dim0,dim1\n")
            f.write("combat,1.0,2.0\n")
            f.write("build,3.0,4.0\n")
            f.flush()
            embeddings, labels = clip_density.load_embeddings_from_csv(f.name)
            assert embeddings == [[1.0, 2.0], [3.0, 4.0]]
            assert labels == ["combat", "build"]
            Path(f.name).unlink()

    def test_empty_csv(self):
        """Test loading CSV with only header."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("label,dim0,dim1\n")
            f.flush()
            embeddings, labels = clip_density.load_embeddings_from_csv(f.name)
            assert embeddings == []
            assert labels == []
            Path(f.name).unlink()


class TestMainCLI:
    """Tests for main CLI entry point."""

    def test_main_json_input(self):
        """Test CLI with JSON input file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "embeddings": [[1.0, 0.0], [0.0, 1.0]],
                    "labels": ["combat", "build"],
                },
                f,
            )
            f.flush()
            import sys
            from io import StringIO

            old_argv = sys.argv
            old_stdout = sys.stdout
            try:
                sys.argv = ["clip_density", "--input", f.name]
                sys.stdout = StringIO()
                clip_density.main()
                output = sys.stdout.getvalue()
                assert "combat" in output
                assert "build" in output
            finally:
                sys.argv = old_argv
                sys.stdout = old_stdout
                Path(f.name).unlink()

    def test_main_missing_input(self):
        """Test CLI with missing input file returns 1."""
        result = clip_density.main(["--input", "/nonexistent/file.json"])
        assert result == 1
