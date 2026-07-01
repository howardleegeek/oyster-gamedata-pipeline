#!/usr/bin/env python3
"""Test coverage for bin/red_team_mixed_vector_format.py.

This module exercises the red-team Vector3 format validation utility that detects
mixed dict/list format inconsistency in position data. Coverage:

- parse_vector3_dict: default values (0.0), custom values, non-float coercion.
- parse_vector3_list: valid 3-element list, wrong length raises ValueError,
  non-float coercion.
- vector3_to_dict: dict input preserved, list input converted.
- vector3_to_list: list input preserved, dict input converted.
- validate_format_consistency: all-dict valid, all-list valid, mixed invalid,
  missing position key, dict missing keys, list wrong length, unsupported type.
- analyze_file: valid file returns 0, syntax error returns 1, mixed format returns 1.
- main CLI: --help exits 0, no args runs sample, file arg analyzes file,
  subprocess --help exits 0, subprocess end-to-end.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from red_team_mixed_vector_format import (  # noqa: E402
    analyze_file,
    parse_vector3_dict,
    parse_vector3_list,
    validate_format_consistency,
    vector3_to_dict,
    vector3_to_list,
)

# ---------------------------------------------------------------------------
# parse_vector3_dict tests
# ---------------------------------------------------------------------------


def test_parse_vector3_dict_default_values() -> None:
    """parse_vector3_dict returns 0.0 for missing keys."""
    result = parse_vector3_dict({})
    assert result == {"x": 0.0, "y": 0.0, "z": 0.0}


def test_parse_vector3_dict_custom_values() -> None:
    """parse_vector3_dict correctly parses custom float values."""
    result = parse_vector3_dict({"x": 1.5, "y": 2.5, "z": 3.5})
    assert result == {"x": 1.5, "y": 2.5, "z": 3.5}


def test_parse_vector3_dict_non_float_coercion() -> None:
    """parse_vector3_dict coerces int and string numbers to float."""
    result = parse_vector3_dict({"x": 1, "y": "2.5", "z": 3})
    assert result == {"x": 1.0, "y": 2.5, "z": 3.0}


def test_parse_vector3_dict_partial_keys() -> None:
    """parse_vector3_dict handles partial key sets with defaults."""
    result = parse_vector3_dict({"x": 1.0})
    assert result == {"x": 1.0, "y": 0.0, "z": 0.0}


# ---------------------------------------------------------------------------
# parse_vector3_list tests
# ---------------------------------------------------------------------------


def test_parse_vector3_list_valid() -> None:
    """parse_vector3_list correctly parses 3-element list."""
    result = parse_vector3_list([1.0, 2.0, 3.0])
    assert result == [1.0, 2.0, 3.0]


def test_parse_vector3_list_wrong_length_raises() -> None:
    """parse_vector3_list raises ValueError for non-3-element list."""
    with pytest.raises(ValueError, match=r"must have exactly 3 elements"):
        parse_vector3_list([1.0, 2.0])

    with pytest.raises(ValueError, match=r"must have exactly 3 elements"):
        parse_vector3_list([1.0, 2.0, 3.0, 4.0])

    with pytest.raises(ValueError, match=r"must have exactly 3 elements"):
        parse_vector3_list([])


def test_parse_vector3_list_non_float_coercion() -> None:
    """parse_vector3_list coerces int and string numbers to float."""
    result = parse_vector3_list([1, "2.5", 3])
    assert result == [1.0, 2.5, 3.0]


# ---------------------------------------------------------------------------
# vector3_to_dict tests
# ---------------------------------------------------------------------------


def test_vector3_to_dict_from_dict() -> None:
    """vector3_to_dict preserves dict input."""
    v = {"x": 1.0, "y": 2.0, "z": 3.0}
    result = vector3_to_dict(v)
    assert result == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_vector3_to_dict_from_list() -> None:
    """vector3_to_dict converts list input to dict."""
    v = [1.0, 2.0, 3.0]
    result = vector3_to_dict(v)
    assert result == {"x": 1.0, "y": 2.0, "z": 3.0}


# ---------------------------------------------------------------------------
# vector3_to_list tests
# ---------------------------------------------------------------------------


def test_vector3_to_list_from_list() -> None:
    """vector3_to_list preserves list input."""
    v = [1.0, 2.0, 3.0]
    result = vector3_to_list(v)
    assert result == [1.0, 2.0, 3.0]


def test_vector3_to_list_from_dict() -> None:
    """vector3_to_list converts dict input to list."""
    v = {"x": 1.0, "y": 2.0, "z": 3.0}
    result = vector3_to_list(v)
    assert result == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# validate_format_consistency tests
# ---------------------------------------------------------------------------


def test_validate_format_consistency_all_dict_valid() -> None:
    """validate_format_consistency returns True for all-dict format."""
    positions = [
        {"position": {"x": 1.0, "y": 2.0, "z": 3.0}},
        {"position": {"x": 4.0, "y": 5.0, "z": 6.0}},
    ]
    is_valid, errors = validate_format_consistency(positions)
    assert is_valid is True
    assert errors == []


def test_validate_format_consistency_all_list_valid() -> None:
    """validate_format_consistency returns True for all-list format."""
    positions = [
        {"position": [1.0, 2.0, 3.0]},
        {"position": [4.0, 5.0, 6.0]},
    ]
    is_valid, errors = validate_format_consistency(positions)
    assert is_valid is True
    assert errors == []


def test_validate_format_consistency_mixed_invalid() -> None:
    """validate_format_consistency returns False for mixed dict/list."""
    positions = [
        {"position": {"x": 1.0, "y": 2.0, "z": 3.0}},
        {"position": [4.0, 5.0, 6.0]},
    ]
    is_valid, errors = validate_format_consistency(positions)
    assert is_valid is False
    assert any("Format inconsistency" in e for e in errors)


def test_validate_format_consistency_missing_position_key() -> None:
    """validate_format_consistency reports missing position key."""
    positions = [
        {"position": {"x": 1.0, "y": 2.0, "z": 3.0}},
        {"other": [4.0, 5.0, 6.0]},
    ]
    is_valid, errors = validate_format_consistency(positions)
    assert is_valid is False
    assert any("missing 'position' key" in e for e in errors)


def test_validate_format_consistency_dict_missing_keys() -> None:
    """validate_format_consistency reports dict missing required keys."""
    positions = [
        {"position": {"x": 1.0, "y": 2.0}},
    ]
    is_valid, errors = validate_format_consistency(positions)
    assert is_valid is False
    assert any("dict missing required keys" in e for e in errors)


def test_validate_format_consistency_list_wrong_length() -> None:
    """validate_format_consistency reports list wrong element count."""
    positions = [
        {"position": [1.0, 2.0]},
    ]
    is_valid, errors = validate_format_consistency(positions)
    assert is_valid is False
    assert any("list must have 3 elements" in e for e in errors)


def test_validate_format_consistency_unsupported_type() -> None:
    """validate_format_consistency reports unsupported type."""
    positions = [
        {"position": "invalid"},
    ]
    is_valid, errors = validate_format_consistency(positions)
    assert is_valid is False
    assert any("unsupported type" in e for e in errors)


def test_validate_format_consistency_tuple_input() -> None:
    """validate_format_consistency treats tuple as list-like."""
    positions = [
        {"position": (1.0, 2.0, 3.0)},
    ]
    is_valid, errors = validate_format_consistency(positions)
    assert is_valid is True
    assert errors == []


# ---------------------------------------------------------------------------
# analyze_file tests
# ---------------------------------------------------------------------------


def test_analyze_file_valid_syntax(tmp_path: Path) -> None:
    """analyze_file returns 0 for valid Python file with consistent format."""
    # Create a Python file with consistent dict format positions
    content = """
