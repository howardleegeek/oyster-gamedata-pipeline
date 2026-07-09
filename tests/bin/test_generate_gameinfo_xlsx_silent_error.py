#!/usr/bin/env python3
"""Regression test: surface silent error in bin/generate_gameinfo_xlsx._load_json_object.

The bare ``except (OSError, json.JSONDecodeError): return None`` was replaced
with a bound exception + ``logger.debug(...)`` so a missing or malformed
JSON file is no longer invisible to operators. Control flow is preserved:
a corrupt or missing file still returns ``None`` so callers (game version
detection, FPS log loader, etc.) behave identically.

Round 421: Surface silent error in _load_json_object.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin import generate_gameinfo_xlsx  # noqa: E402


def test_module_compiles():
    """Module must compile (py_compile)."""
    import py_compile

    path = (
        Path(__file__).resolve().parent.parent.parent
        / "bin"
        / "generate_gameinfo_xlsx.py"
    )
    py_compile.compile(str(path), doraise=True)


def test_load_json_object_missing_file_returns_none(tmp_path: Path) -> None:
    """Control flow preserved: missing file → None (not raise)."""
    missing = tmp_path / "does_not_exist.json"
    result = generate_gameinfo_xlsx._load_json_object(missing)
    assert result is None


def test_load_json_object_corrupt_json_returns_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt JSON → None AND a DEBUG log record surfaces the failure."""
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not valid json,,,,")
    with caplog.at_level(
        logging.DEBUG, logger="bin.generate_gameinfo_xlsx"
    ):
        result = generate_gameinfo_xlsx._load_json_object(corrupt)
    assert result is None
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, (
        "expected a DEBUG log record surfacing the JSON parse failure"
    )
    msg = debug_records[0].getMessage()
    assert "generate_gameinfo_xlsx" in msg
    assert str(corrupt) in msg or corrupt.name in msg


def test_load_json_object_missing_file_emits_debug_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing file → None AND a DEBUG log record surfaces the OSError."""
    missing = tmp_path / "ghost.json"
    with caplog.at_level(
        logging.DEBUG, logger="bin.generate_gameinfo_xlsx"
    ):
        result = generate_gameinfo_xlsx._load_json_object(missing)
    assert result is None
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, (
        "expected a DEBUG log record surfacing the missing-file OSError"
    )
    msg = debug_records[0].getMessage()
    assert "generate_gameinfo_xlsx" in msg
    assert str(missing) in msg or missing.name in msg


def test_load_json_object_valid_json_returns_parsed(tmp_path: Path) -> None:
    """Happy path preserved: a valid JSON file returns the parsed object."""
    good = tmp_path / "good.json"
    good.write_text('{"a": 1, "b": [2, 3]}')
    result = generate_gameinfo_xlsx._load_json_object(good)
    assert result == {"a": 1, "b": [2, 3]}
