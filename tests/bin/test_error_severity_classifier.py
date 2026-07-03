"""Tests for the override-load error path in bin/error_severity_classifier.py.

Round 240 — verify the silent `except Exception: pass` in
``RuleEngine._load_overrides`` is gone and that bad override files now log
at WARNING and fall back to default rules.

Cases:
  1. No override path → default rules loaded, no log emitted.
  2. Valid JSON override → rules prepended, no warning logged.
  3. Malformed JSON override → rules stay at default, WARNING logged with
     the underlying parse error.
  4. Override file with wrong-shape payload (no "rules" key) → rules stay
     at default, no warning (this is a legitimate empty override).
  5. Override file with permission error → rules stay at default, WARNING
     logged with the underlying OS error.
  6. Override file with ``.yaml`` extension and YAML_AVAILABLE=False → no
     attempt to load, no warning.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from bin.error_severity_classifier import (
    DEFAULT_RULES,
    RuleEngine,
    Severity,
    YAML_AVAILABLE,
)


@pytest.fixture
def capture_log_records() -> list[logging.LogRecord]:
    """Capture WARNING+ log records emitted by the module under test."""
    from bin import error_severity_classifier as mod
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
            records.append(record)

    handler = _ListHandler(level=logging.DEBUG)
    mod.logger.addHandler(handler)
    prev_level = mod.logger.level
    mod.logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        mod.logger.removeHandler(handler)
        mod.logger.setLevel(prev_level)


def test_no_override_uses_defaults(capture_log_records: list[logging.LogRecord]) -> None:
    """No override_path → DEFAULT_RULES copied in, no log noise."""
    engine = RuleEngine()
    assert engine.rules == DEFAULT_RULES
    assert capture_log_records == []


def test_valid_json_override_prepended(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """A well-formed JSON override is prepended to the default rules."""
    override = tmp_path / "rules.json"
    override.write_text(
        json.dumps({
            "rules": [
                [".*", ".*super_urgent.*", ".*", Severity.CRITICAL],
            ],
        }),
        encoding="utf-8",
    )
    engine = RuleEngine(override_path=override)
    # Override rule is prepended, defaults still present.
    assert engine.rules[0][1] == ".*super_urgent.*"
    assert len(engine.rules) == len(DEFAULT_RULES) + 1
    # And it actually classifies:
    assert engine.classify("AnythingError", "super_urgent", "") == Severity.CRITICAL
    assert capture_log_records == []


def test_malformed_json_override_logs_warning_and_falls_back(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """Bad JSON → WARNING logged with parse error, defaults preserved."""
    override = tmp_path / "bad.json"
    override.write_text("{not valid json", encoding="utf-8")
    engine = RuleEngine(override_path=override)
    assert engine.rules == DEFAULT_RULES
    warnings = [r for r in capture_log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected 1 WARNING, got {warnings}"
    msg = warnings[0].getMessage()
    assert str(override) in msg
    assert "default rules" in msg
    # And the underlying error class is exposed via exc_info if attached.
    assert warnings[0].exc_info is not None


def test_yaml_malformed_override_logs_warning_when_yaml_available(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """Bad YAML → WARNING logged with parse error, defaults preserved (if yaml installed)."""
    if not YAML_AVAILABLE:
        pytest.skip("PyYAML not installed in this environment")
    override = tmp_path / "bad.yaml"
    override.write_text("rules: [unterminated", encoding="utf-8")
    engine = RuleEngine(override_path=override)
    assert engine.rules == DEFAULT_RULES
    warnings = [r for r in capture_log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "default rules" in warnings[0].getMessage()


def test_override_without_rules_key_is_silent(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """An override that parses but lacks a 'rules' key is a legitimate empty
    override — no warning expected, defaults remain."""
    override = tmp_path / "empty.json"
    override.write_text(json.dumps({"note": "no rules here"}), encoding="utf-8")
    engine = RuleEngine(override_path=override)
    assert engine.rules == DEFAULT_RULES
    assert capture_log_records == []


def test_unreadable_override_logs_warning(
    capture_log_records: list[logging.LogRecord], tmp_path: Path,
) -> None:
    """An override file we can't read (chmod 000) → WARNING, defaults preserved."""
    if os.geteuid() == 0:  # root bypasses file-mode bits; skip on CI-as-root
        pytest.skip("running as root; chmod 000 does not deny read")
    override = tmp_path / "locked.json"
    override.write_text(json.dumps({"rules": []}), encoding="utf-8")
    override.chmod(stat.S_IRUSR ^ stat.S_IRWXU ^ stat.S_IRWXG ^ stat.S_IRWXO)  # 0o000
    try:
        engine = RuleEngine(override_path=override)
    finally:
        # Restore so tmp_path cleanup can remove the file.
        override.chmod(stat.S_IRWXU)
    assert engine.rules == DEFAULT_RULES
    warnings = [r for r in capture_log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(override) in warnings[0].getMessage()


def test_override_yaml_unavailable_is_silent(
    capture_log_records: list[logging.LogRecord], tmp_path: Path, monkeypatch,
) -> None:
    """A .yaml override with YAML_AVAILABLE=False → silent skip, no warning."""
    from bin import error_severity_classifier as mod
    monkeypatch.setattr(mod, "YAML_AVAILABLE", False)
    override = tmp_path / "rules.yaml"
    override.write_text("rules: []", encoding="utf-8")
    engine = RuleEngine(override_path=override)
    assert engine.rules == DEFAULT_RULES
    assert capture_log_records == []


def test_no_silent_pass_in_load_overrides() -> None:
    """Static guard: ensure the bare ``except Exception: pass`` is gone."""
    from bin import error_severity_classifier as mod
    import inspect
    src = inspect.getsource(mod.RuleEngine._load_overrides)
    # The original antipattern was a bare except followed by `pass`.
    # Our new implementation must not contain that exact pattern.
    assert "except Exception:\n            pass" not in src
    assert "except Exception:\n                pass" not in src
    # And the file should reference the logger (proves we route through it).
    assert "logger." in src
