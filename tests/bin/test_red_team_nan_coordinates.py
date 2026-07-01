#!/usr/bin/env python3
"""Test coverage for bin/red_team_nan_coordinates.py.

This module exercises the red-team NaN injection tool used to validate
that lint/validation pipelines reject corrupt camera_position data
rather than silently propagating NaN values. Coverage:

- inject_nan: default field/coords, custom field path, nested field path,
  missing intermediate path raises ValueError, missing final key raises
  ValueError, custom coords list, partial coord subset, coord that is
  not in the position dict is silently skipped, non-dict final value
  is silently a no-op, returns the same data object, and is
  in-place mutating.
- load_yaml: success, empty file raises ValueError, invalid YAML raises
  ValueError, missing path raises FileNotFoundError.
- main: success writes NaN-tagged output, dry-run does not write output,
  missing input file returns 1, invalid YAML returns 1, missing field
  returns 1, custom --field flag, custom --coordinates flag, output file
  is created, output contains NaN values, output preserves other data,
  atomic write cleans up temp file, --help exits cleanly, unknown arg
  exits non-zero.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from red_team_nan_coordinates import inject_nan, load_yaml, main  # noqa: E402

# ---------------------------------------------------------------------------
# inject_nan
# ---------------------------------------------------------------------------


class TestInjectNan:
    """Tests for inject_nan function."""

    def test_default_field_corrupts_x_y_z(self):
        data = {"camera_position": {"x": 1.0, "y": 2.0, "z": 3.0}}
        out = inject_nan(data)
        assert math.isnan(out["camera_position"]["x"])
        assert math.isnan(out["camera_position"]["y"])
        assert math.isnan(out["camera_position"]["z"])

    def test_default_returns_same_data_object(self):
        data = {"camera_position": {"x": 1.0, "y": 2.0, "z": 3.0}}
        out = inject_nan(data)
        assert out is data

    def test_default_mutates_dict_in_place(self):
        data = {"camera_position": {"x": 1.0, "y": 2.0, "z": 3.0}}
        inject_nan(data)
        # The dict is mutated in place (the same object identity is returned,
        # and the dict's keys now hold NaN values).
        assert math.isnan(data["camera_position"]["x"])
        assert math.isnan(data["camera_position"]["y"])
        assert math.isnan(data["camera_position"]["z"])

    def test_custom_field_path(self):
        data = {"session": {"pose": {"x": 1.0, "y": 2.0, "z": 3.0}}}
        out = inject_nan(data, field_path="session.pose")
        assert math.isnan(out["session"]["pose"]["x"])
        assert math.isnan(out["session"]["pose"]["y"])
        assert math.isnan(out["session"]["pose"]["z"])

    def test_custom_coords_subset(self):
        data = {"camera_position": {"x": 1.0, "y": 2.0, "z": 3.0, "w": 4.0}}
        out = inject_nan(data, coords=["x", "z"])
        assert math.isnan(out["camera_position"]["x"])
        assert out["camera_position"]["y"] == 2.0
        assert math.isnan(out["camera_position"]["z"])
        assert out["camera_position"]["w"] == 4.0

    def test_custom_coords_single(self):
        data = {"camera_position": {"x": 1.0, "y": 2.0, "z": 3.0}}
        out = inject_nan(data, coords=["y"])
        assert out["camera_position"]["x"] == 1.0
        assert math.isnan(out["camera_position"]["y"])
        assert out["camera_position"]["z"] == 3.0

    def test_missing_intermediate_path_raises_value_error(self):
        data = {"session": {}}
        with pytest.raises(ValueError, match="not found"):
            inject_nan(data, field_path="session.pose")

    def test_missing_final_key_raises_value_error(self):
        data = {"session": {"other": {"x": 1.0}}}
        with pytest.raises(ValueError, match="not found"):
            inject_nan(data, field_path="session.pose")

    def test_top_level_field_missing_raises_value_error(self):
        data = {"other_key": {"x": 1.0}}
        with pytest.raises(ValueError, match="not found"):
            inject_nan(data, field_path="camera_position")

    def test_coord_not_in_position_dict_is_skipped(self):
        # coords list contains names that aren't keys in the position dict.
        data = {"camera_position": {"x": 1.0}}
        out = inject_nan(data, coords=["x", "absent", "also_missing"])
        assert math.isnan(out["camera_position"]["x"])
        # Unrelated key remains untouched.
        assert "absent" not in out["camera_position"]

    def test_non_dict_final_value_is_noop(self):
        # If the final key points to a non-dict (e.g. scalar), nothing happens.
        data = {"camera_position": [1.0, 2.0, 3.0]}
        out = inject_nan(data)
        assert out["camera_position"] == [1.0, 2.0, 3.0]

    def test_string_final_value_is_noop(self):
        data = {"camera_position": "not_a_dict"}
        out = inject_nan(data)
        assert out["camera_position"] == "not_a_dict"

    def test_empty_coords_list_is_noop(self):
        data = {"camera_position": {"x": 1.0, "y": 2.0, "z": 3.0}}
        out = inject_nan(data, coords=[])
        assert out["camera_position"]["x"] == 1.0
        assert out["camera_position"]["y"] == 2.0
        assert out["camera_position"]["z"] == 3.0

    def test_preserves_sibling_keys(self):
        data = {
            "camera_position": {"x": 1.0, "y": 2.0, "z": 3.0},
            "session_id": "abc",
            "metadata": {"fps": 60},
        }
        out = inject_nan(data)
        assert out["session_id"] == "abc"
        assert out["metadata"]["fps"] == 60
        assert math.isnan(out["camera_position"]["x"])

    def test_nested_path_with_deep_intermediate(self):
        data = {"a": {"b": {"c": {"x": 1.0, "y": 2.0, "z": 3.0}}}}
        out = inject_nan(data, field_path="a.b.c")
        assert math.isnan(out["a"]["b"]["c"]["x"])
        assert math.isnan(out["a"]["b"]["c"]["y"])
        assert math.isnan(out["a"]["b"]["c"]["z"])


# ---------------------------------------------------------------------------
# load_yaml
# ---------------------------------------------------------------------------


class TestLoadYaml:
    """Tests for load_yaml function."""

    def test_load_valid_yaml(self, tmp_path):
        p = tmp_path / "data.yaml"
        p.write_text("camera_position:\n  x: 1.0\n  y: 2.0\n  z: 3.0\n", encoding="utf-8")
        data = load_yaml(p)
        assert data == {"camera_position": {"x": 1.0, "y": 2.0, "z": 3.0}}

    def test_empty_file_raises_value_error(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="Empty YAML"):
            load_yaml(p)

    def test_invalid_yaml_raises_value_error(self, tmp_path):
        p = tmp_path / "bad.yaml"
        # Unbalanced braces => YAML parse error.
        p.write_text("foo: [unclosed\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_yaml(p)

    def test_missing_path_raises_file_not_found(self, tmp_path):
        p = tmp_path / "nope.yaml"
        with pytest.raises(FileNotFoundError):
            load_yaml(p)

    def test_load_yaml_returns_dict_for_mapping(self, tmp_path):
        p = tmp_path / "map.yaml"
        p.write_text("key: value\n", encoding="utf-8")
        data = load_yaml(p)
        assert isinstance(data, dict)
        assert data["key"] == "value"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _write_valid_yaml(path: Path) -> None:
    path.write_text(
        "session_id: test-001\n"
        "camera_position:\n"
        "  x: 1.5\n"
        "  y: 2.5\n"
        "  z: 3.5\n",
        encoding="utf-8",
    )


class TestMainSuccess:
    """Tests for main() success paths."""

    def test_writes_nan_to_output(self, tmp_path):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)

        rc = main(["--input", str(inp), "--output", str(out)])
        assert rc == 0
        assert out.exists()

        import yaml as _yaml

        result = _yaml.safe_load(out.read_text(encoding="utf-8"))
        assert math.isnan(result["camera_position"]["x"])
        assert math.isnan(result["camera_position"]["y"])
        assert math.isnan(result["camera_position"]["z"])

    def test_preserves_sibling_data(self, tmp_path):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)

        main(["--input", str(inp), "--output", str(out)])

        import yaml as _yaml

        result = _yaml.safe_load(out.read_text(encoding="utf-8"))
        assert result["session_id"] == "test-001"

    def test_custom_field_path(self, tmp_path):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        inp.write_text(
            "session:\n  pose:\n    x: 1.0\n    y: 2.0\n    z: 3.0\n", encoding="utf-8"
        )

        rc = main(["--input", str(inp), "--output", str(out), "--field", "session.pose"])
        assert rc == 0

        import yaml as _yaml

        result = _yaml.safe_load(out.read_text(encoding="utf-8"))
        assert math.isnan(result["session"]["pose"]["x"])
        assert math.isnan(result["session"]["pose"]["y"])
        assert math.isnan(result["session"]["pose"]["z"])

    def test_custom_coordinates(self, tmp_path):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)

        rc = main(
            [
                "--input",
                str(inp),
                "--output",
                str(out),
                "--coordinates",
                "x",
            ]
        )
        assert rc == 0

        import yaml as _yaml

        result = _yaml.safe_load(out.read_text(encoding="utf-8"))
        assert math.isnan(result["camera_position"]["x"])
        assert result["camera_position"]["y"] == 2.5
        assert result["camera_position"]["z"] == 3.5


class TestMainDryRun:
    """Tests for main() --dry-run."""

    def test_dry_run_does_not_write_output(self, tmp_path):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)

        rc = main(["--input", str(inp), "--output", str(out), "--dry-run"])
        assert rc == 0
        assert not out.exists()

    def test_dry_run_prints_message(self, tmp_path, capsys):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)

        main(["--input", str(inp), "--output", str(out), "--dry-run"])
        captured = capsys.readouterr()
        assert "Dry run complete" in captured.out


class TestMainErrors:
    """Tests for main() error paths."""

    def test_missing_input_returns_1(self, tmp_path, capsys):
        inp = tmp_path / "missing.yaml"
        out = tmp_path / "out.yaml"

        rc = main(["--input", str(inp), "--output", str(out)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Input not found" in captured.err
        assert not out.exists()

    def test_invalid_yaml_returns_1(self, tmp_path, capsys):
        inp = tmp_path / "bad.yaml"
        out = tmp_path / "out.yaml"
        inp.write_text("foo: [unclosed\n", encoding="utf-8")

        rc = main(["--input", str(inp), "--output", str(out)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Invalid YAML" in captured.err

    def test_empty_yaml_returns_1(self, tmp_path, capsys):
        inp = tmp_path / "empty.yaml"
        out = tmp_path / "out.yaml"
        inp.write_text("", encoding="utf-8")

        rc = main(["--input", str(inp), "--output", str(out)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Empty YAML" in captured.err

    def test_missing_field_returns_1(self, tmp_path, capsys):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        inp.write_text("other_key: 1\n", encoding="utf-8")

        rc = main(["--input", str(inp), "--output", str(out), "--field", "camera_position"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err
        assert not out.exists()

    def test_missing_field_dry_run_also_returns_1(self, tmp_path, capsys):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        inp.write_text("other_key: 1\n", encoding="utf-8")

        rc = main(
            [
                "--input",
                str(inp),
                "--output",
                str(out),
                "--field",
                "camera_position",
                "--dry-run",
            ]
        )
        assert rc == 1


class TestMainCli:
    """Tests for main() CLI plumbing."""

    def test_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_missing_required_input_arg_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--output", "/tmp/x.yaml"])
        assert exc_info.value.code != 0

    def test_missing_required_output_arg_exits_nonzero(self, tmp_path):
        inp = tmp_path / "in.yaml"
        _write_valid_yaml(inp)
        with pytest.raises(SystemExit) as exc_info:
            main(["--input", str(inp)])
        assert exc_info.value.code != 0

    def test_short_flags_work(self, tmp_path):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)

        rc = main(["-i", str(inp), "-o", str(out)])
        assert rc == 0
        assert out.exists()

    def test_subprocess_invocation_matches_main(self, tmp_path):
        """End-to-end: invoking as a script produces the same result as main()."""
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)

        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_nan_coordinates.py"),
             "--input", str(inp), "--output", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert out.exists()

        import yaml as _yaml

        data = _yaml.safe_load(out.read_text(encoding="utf-8"))
        assert math.isnan(data["camera_position"]["x"])
        assert data["session_id"] == "test-001"


class TestMainAtomicWrite:
    """Tests verifying atomic write semantics."""

    def test_no_temp_files_left_in_output_dir(self, tmp_path):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)

        main(["--input", str(inp), "--output", str(out)])

        # Only the input + output should be present; no leftover .yaml
        # temp files from the atomic-write dance.
        yamls = sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".yaml")
        assert yamls == ["in.yaml", "out.yaml"]

    def test_overwrites_existing_output(self, tmp_path):
        inp = tmp_path / "in.yaml"
        out = tmp_path / "out.yaml"
        _write_valid_yaml(inp)
        out.write_text("stale: data\n", encoding="utf-8")

        rc = main(["--input", str(inp), "--output", str(out)])
        assert rc == 0

        import yaml as _yaml

        result = _yaml.safe_load(out.read_text(encoding="utf-8"))
        assert "stale" not in result
        assert math.isnan(result["camera_position"]["x"])
