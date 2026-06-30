#!/usr/bin/env python3
"""Tests for bin/red_team_duplicate_frame_id.py — duplicate frame_id detection.

Covers:
- parse_args: positional ``input`` + ``--field`` / ``--verbose`` flags
- load_json: list input, dict input, dict-with-"records" key, missing file,
  non-list/dict root, non-dict record element
- find_duplicates: no duplicates (returns empty set), exact duplicates
  (2+ indices), partial-membership records (records that lack the field
  are ignored), multiple distinct duplicate values, custom field name
- main: exit 0 (no duplicates), exit 1 (duplicates, with and without
  --verbose), exit 2 (FileNotFoundError, JSONDecodeError, ValueError on
  bad structure, ValueError on non-dict record), exit 2 on empty
  records list
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.red_team_duplicate_frame_id import (  # noqa: E402
    find_duplicates,
    load_json,
    main,
    parse_args,
)

# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Tests for the argparse wrapper."""

    def test_input_positional(self):
        """Positional ``input`` is captured as-is."""
        ns = parse_args(["records.json"])
        assert ns.input == "records.json"
        assert ns.field == "frame_id"
        assert ns.verbose is False

    def test_custom_field(self):
        """``--field`` overrides the default of ``frame_id``."""
        ns = parse_args(["records.json", "--field", "uuid"])
        assert ns.field == "uuid"

    def test_short_field_flag(self):
        """``-f`` is the short form of ``--field``."""
        ns = parse_args(["records.json", "-f", "id"])
        assert ns.field == "id"

    def test_verbose_flag(self):
        """``--verbose`` sets ``verbose`` to True."""
        ns = parse_args(["records.json", "--verbose"])
        assert ns.verbose is True

    def test_short_verbose_flag(self):
        """``-v`` is the short form of ``--verbose``."""
        ns = parse_args(["records.json", "-v"])
        assert ns.verbose is True


# ---------------------------------------------------------------------------
# load_json
# ---------------------------------------------------------------------------


class TestLoadJson:
    """Tests for the JSON loader + record-normalization helper."""

    def test_load_list_of_records(self, tmp_path):
        """Top-level list of records is returned as-is."""
        fp = tmp_path / "recs.json"
        fp.write_text(json.dumps([{"frame_id": 1}, {"frame_id": 2}]))
        records = load_json(str(fp))
        assert records == [{"frame_id": 1}, {"frame_id": 2}]

    def test_load_single_dict(self, tmp_path):
        """Top-level dict with no ``records`` key is wrapped in a list."""
        fp = tmp_path / "rec.json"
        fp.write_text(json.dumps({"frame_id": 1, "value": "x"}))
        records = load_json(str(fp))
        assert records == [{"frame_id": 1, "value": "x"}]

    def test_load_dict_with_records_key(self, tmp_path):
        """Top-level dict with a ``records`` key returns the inner list."""
        fp = tmp_path / "rec.json"
        fp.write_text(json.dumps({"records": [{"frame_id": 1}, {"frame_id": 2}]}))
        records = load_json(str(fp))
        assert records == [{"frame_id": 1}, {"frame_id": 2}]

    def test_load_missing_file_raises(self, tmp_path):
        """Non-existent file raises FileNotFoundError."""
        missing = tmp_path / "nope.json"
        with pytest.raises(FileNotFoundError):
            load_json(str(missing))

    def test_load_scalar_root_raises(self, tmp_path):
        """Top-level scalar (e.g. integer) raises ValueError."""
        fp = tmp_path / "scalar.json"
        fp.write_text("42")
        with pytest.raises(ValueError, match="Expected list or dict"):
            load_json(str(fp))

    def test_load_non_dict_record_raises(self, tmp_path):
        """List element that is not a dict raises ValueError with index."""
        fp = tmp_path / "bad.json"
        fp.write_text(json.dumps([{"frame_id": 1}, "not-a-dict"]))
        with pytest.raises(ValueError, match="Record at index 1 is not a dict"):
            load_json(str(fp))

    def test_load_empty_list(self, tmp_path):
        """Empty list is a valid input — returns empty list."""
        fp = tmp_path / "empty.json"
        fp.write_text("[]")
        assert load_json(str(fp)) == []


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------


