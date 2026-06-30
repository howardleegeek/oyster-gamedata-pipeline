#!/usr/bin/env python3
"""Tests for src/oyster_agent_runner/keycode_int_normalizer.py

PRD page 11 (Bug #3 in PRD_AUDIT_2026_05_04.md): ``keyCode`` must be a
single ``int`` (ASCII code point — W=87) per record, never a list.
This module exposes ``collapse`` (list → sum) and ``expand`` (list → N
records) helpers plus a CLI entry-point.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from oyster_agent_runner.keycode_int_normalizer import (
    _collapse_keycode,
    _expand_keycode,
    main,
    normalize,
)

# ---------------------------------------------------------------------------
# _collapse_keycode
# ---------------------------------------------------------------------------


class TestCollapseKeycode:
    """Unit tests for the private ``_collapse_keycode`` helper."""

    def test_int_passes_through(self):
        """A scalar int must be returned unchanged."""
        assert _collapse_keycode(87) == 87
        assert _collapse_keycode(0) == 0

    def test_single_element_list(self):
        """A one-element list is collapsed to that element."""
        assert _collapse_keycode([87]) == 87
        assert _collapse_keycode([65]) == 65

    def test_multi_element_list_sums(self):
        """W (87) + A (65) collapses to 87+65=152 (combined key press)."""
        assert _collapse_keycode([87, 65]) == 152
        assert _collapse_keycode([87, 65, 83, 68]) == 87 + 65 + 83 + 68

    def test_tuple_input_also_supported(self):
        """A tuple is treated the same as a list."""
        assert _collapse_keycode((87, 65)) == 152

    def test_empty_list_collapses_to_zero(self):
        """An empty list collapses to 0 (the additive identity)."""
        assert _collapse_keycode([]) == 0

    def test_unsupported_type_raises(self):
        """A non-int / non-list type (e.g. str, dict) raises TypeError."""
        with pytest.raises(TypeError):
            _collapse_keycode("87")
        with pytest.raises(TypeError):
            _collapse_keycode({"code": 87})
        with pytest.raises(TypeError):
            _collapse_keycode(87.5)


# ---------------------------------------------------------------------------
# _expand_keycode
# ---------------------------------------------------------------------------


class TestExpandKeycode:
    """Unit tests for the private ``_expand_keycode`` helper."""

    def test_missing_key_returns_single_copy(self):
        """A record without the keyCode field is returned as a 1-element list."""
        rec = {"time": "t", "frame": 0}
        out = _expand_keycode(rec)
        assert out == [rec]
        # Important: must be a copy, not the same object (avoids aliasing bugs).
        assert out[0] is not rec

    def test_scalar_int_returns_single_copy(self):
        """A scalar int is returned as a single-element list (no-op expand)."""
        rec = {"keyCode": 87, "frame": 1}
        out = _expand_keycode(rec)
        assert len(out) == 1
        assert out[0]["keyCode"] == 87

    def test_list_becomes_n_records(self):
        """A 3-element list yields 3 records each with one int."""
        rec = {"keyCode": [87, 65, 83], "frame": 2}
        out = _expand_keycode(rec)
        assert len(out) == 3
        assert out[0]["keyCode"] == 87
        assert out[1]["keyCode"] == 65
        assert out[2]["keyCode"] == 83

    def test_expansion_adds_frame_idx(self):
        """Each expanded record gets a ``_frame_idx`` field (0..N-1)."""
        rec = {"keyCode": [87, 65]}
        out = _expand_keycode(rec)
        assert out[0]["_frame_idx"] == 0
        assert out[1]["_frame_idx"] == 1

    def test_expansion_preserves_other_fields(self):
        """Sibling fields (time, frame, route_type) survive expansion."""
        rec = {
            "time": "2026-05-18 12:00:00.000",
            "frame": 5,
            "route_type": 1,
            "keyCode": [87, 65],
        }
        out = _expand_keycode(rec)
        for o in out:
            assert o["time"] == "2026-05-18 12:00:00.000"
            assert o["frame"] == 5
            assert o["route_type"] == 1

    def test_does_not_mutate_input_record(self):
        """The input record must be left untouched (shallow-copy semantics)."""
        rec = {"keyCode": [87, 65], "frame": 1}
        _ = _expand_keycode(rec)
        assert rec == {"keyCode": [87, 65], "frame": 1}

    def test_unsupported_type_raises(self):
        """A string in the keyCode field is not a valid type."""
        with pytest.raises(TypeError):
            _expand_keycode({"keyCode": "WASD"})

    def test_custom_key_name(self):
        """``key`` argument lets you expand a different field."""
        rec = {"action_codes": [1, 2, 3]}
        out = _expand_keycode(rec, key="action_codes")
        assert len(out) == 3
        assert out[0]["action_codes"] == 1
        assert out[2]["action_codes"] == 3


# ---------------------------------------------------------------------------
# normalize (public API)
# ---------------------------------------------------------------------------


class TestNormalize:
    """End-to-end tests for the public ``normalize`` function."""

    def test_collapse_default_mode(self):
        """Default mode is ``collapse``."""
        records = [{"keyCode": [87, 65]}, {"keyCode": 83}]
        out = normalize(records)
        assert out[0]["keyCode"] == 152
        assert out[1]["keyCode"] == 83

    def test_collapse_explicit_mode(self):
        """Explicit ``mode="collapse"`` matches the default."""
        records = [{"keyCode": [87, 65]}]
        out = normalize(records, mode="collapse")
        assert out[0]["keyCode"] == 152

    def test_expand_mode(self):
        """``mode="expand"`` produces one record per list element."""
        records = [{"keyCode": [87, 65], "frame": 1}]
        out = normalize(records, mode="expand")
        assert len(out) == 2
        assert out[0]["keyCode"] == 87
        assert out[1]["keyCode"] == 65

    def test_expand_with_scalar_is_noop(self):
        """Expanding a record whose keyCode is already int yields one record."""
        records = [{"keyCode": 87, "frame": 1}]
        out = normalize(records, mode="expand")
        assert len(out) == 1
        assert out[0]["keyCode"] == 87

    def test_unknown_mode_raises(self):
        """An unknown mode raises ValueError, not silent success."""
        with pytest.raises(ValueError):
            normalize([{"keyCode": 87}], mode="bogus")

    def test_empty_input_list(self):
        """An empty input list yields an empty output list."""
        assert normalize([]) == []
        assert normalize([], mode="expand") == []

    def test_collapse_does_not_mutate_input(self):
        """The input list/records must be left untouched."""
        records = [{"keyCode": [87, 65]}]
        _ = normalize(records, mode="collapse")
        assert records[0]["keyCode"] == [87, 65]

    def test_expand_does_not_mutate_input(self):
        """The input list/records must be left untouched in expand mode."""
        records = [{"keyCode": [87, 65]}]
        _ = normalize(records, mode="expand")
        assert records[0]["keyCode"] == [87, 65]

    def test_custom_key(self):
        """Custom key name lets you normalize a non-default field."""
        records = [{"key_codes": [1, 2]}]
        out = normalize(records, key="key_codes")
        assert out[0]["key_codes"] == 3

    def test_mixed_list_and_scalar_records(self):
        """Mixed record types must each be normalized correctly."""
        records = [
            {"keyCode": [87, 65]},
            {"keyCode": 83},
            {"frame": 99},  # no keyCode at all
        ]
        out = normalize(records, mode="collapse")
        assert out[0]["keyCode"] == 152
        assert out[1]["keyCode"] == 83
        assert "keyCode" not in out[2]


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the command-line entry-point."""

    def _run(self, *args: str, input_obj=None) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "oyster_agent_runner.keycode_int_normalizer", *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=json.dumps(input_obj) if input_obj is not None else None,
            check=False,
        )

    def test_collapse_round_trip(self, tmp_path: Path):
        """CLI collapse mode: read JSON list, write normalized JSON, exit 0."""
        inp = tmp_path / "in.json"
        outp = tmp_path / "out.json"
        inp.write_text(json.dumps([{"keyCode": [87, 65]}]))
        rc = main(["--input", str(inp), "--output", str(outp), "--mode", "collapse"])
        assert rc == 0
        data = json.loads(outp.read_text())
        assert data == [{"keyCode": 152}]

    def test_expand_round_trip(self, tmp_path: Path):
        """CLI expand mode: produces N records from a list-form keyCode."""
        inp = tmp_path / "in.json"
        outp = tmp_path / "out.json"
        inp.write_text(json.dumps([{"keyCode": [87, 65, 83]}]))
        rc = main(["--input", str(inp), "--output", str(outp), "--mode", "expand"])
        assert rc == 0
        data = json.loads(outp.read_text())
        assert len(data) == 3
        assert [d["keyCode"] for d in data] == [87, 65, 83]

    def test_missing_input_file_returns_nonzero(self, tmp_path: Path):
        """Missing input file produces a non-zero exit code, no silent success."""
        rc = main(
            [
                "--input",
                str(tmp_path / "nope.json"),
                "--output",
                str(tmp_path / "out.json"),
            ]
        )
        assert rc != 0

    def test_invalid_json_returns_nonzero(self, tmp_path: Path):
        """A non-list JSON document (e.g. dict at top level) fails loudly."""
        inp = tmp_path / "in.json"
        inp.write_text(json.dumps({"not": "a list"}))
        rc = main(["--input", str(inp), "--output", str(tmp_path / "out.json")])
        assert rc != 0

    def test_default_mode_is_collapse(self, tmp_path: Path):
        """With no ``--mode`` flag, default is collapse (sums list)."""
        inp = tmp_path / "in.json"
        outp = tmp_path / "out.json"
        inp.write_text(json.dumps([{"keyCode": [10, 20]}]))
        rc = main(["--input", str(inp), "--output", str(outp)])
        assert rc == 0
        assert json.loads(outp.read_text())[0]["keyCode"] == 30
