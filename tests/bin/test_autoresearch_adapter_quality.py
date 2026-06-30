#!/usr/bin/env python3
"""Tests for bin/autoresearch_adapter_quality.py — coverage/recall/F1 metrics for golden vs predicted corpora."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the bin module is importable
BIN_DIR = Path(__file__).parent.parent.parent / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from autoresearch_adapter_quality import (
    compute_metrics,
    load_corpus,
    load_json,
    main,
)


class TestLoadJson:
    """Tests for load_json() — basic JSON file loader."""

    def test_loads_simple_dict(self, tmp_path: Path):
        """A simple JSON object should round-trip as a dict."""
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"a": 1, "b": 2}))
        result = load_json(f)
        assert result == {"a": 1, "b": 2}

    def test_loads_unicode(self, tmp_path: Path):
        """Non-ASCII content should be decoded as UTF-8."""
        f = tmp_path / "u.json"
        f.write_text(json.dumps({"k": "café"}, ensure_ascii=False), encoding="utf-8")
        result = load_json(f)
        assert result == {"k": "café"}


class TestLoadCorpus:
    """Tests for load_corpus() — directory or file corpus loader."""

    def test_load_from_file(self, tmp_path: Path):
        """A JSON file with scene_id -> list maps to a dict of sets."""
        f = tmp_path / "corpus.json"
        f.write_text(json.dumps({"s1": ["a", "b"], "s2": ["c"]}))
        corpus = load_corpus(f, "entities")
        assert corpus == {"s1": {"a", "b"}, "s2": {"c"}}

    def test_load_from_file_scalar_entities(self, tmp_path: Path):
        """A scalar entity value is wrapped into a one-element set."""
        f = tmp_path / "corpus.json"
        f.write_text(json.dumps({"s1": "single"}))
        corpus = load_corpus(f, "entities")
        assert corpus == {"s1": {"single"}}

    def test_load_from_directory(self, tmp_path: Path):
        """A directory of *.json files is loaded by stem as scene_id."""
        (tmp_path / "a.json").write_text(json.dumps({"entities": ["x", "y"]}))
        (tmp_path / "b.json").write_text(json.dumps({"entities": ["z"]}))
        corpus = load_corpus(tmp_path, "entities")
        assert corpus == {"a": {"x", "y"}, "b": {"z"}}

    def test_load_from_directory_uses_fallback_key(self, tmp_path: Path):
        """When configured key is missing, the 'entities' fallback is used."""
        (tmp_path / "s.json").write_text(json.dumps({"entities": ["e1"]}))
        corpus = load_corpus(tmp_path, "labels")
        assert corpus == {"s": {"e1"}}

    def test_load_from_directory_list_payload(self, tmp_path: Path):
        """A list payload is used directly as the entity set."""
        (tmp_path / "p.json").write_text(json.dumps(["alpha", "beta"]))
        corpus = load_corpus(tmp_path, "entities")
        assert corpus == {"p": {"alpha", "beta"}}

    def test_load_from_directory_scalar_payload(self, tmp_path: Path):
        """A scalar JSON payload is wrapped into a one-element set."""
        (tmp_path / "q.json").write_text(json.dumps("solo"))
        corpus = load_corpus(tmp_path, "entities")
        assert corpus == {"q": {"solo"}}

    def test_load_empty_directory(self, tmp_path: Path):
        """An empty directory yields an empty corpus."""
        corpus = load_corpus(tmp_path, "entities")
        assert corpus == {}


class TestComputeMetrics:
    """Tests for compute_metrics() — coverage, recall, precision, F1."""

    def test_empty_both(self):
        """Both empty → all metrics are 0.0, counts are 0."""
        m = compute_metrics({}, {})
        assert m["coverage"] == 0.0
        assert m["recall"] == 0.0
        assert m["precision"] == 0.0
        assert m["f1"] == 0.0
        assert m["golden_count"] == 0
        assert m["predicted_count"] == 0
        assert m["matched_count"] == 0

    def test_perfect_overlap(self):
        """Identical sets → coverage/recall/precision/F1 all 1.0."""
        golden = {"s1": {"a", "b"}, "s2": {"c"}}
        predicted = {"s1": {"a", "b"}, "s2": {"c"}}
        m = compute_metrics(golden, predicted)
        assert m["coverage"] == 1.0
        assert m["recall"] == 1.0
        assert m["precision"] == 1.0
        assert m["f1"] == 1.0
        assert m["matched_count"] == 2

    def test_no_overlap(self):
        """Disjoint scene IDs → coverage 0, entity metrics 0."""
        golden = {"s1": {"a"}}
        predicted = {"s2": {"b"}}
        m = compute_metrics(golden, predicted)
        assert m["coverage"] == 0.0
        assert m["recall"] == 0.0
        assert m["precision"] == 0.0
        assert m["f1"] == 0.0
        assert m["matched_count"] == 0

    def test_partial_entity_overlap(self):
        """Partial entity overlap → F1 is harmonic mean of precision/recall."""
        golden = {"s1": {"a", "b", "c"}}
        predicted = {"s1": {"b", "c", "d"}}
        m = compute_metrics(golden, predicted)
        # 2 correct out of 3 golden (recall=2/3), 2 correct out of 3 predicted (precision=2/3)
        assert m["recall"] == pytest.approx(2 / 3)
        assert m["precision"] == pytest.approx(2 / 3)
        # F1 = 2*p*r/(p+r) with p==r
        assert m["f1"] == pytest.approx(2 / 3)
        assert m["matched_count"] == 1

    def test_extra_predicted_scenes_counted(self):
        """Predicted scenes beyond the golden set are tracked but excluded from coverage."""
        golden = {"s1": {"a"}}
        predicted = {"s1": {"a"}, "s2": {"b"}}
        m = compute_metrics(golden, predicted)
        assert m["coverage"] == 1.0
        assert m["precision"] == 1.0
        assert m["recall"] == 1.0
        assert m["predicted_count"] == 2
        assert m["matched_count"] == 1

    def test_missing_predicted_scenes_lower_coverage(self):
        """Missing predictions reduce coverage but do not crash entity metrics."""
        golden = {"s1": {"a"}, "s2": {"b"}}
        predicted = {"s1": {"a"}}
        m = compute_metrics(golden, predicted)
        assert m["coverage"] == 0.5
        assert m["recall"] == 1.0
        assert m["precision"] == 1.0
        assert m["matched_count"] == 1
        assert m["golden_count"] == 2

    def test_empty_predicted_entities_for_matched_scene(self):
        """A matched scene with empty predicted entities is a division-by-zero guard for p side."""
        golden = {"s1": {"a"}}
        predicted = {"s1": set()}
        m = compute_metrics(golden, predicted)
        # recall = 0/1 = 0.0, precision = 0/0 → 0.0
        assert m["recall"] == 0.0
        assert m["precision"] == 0.0
        assert m["f1"] == 0.0


class TestMain:
    """Tests for main() — CLI entry point."""

    def test_missing_golden_path_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """A non-existent --golden path returns exit code 1 and prints an error."""
        nonexistent = tmp_path / "nope.json"
        # Need a real --predicted so the check ordering picks up --golden first.
        predicted = tmp_path / "p.json"
        predicted.write_text("{}")
        rc = main(["--golden", str(nonexistent), "--predicted", str(predicted)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Golden" in err

    def test_missing_predicted_path_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """A non-existent --predicted path returns exit code 1 and prints an error."""
        golden = tmp_path / "g.json"
        golden.write_text("{}")
        nonexistent = tmp_path / "nope.json"
        rc = main(["--golden", str(golden), "--predicted", str(nonexistent)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Predicted" in err

    def test_verbose_prints_report(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """--verbose prints a report containing key metric labels."""
        golden = tmp_path / "g.json"
        golden.write_text(json.dumps({"s1": ["a", "b"]}))
        predicted = tmp_path / "p.json"
        predicted.write_text(json.dumps({"s1": ["a", "b"]}))
        rc = main(
            [
                "--golden",
                str(golden),
                "--predicted",
                str(predicted),
                "--verbose",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Coverage:" in out
        assert "Recall:" in out
        assert "Precision:" in out
        assert "F1 Score:" in out

    def test_output_writes_json(self, tmp_path: Path):
        """--output writes the metrics dict as JSON."""
        golden = tmp_path / "g.json"
        golden.write_text(json.dumps({"s1": ["a"]}))
        predicted = tmp_path / "p.json"
        predicted.write_text(json.dumps({"s1": ["a"]}))
        out = tmp_path / "report.json"
        rc = main(
            [
                "--golden",
                str(golden),
                "--predicted",
                str(predicted),
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["coverage"] == 1.0
        assert data["f1"] == 1.0

    def test_load_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """A malformed JSON file causes load to raise → main returns 1."""
        golden = tmp_path / "bad.json"
        golden.write_text("{ this is not valid json")
        predicted = tmp_path / "p.json"
        predicted.write_text("{}")
        rc = main(["--golden", str(golden), "--predicted", str(predicted)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error loading data" in err

    def test_output_write_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch):
        """An --output write failure is caught and reported as exit 1."""
        golden = tmp_path / "g.json"
        golden.write_text(json.dumps({"s1": ["a"]}))
        predicted = tmp_path / "p.json"
        predicted.write_text(json.dumps({"s1": ["a"]}))
        # Output path inside a non-existent directory triggers OSError on write.
        bad_out = tmp_path / "missing_dir" / "nope" / "r.json"
        rc = main(
            [
                "--golden",
                str(golden),
                "--predicted",
                str(predicted),
                "--output",
                str(bad_out),
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error writing output" in err