class TestFindDuplicates:
    """Tests for the duplicate-detection core function."""

    def test_no_duplicates_returns_empty_set(self):
        """All unique values → duplicates set is empty, every value mapped."""
        records = [{"frame_id": 1}, {"frame_id": 2}, {"frame_id": 3}]
        value_to_indices, dups = find_duplicates(records, "frame_id")
        assert dups == set()
        assert value_to_indices == {1: [0], 2: [1], 3: [2]}

    def test_exact_duplicate(self):
        """Duplicate value is reported with all its indices."""
        records = [{"frame_id": 1}, {"frame_id": 2}, {"frame_id": 1}]
        value_to_indices, dups = find_duplicates(records, "frame_id")
        assert dups == {1}
        assert value_to_indices[1] == [0, 2]
        assert value_to_indices[2] == [1]

    def test_missing_field_records_ignored(self):
        """Records lacking the target field are silently skipped."""
        records = [
            {"frame_id": 1},
            {"other": "no-frame-id"},
            {"frame_id": 1},
        ]
        value_to_indices, dups = find_duplicates(records, "frame_id")
        assert dups == {1}
        assert value_to_indices == {1: [0, 2]}

    def test_multiple_duplicate_values(self):
        """Several distinct duplicate values are all flagged."""
        records = [
            {"frame_id": "a"},
            {"frame_id": "b"},
            {"frame_id": "a"},
            {"frame_id": "b"},
            {"frame_id": "c"},
        ]
        value_to_indices, dups = find_duplicates(records, "frame_id")
        assert dups == {"a", "b"}
        assert value_to_indices["a"] == [0, 2]
        assert value_to_indices["b"] == [1, 3]
        assert value_to_indices["c"] == [4]

    def test_empty_records(self):
        """Empty record list returns empty mapping and empty duplicate set."""
        value_to_indices, dups = find_duplicates([], "frame_id")
        assert dups == set()
        assert value_to_indices == {}

    def test_custom_field(self):
        """The ``field`` argument controls which key is checked."""
        records = [{"id": 1}, {"id": 2}, {"id": 1}]
        value_to_indices, dups = find_duplicates(records, "id")
        assert dups == {1}
        assert value_to_indices[1] == [0, 2]

    def test_string_values(self):
        """String field values are handled identically to numeric."""
        records = [{"frame_id": "abc"}, {"frame_id": "abc"}]
        value_to_indices, dups = find_duplicates(records, "frame_id")
        assert dups == {"abc"}
        assert value_to_indices["abc"] == [0, 1]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the CLI entry point and exit-code contract."""

    def _write(self, tmp_path, payload):
        fp = tmp_path / "in.json"
        fp.write_text(json.dumps(payload))
        return str(fp)

    def test_no_duplicates_exit_zero(self, tmp_path, capsys):
        """All unique values → exit 0, PASS message on stdout."""
        fp = self._write(tmp_path, [{"frame_id": 1}, {"frame_id": 2}])
        rc = main([fp])
        assert rc == 0
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "No duplicate" in out

    def test_duplicates_exit_one(self, tmp_path, capsys):
        """Duplicate value → exit 1, FAIL message on stdout."""
        fp = self._write(tmp_path, [{"frame_id": 1}, {"frame_id": 1}])
        rc = main([fp])
        assert rc == 1
        captured = capsys.readouterr()
        out, err = captured.out, captured.err
        assert "FAIL" in out
        assert "1 duplicate" in out
        # Lint-rejects message goes to stderr per the SUT
        assert "Lint rejects" in err

    def test_duplicates_verbose(self, tmp_path, capsys):
        """``--verbose`` surfaces per-value index lists on stdout."""
        fp = self._write(tmp_path, [{"frame_id": 1}, {"frame_id": 2}, {"frame_id": 1}])
        rc = main([fp, "--verbose"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "indices:" in out
        assert "[0, 2]" in out

    def test_duplicates_non_verbose(self, tmp_path, capsys):
        """Without ``--verbose``, only the count is shown, not the index list."""
        fp = self._write(tmp_path, [{"frame_id": 1}, {"frame_id": 1}])
        rc = main([fp])
        assert rc == 1
        out = capsys.readouterr().out
        assert "appears 2 times" in out
        assert "indices:" not in out

    def test_custom_field_flag(self, tmp_path, capsys):
        """``--field`` selects a non-default key for the duplicate check."""
        fp = self._write(tmp_path, [{"uuid": "x"}, {"uuid": "x"}])
        rc = main([fp, "--field", "uuid"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "uuid" in out
        assert "FAIL" in out

    def test_short_field_flag(self, tmp_path, capsys):
        """``-f`` is the short form of ``--field``."""
        fp = self._write(tmp_path, [{"uuid": "x"}, {"uuid": "x"}])
        rc = main([fp, "-f", "uuid"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "uuid" in out

    def test_short_verbose_flag(self, tmp_path, capsys):
        """``-v`` is the short form of ``--verbose``."""
        fp = self._write(tmp_path, [{"frame_id": 1}, {"frame_id": 1}])
        rc = main([fp, "-v"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "indices:" in out

    def test_missing_file_exit_two(self, tmp_path, capsys):
        """Non-existent input → exit 2, error printed to stderr."""
        missing = str(tmp_path / "nope.json")
        rc = main([missing])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Error" in err
        assert "nope.json" in err

    def test_invalid_json_exit_two(self, tmp_path, capsys):
        """Malformed JSON → exit 2, JSONDecodeError surfaced on stderr."""
        fp = tmp_path / "bad.json"
        fp.write_text("{ not valid json")
        rc = main([str(fp)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "JSON parse error" in err

    def test_scalar_root_exit_two(self, tmp_path, capsys):
        """JSON root that is neither list nor dict → exit 2, ValueError on stderr."""
        fp = tmp_path / "scalar.json"
        fp.write_text("42")
        rc = main([str(fp)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Invalid data structure" in err

    def test_non_dict_record_exit_two(self, tmp_path, capsys):
        """List element that is not a dict → exit 2, ValueError on stderr."""
        fp = tmp_path / "mixed.json"
        fp.write_text(json.dumps([{"frame_id": 1}, "oops"]))
        rc = main([str(fp)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Invalid data structure" in err
        assert "index 1" in err

    def test_empty_records_exit_two(self, tmp_path, capsys):
        """Empty record list is treated as an error (exit 2)."""
        fp = tmp_path / "empty.json"
        fp.write_text("[]")
        rc = main([str(fp)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "No records found" in err
