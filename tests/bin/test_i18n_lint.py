#!/usr/bin/env python3
"""Tests for bin/i18n_lint.py — i18n translation file linter.

Covers:
- extract_placeholders: extracts `{name}` placeholders; unique set semantics;
  handles multiple placeholders, no placeholders, nested braces (best-effort
  regex).
- load_json_file: happy path; FileNotFoundError → sys.exit(1); JSONDecodeError
  → sys.exit(1).
- lint_translations: missing directory → False; missing key in zh/ja raises
  an error; extra key in zh/ja raises a warning; empty-string value is
  detected as a warning (regression for the `if not value and value != ""`
  dead-code bug); placeholder mismatch raises an error; clean fixture
  returns True with no warnings/errors.
- main CLI: clean directory exits 0; dirty directory (errors) exits 1;
  accepts custom directory positional arg; defaults to dashboard/i18n when
  no arg given.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import i18n_lint as m  # noqa: E402

# ---------------------------------------------------------------------------
# extract_placeholders
# ---------------------------------------------------------------------------


class TestExtractPlaceholders:
    """extract_placeholders returns a set of placeholder names."""

    def test_no_placeholders(self):
        """Plain text with no braces → empty set."""
        assert m.extract_placeholders("hello world") == set()

    def test_single_placeholder(self):
        """Single `{name}` → set with one element."""
        assert m.extract_placeholders("{count} sessions") == {"count"}

    def test_multiple_placeholders(self):
        """Multiple `{a}` and `{b}` → set with both."""
        assert m.extract_placeholders("{a} and {b}") == {"a", "b"}

    def test_duplicate_placeholders_deduped(self):
        """Repeated `{x}` → set with one element (set semantics)."""
        assert m.extract_placeholders("{x} then {x} again") == {"x"}

    def test_empty_string(self):
        """Empty string → empty set."""
        assert m.extract_placeholders("") == set()


# ---------------------------------------------------------------------------
# load_json_file
# ---------------------------------------------------------------------------


class TestLoadJsonFile:
    """load_json_file loads a JSON file or sys.exit(1)s on error."""

    def test_valid_json(self, tmp_path: Path):
        """Valid JSON file → dict contents returned."""
        p = tmp_path / "ok.json"
        p.write_text('{"a": 1, "b": "two"}', encoding="utf-8")
        assert m.load_json_file(p) == {"a": 1, "b": "two"}

    def test_unicode_json(self, tmp_path: Path):
        """UTF-8 content with non-ASCII characters is decoded correctly."""
        p = tmp_path / "i18n.json"
        p.write_text('{"greet": "你好"}', encoding="utf-8")
        assert m.load_json_file(p) == {"greet": "你好"}

    def test_missing_file_exits(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """FileNotFoundError → sys.exit(1) and a 'File not found' message."""
        p = tmp_path / "missing.json"
        with pytest.raises(SystemExit) as exc:
            m.load_json_file(p)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "File not found" in out

    def test_invalid_json_exits(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Malformed JSON → sys.exit(1) and a 'Error parsing' message."""
        p = tmp_path / "bad.json"
        p.write_text("{this is not json", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            m.load_json_file(p)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Error parsing" in out


# ---------------------------------------------------------------------------
# lint_translations
# ---------------------------------------------------------------------------


def _write_i18n_dir(
    base: Path,
    en: dict,
    zh: dict,
    ja: dict,
) -> Path:
    """Helper: write en/zh/ja JSON files under `base` and return base path."""
    (base / "en.json").write_text(json.dumps(en), encoding="utf-8")
    (base / "zh-CN.json").write_text(json.dumps(zh), encoding="utf-8")
    (base / "ja-JP.json").write_text(json.dumps(ja), encoding="utf-8")
    return base


class TestLintTranslations:
    """lint_translations walks the three translation files and emits diagnostics."""

    def test_missing_directory_returns_false(self, tmp_path: Path):
        """Non-existent i18n directory → False."""
        bogus = tmp_path / "does-not-exist"
        assert m.lint_translations(bogus) is False

    def test_clean_fixture_returns_true(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """All three files have the same keys, no empty values, no placeholder
        mismatches → returns True and prints PASS."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello", "b": "{n} items"},
            zh={"a": "你好", "b": "{n} 项"},
            ja={"a": "こんにちは", "b": "{n} 個"},
        )
        assert m.lint_translations(tmp_path) is True
        out = capsys.readouterr().out
        assert "All checks passed" in out

    def test_missing_zh_key_raises_error(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Key present in en but missing in zh → an error is reported."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello", "b": "world"},
            zh={"a": "你好"},  # 'b' missing
            ja={"a": "こんにちは", "b": "世界"},
        )
        assert m.lint_translations(tmp_path) is False
        out = capsys.readouterr().out
        assert "Missing keys in zh-CN.json" in out
        assert "'b'" in out

    def test_missing_ja_key_raises_error(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Key present in en but missing in ja → an error is reported."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello", "b": "world"},
            zh={"a": "你好", "b": "世界"},
            ja={"a": "こんにちは"},  # 'b' missing
        )
        assert m.lint_translations(tmp_path) is False
        out = capsys.readouterr().out
        assert "Missing keys in ja-JP.json" in out

    def test_extra_zh_key_emits_warning(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Extra key in zh not present in en → warning, not error."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello"},
            zh={"a": "你好", "extra": "额外"},
            ja={"a": "こんにちは"},
        )
        # No errors → returns True
        assert m.lint_translations(tmp_path) is True
        out = capsys.readouterr().out
        assert "Extra keys in zh-CN.json" in out
        assert "extra" in out

    def test_extra_ja_key_emits_warning(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Extra key in ja not present in en → warning, not error."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello"},
            zh={"a": "你好"},
            ja={"a": "こんにちは", "extra": "余分"},
        )
        assert m.lint_translations(tmp_path) is True
        out = capsys.readouterr().out
        assert "Extra keys in ja-JP.json" in out

    def test_empty_string_in_en_is_warning(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """REGRESSION: empty value in en.json must produce a warning.

        Previously the code was ``if not value and value != ""`` which is
        logically impossible for string JSON values, so empty strings were
        silently swallowed. This test guards against the regression.
        """
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello", "b": ""},
            zh={"a": "你好", "b": ""},
            ja={"a": "こんにちは", "b": ""},
        )
        # No errors (empty string is a warning) → returns True
        assert m.lint_translations(tmp_path) is True
        out = capsys.readouterr().out
        assert "Empty string in en.json: 'b'" in out

    def test_empty_string_in_zh_is_warning(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """REGRESSION: empty value in zh-CN.json must produce a warning."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello", "b": "world"},
            zh={"a": "你好", "b": ""},
            ja={"a": "こんにちは", "b": "世界"},
        )
        assert m.lint_translations(tmp_path) is True
        out = capsys.readouterr().out
        assert "Empty string in zh-CN.json: 'b'" in out

    def test_empty_string_in_ja_is_warning(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """REGRESSION: empty value in ja-JP.json must produce a warning."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello", "b": "world"},
            zh={"a": "你好", "b": "世界"},
            ja={"a": "こんにちは", "b": ""},
        )
        assert m.lint_translations(tmp_path) is True
        out = capsys.readouterr().out
        assert "Empty string in ja-JP.json: 'b'" in out

    def test_placeholder_mismatch_in_zh_raises_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        """Placeholder set in en differs from zh for the same key → error."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "you have {count} messages"},
            zh={"a": "你有 {num} 条消息"},  # {num} not {count}
            ja={"a": "{count} 件のメッセージ"},
        )
        assert m.lint_translations(tmp_path) is False
        out = capsys.readouterr().out
        assert "Placeholder mismatch" in out
        assert "'a'" in out

    def test_placeholder_mismatch_in_ja_raises_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        """Placeholder set in en differs from ja for the same key → error."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "you have {count} messages"},
            zh={"a": "你有 {count} 条消息"},
            ja={"a": "{num} 件のメッセージ"},  # {num} not {count}
        )
        assert m.lint_translations(tmp_path) is False
        out = capsys.readouterr().out
        assert "Placeholder mismatch" in out

    def test_summary_prints_key_counts(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Summary section shows the per-language key counts."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "1", "b": "2", "c": "3"},
            zh={"a": "一", "b": "二", "c": "三"},
            ja={"a": "一", "b": "二", "c": "三"},
        )
        m.lint_translations(tmp_path)
        out = capsys.readouterr().out
        assert "English keys: 3" in out
        assert "Chinese keys: 3" in out
        assert "Japanese keys: 3" in out


# ---------------------------------------------------------------------------
# main CLI
# ---------------------------------------------------------------------------


class TestMainCli:
    """main() drives the linter as a CLI; clean → 0, errors → 1."""

    def _valid_dir(self, tmp_path: Path) -> Path:
        return _write_i18n_dir(
            tmp_path,
            en={"a": "hello"},
            zh={"a": "你好"},
            ja={"a": "こんにちは"},
        )

    def test_clean_directory_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        """A clean i18n directory → main() exits 0."""
        d = self._valid_dir(tmp_path)
        with mock.patch.object(sys, "argv", ["i18n_lint.py", str(d)]):
            m.main()
        # No SystemExit raised
        out = capsys.readouterr().out
        assert "All checks passed" in out

    def test_dirty_directory_exits_one(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """A missing-key error → main() sys.exit(1)."""
        _write_i18n_dir(
            tmp_path,
            en={"a": "hello", "b": "world"},
            zh={"a": "你好"},  # missing 'b'
            ja={"a": "こんにちは", "b": "世界"},
        )
        with mock.patch.object(sys, "argv", ["i18n_lint.py", str(tmp_path)]):
            with pytest.raises(SystemExit) as exc:
                m.main()
        assert exc.value.code == 1

    def test_default_directory_arg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ):
        """No positional arg → defaults to 'dashboard/i18n' (relative to cwd)."""
        # Set up a fake cwd where dashboard/i18n exists
        cwd = tmp_path
        i18n = cwd / "dashboard" / "i18n"
        i18n.mkdir(parents=True)
        _write_i18n_dir(
            i18n,
            en={"a": "hello"},
            zh={"a": "你好"},
            ja={"a": "こんにちは"},
        )
        monkeypatch.chdir(cwd)
        with mock.patch.object(sys, "argv", ["i18n_lint.py"]):
            m.main()
        out = capsys.readouterr().out
        assert "All checks passed" in out
