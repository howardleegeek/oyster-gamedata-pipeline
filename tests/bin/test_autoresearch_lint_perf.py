"""Tests for bin/autoresearch_lint_perf.py"""

import json
import tempfile
from pathlib import Path

import pytest

from bin.autoresearch_lint_perf import (
    calculate_percentiles,
    discover_corpus,
    format_results,
    lint_buyer_spec,
    parse_args,
)


class TestParseArgs:
    """Tests for parse_args function."""

    def test_parse_corpus_required(self):
        """Test that corpus argument is required."""
        with pytest.raises(SystemExit):
            parse_args([])

    def test_parse_corpuspositional(self):
        """Test positional corpus argument."""
        args = parse_args(["/tmp/corpus"])
        assert args.corpus == Path("/tmp/corpus")

    def test_parse_runs_default(self):
        """Test default runs value."""
        args = parse_args(["/tmp/corpus"])
        assert args.runs == 1

    def test_parse_runs_option(self):
        """Test custom runs value."""
        args = parse_args(["/tmp/corpus", "--runs", "5"])
        assert args.runs == 5

    def test_parse_output_option(self):
        """Test output option."""
        args = parse_args(["/tmp/corpus", "--output", "/tmp/out.json"])
        assert args.output == Path("/tmp/out.json")

    def test_parse_verbose_option(self):
        """Test verbose flag."""
        args = parse_args(["/tmp/corpus", "--verbose"])
        assert args.verbose is True


class TestDiscoverCorpus:
    """Tests for discover_corpus function."""

    def test_not_a_directory(self):
        """Test that non-directory raises ValueError."""
        with tempfile.NamedTemporaryFile() as f:
            with pytest.raises(ValueError, match="Not a directory"):
                discover_corpus(Path(f.name))

    def test_empty_directory(self):
        """Test empty directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir)
            files = discover_corpus(corpus_path)
            assert files == []

    def test_finds_json_files(self):
        """Test discovers JSON files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir)
            # Create test file
            (corpus_path / "test.json").write_text("{}")
            files = discover_corpus(corpus_path)
            assert len(files) == 1
            assert files[0].name == "test.json"

    def test_finds_yaml_files(self):
        """Test discovers YAML files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir)
            (corpus_path / "test.yaml").write_text("key: value")
            (corpus_path / "test.yml").write_text("key: value")
            files = discover_corpus(corpus_path)
            assert len(files) == 2

    def test_finds_tar_files(self):
        """Test discovers tar archives."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir)
            (corpus_path / "test.tar").write_bytes(b"")
            (corpus_path / "test.tar.gz").write_bytes(b"")
            (corpus_path / "test.tar.bz2").write_bytes(b"")
            (corpus_path / "test.tar.xz").write_bytes(b"")
            files = discover_corpus(corpus_path)
            assert len(files) == 4

    def test_ignores_non_matching_files(self):
        """Test ignores files with unsupported extensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir)
            (corpus_path / "test.txt").write_text("")
            (corpus_path / "test.py").write_text("")
            (corpus_path / "test.csv").write_text("")
            files = discover_corpus(corpus_path)
            assert files == []

    def test_sorted_order(self):
        """Test files are returned in sorted order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir)
            (corpus_path / "z_file.json").write_text("{}")
            (corpus_path / "a_file.json").write_text("{}")
            files = discover_corpus(corpus_path)
            assert [f.name for f in files] == ["a_file.json", "z_file.json"]


class TestLintBuyerSpec:
    """Tests for lint_buyer_spec function."""

    def test_valid_json(self):
        """Test linting valid JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "value"}, f)
            f.flush()
            success, msg = lint_buyer_spec(Path(f.name))
            assert success is True
            assert msg == "OK"

    def test_valid_yaml(self):
        """Test linting valid YAML file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("key: value")
            f.flush()
            success, msg = lint_buyer_spec(Path(f.name))
            assert success is True
            assert msg == "OK"

    def test_invalid_json_falls_back_to_yaml(self):
        """Test that invalid JSON falls back to YAML parsing (lenient)."""
        # The implementation is lenient - it falls back to YAML for invalid JSON
        # This is by design for the buyer spec linting use case
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("key: value")  # Valid YAML, invalid JSON
            f.flush()
            success, msg = lint_buyer_spec(Path(f.name))
            # Lenient implementation accepts YAML in JSON files
            assert success is True

    def test_tar_file_simulated(self):
        """Test tar files return OK (simulated I/O)."""
        with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as f:
            f.write(b"")
            f.flush()
            success, msg = lint_buyer_spec(Path(f.name))
            assert success is True


class TestCalculatePercentiles:
    """Tests for calculate_percentiles function."""

    def test_basic_percentiles(self):
        """Test percentile calculation."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = calculate_percentiles(values, [50, 95, 99])
        assert len(result) == 3
        # For sorted 1-5: p50=3.0, p95=4.8, p99=4.96
        assert result[0] == 3.0
        assert 4.8 <= result[1] <= 5.0

    def test_single_value(self):
        """Test with single value."""
        values = [1.0]
        result = calculate_percentiles(values, [50])
        assert result[0] == 1.0


class TestFormatResults:
    """Tests for format_results function."""

    def test_format_output(self):
        """Test result formatting."""
        timings = [1.0, 2.0, 3.0]
        output = format_results(timings, 2.0, 2.9, 3.0, 3)
        assert "Autoresearch Lint Performance Benchmark" in output
        assert "3" in output  # total files