positions = [
    {"position": {"x": 1.0, "y": 2.0, "z": 3.0}},
    {"position": {"x": 4.0, "y": 5.0, "z": 6.0}},
]
"""
    test_file = tmp_path / "valid.py"
    test_file.write_text(content)
    result = analyze_file(test_file)
    assert result == 0


def test_analyze_file_syntax_error(tmp_path: Path) -> None:
    """analyze_file returns 1 for file with syntax error."""
    test_file = tmp_path / "syntax_error.py"
    test_file.write_text("def broken(:\n")
    result = analyze_file(test_file)
    assert result == 1


def test_analyze_file_mixed_format(tmp_path: Path) -> None:
    """analyze_file returns 1 for file with mixed dict/list format."""
    content = """
positions = [
    {"position": {"x": 1.0, "y": 2.0, "z": 3.0}},
    {"position": [4.0, 5.0, 6.0]},
]
"""
    test_file = tmp_path / "mixed.py"
    test_file.write_text(content)
    result = analyze_file(test_file)
    assert result == 1


def test_analyze_file_nonexistent(tmp_path: Path) -> None:
    """analyze_file returns 1 for nonexistent file."""
    test_file = tmp_path / "nonexistent.py"
    result = analyze_file(test_file)
    assert result == 1


# ---------------------------------------------------------------------------
# CLI / main tests
# ---------------------------------------------------------------------------


def test_main_cli_help() -> None:
    """main --help exits 0 and shows usage."""
    with pytest.raises(SystemExit) as exc_info:
        import red_team_mixed_vector_format
        red_team_mixed_vector_format.main(["--help"])
    assert exc_info.value.code == 0


def test_main_cli_no_args() -> None:
    """main with no args runs on sample data (which is mixed, returns 1)."""
    import red_team_mixed_vector_format
    result = red_team_mixed_vector_format.main([])
    # SAMPLE_POSITIONS has mixed format, so it should return 1
    assert result == 1


def test_main_cli_with_valid_file(tmp_path: Path) -> None:
    """main with valid file argument returns 0."""
    content = """
positions = [
    {"position": {"x": 1.0, "y": 2.0, "z": 3.0}},
]
"""
    test_file = tmp_path / "valid.py"
    test_file.write_text(content)
    import red_team_mixed_vector_format
    result = red_team_mixed_vector_format.main([str(test_file)])
    assert result == 0


def test_main_cli_with_mixed_file(tmp_path: Path) -> None:
    """main with mixed format file returns 1."""
    content = """
positions = [
    {"position": {"x": 1.0, "y": 2.0, "z": 3.0}},
    {"position": [4.0, 5.0, 6.0]},
]
"""
    test_file = tmp_path / "mixed.py"
    test_file.write_text(content)
    import red_team_mixed_vector_format
    result = red_team_mixed_vector_format.main([str(test_file)])
    assert result == 1


def test_subprocess_help() -> None:
    """Subprocess running the script with --help exits 0."""
    result = subprocess.run(
        [sys.executable, str(_BIN_DIR / "red_team_mixed_vector_format.py"), "--help"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_subprocess_end_to_end(tmp_path: Path) -> None:
    """Subprocess end-to-end with valid file returns 0."""
    content = """
positions = [
    {"position": [1.0, 2.0, 3.0]},
    {"position": [4.0, 5.0, 6.0]},
]
"""
    test_file = tmp_path / "valid_list.py"
    test_file.write_text(content)
    result = subprocess.run(
        [sys.executable, str(_BIN_DIR / "red_team_mixed_vector_format.py"), str(test_file)],
        capture_output=True,
    )
    assert result.returncode == 0
    assert b"consistent" in result.stdout or b"Format" in result.stdout
