#!/usr/bin/env python3
"""Tests for bin/error_severity_classifier.py — Error severity classifier.

Covers:
- Severity class: constants, DESCRIPTIONS, PRIORITY ordering, is_valid
- RuleEngine: classify() across the full DEFAULT_RULES rule set,
  case-insensitive matching, override-path handling (valid JSON, missing
  file, malformed JSON, empty JSON, unknown extension, no 'rules' key)
- parse_args / main CLI: required flags, default values, json vs text
  output, --verbose description, exit codes (0 success / 1 missing
  args / unknown severity still exit 0 because UNKNOWN is valid)
- Silent-error-swallow contract: malformed override must NOT crash; it
  must fall through to DEFAULT_RULES.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make bin/ importable as a top-level module package
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from bin.error_severity_classifier import (  # noqa: E402
    DEFAULT_RULES,
    RuleEngine,
    Severity,
    main,
    parse_args,
)

# ---------------------------------------------------------------------------
# Severity class
# ---------------------------------------------------------------------------


class TestSeverityConstants:
    """Severity class exposes the 5 expected level constants + helpers."""

    def test_severity_levels(self):
        """All five named levels are exposed as class attributes."""
        assert Severity.CRITICAL == "critical"
        assert Severity.HIGH == "high"
        assert Severity.MEDIUM == "medium"
        assert Severity.LOW == "low"
        assert Severity.UNKNOWN == "unknown"

    def test_descriptions_complete(self):
        """Every severity level has a non-empty human description."""
        for lvl in (
            Severity.CRITICAL,
            Severity.HIGH,
            Severity.MEDIUM,
            Severity.LOW,
            Severity.UNKNOWN,
        ):
            assert lvl in Severity.DESCRIPTIONS
            assert isinstance(Severity.DESCRIPTIONS[lvl], str)
            assert Severity.DESCRIPTIONS[lvl].strip() != ""

    def test_priority_ordering(self):
        """CRITICAL < HIGH < MEDIUM < LOW < UNKNOWN (lower = more severe)."""
        assert Severity.PRIORITY[Severity.CRITICAL] < Severity.PRIORITY[Severity.HIGH]
        assert Severity.PRIORITY[Severity.HIGH] < Severity.PRIORITY[Severity.MEDIUM]
        assert Severity.PRIORITY[Severity.MEDIUM] < Severity.PRIORITY[Severity.LOW]
        assert Severity.PRIORITY[Severity.LOW] < Severity.PRIORITY[Severity.UNKNOWN]

    def test_is_valid(self):
        """is_valid accepts the 5 named levels and rejects arbitrary strings."""
        for lvl in (
            Severity.CRITICAL,
            Severity.HIGH,
            Severity.MEDIUM,
            Severity.LOW,
            Severity.UNKNOWN,
        ):
            assert Severity.is_valid(lvl) is True
        assert Severity.is_valid("fatal") is False
        assert Severity.is_valid("") is False
        assert Severity.is_valid("CRITICAL") is False  # case-sensitive


# ---------------------------------------------------------------------------
# DEFAULT_RULES structure
# ---------------------------------------------------------------------------


class TestDefaultRules:
    """DEFAULT_RULES is the canonical rule table; verify its structure."""

    def test_default_rules_nonempty(self):
        """There is at least one default rule loaded."""
        assert len(DEFAULT_RULES) >= 1

    def test_default_rules_shape(self):
        """Each default rule is a 4-tuple (pattern, pattern, keywords, severity)."""
        for rule in DEFAULT_RULES:
            assert isinstance(rule, tuple)
            assert len(rule) == 4
            # first two are regex patterns (strings)
            assert isinstance(rule[0], str)
            assert isinstance(rule[1], str)
            # third is keyword regex or ".*"
            assert isinstance(rule[2], str)
            # fourth is a severity string
            assert rule[3] in Severity.PRIORITY

    def test_default_rules_severities_are_known(self):
        """No rule references an unknown severity constant."""
        seen = {r[3] for r in DEFAULT_RULES}
        for s in seen:
            assert s in Severity.PRIORITY


# ---------------------------------------------------------------------------
# RuleEngine.classify — default rules
# ---------------------------------------------------------------------------


class TestRuleEngineClassifyDefaults:
    """RuleEngine with no overrides classifies via DEFAULT_RULES."""

    def setup_method(self):
        self.engine = RuleEngine()

    # ---- CRITICAL ----

    def test_auth_module_unauthorized(self):
        """Auth module + 'unauthorized' traceback → CRITICAL."""
        assert self.engine.classify("ValueError", "auth.login", "unauthorized") == Severity.CRITICAL

    def test_payment_module_failed(self):
        """Payment module + 'failed' → CRITICAL."""
        assert self.engine.classify("Exception", "billing.invoice", "failed") == Severity.CRITICAL

    def test_database_data_loss(self):
        """Database module + 'data loss' → CRITICAL."""
        assert (
            self.engine.classify("Exception", "db.user", "data loss detected") == Severity.CRITICAL
        )

    def test_out_of_memory(self):
        """OutOfMemoryError anywhere → CRITICAL."""
        assert self.engine.classify("OutOfMemoryError", "any.module", "killed") == Severity.CRITICAL

    def test_stack_overflow(self):
        """StackOverflowError anywhere → CRITICAL."""
        assert self.engine.classify("StackOverflowError", "any.module", "") == Severity.CRITICAL

    # ---- HIGH ----

    def test_null_pointer(self):
        """NullPointerException → HIGH."""
        assert self.engine.classify("NullPointerException", "api.handler", "") == Severity.HIGH

    def test_none_type_in_traceback(self):
        """'NoneType' substring → HIGH."""
        assert (
            self.engine.classify("AttributeError", "utils.helper", "NoneType has no attribute foo")
            == Severity.HIGH
        )

    def test_connection_refused(self):
        """ConnectionRefused → HIGH."""
        assert self.engine.classify("Exception", "net.client", "ConnectionRefused") == Severity.HIGH

    def test_runtime_error(self):
        """RuntimeError matches the high-severity rule via error-class regex."""
        assert self.engine.classify("RuntimeError", "any.module", "") == Severity.HIGH

    def test_importerror(self):
        """ImportError → HIGH."""
        assert self.engine.classify("ImportError", "any.module", "") == Severity.HIGH

    # ---- MEDIUM ----

    def test_timeout_keyword(self):
        """Traceback with 'timeout' → MEDIUM."""
        assert (
            self.engine.classify("ValueError", "api.handler", "request timeout") == Severity.MEDIUM
        )

    def test_circuit_breaker_keyword(self):
        """Traceback mentioning 'circuit breaker' → MEDIUM."""
        assert (
            self.engine.classify("Exception", "client.http", "circuit breaker open")
            == Severity.MEDIUM
        )

    def test_degraded_keyword(self):
        """Traceback with 'degraded' → MEDIUM."""
        assert (
            self.engine.classify("Exception", "service.core", "performance degraded")
            == Severity.MEDIUM
        )

    # ---- LOW ----

    def test_warning_keyword(self):
        """Traceback with 'warning' → LOW."""
        assert self.engine.classify("UserWarning", "logger.util", "warning issued") == Severity.LOW

    # ---- UNKNOWN ----

    def test_unmatched_falls_through_to_unknown(self):
        """No matching rule → UNKNOWN."""
        assert self.engine.classify("WeirdError", "no.match", "nothing here") == Severity.UNKNOWN

    def test_unknown_module_and_empty_traceback(self):
        """Unrelated module, empty traceback → UNKNOWN."""
        assert self.engine.classify("FooError", "mystery.module", "") == Severity.UNKNOWN

    # ---- Case insensitivity ----

    def test_case_insensitive_error_class(self):
        """Pattern matching is case-insensitive (re.IGNORECASE)."""
        assert self.engine.classify("valueerror", "payment.service", "") == Severity.CRITICAL

    def test_case_insensitive_module(self):
        """Module name match is case-insensitive."""
        assert self.engine.classify("Exception", "AUTH.LOGIN", "unauthorized") == Severity.CRITICAL


# ---------------------------------------------------------------------------
# RuleEngine — constructor with explicit rules
# ---------------------------------------------------------------------------


class TestRuleEngineCustomRules:
    """RuleEngine accepts a custom rules list and bypasses DEFAULT_RULES."""

    def test_custom_rules_take_precedence(self):
        """When rules= is provided, DEFAULT_RULES is ignored."""
        custom = [(".*", ".*", ".*", Severity.LOW)]
        engine = RuleEngine(rules=custom)
        # Even an OutOfMemoryError falls through the custom rule to LOW
        assert engine.classify("OutOfMemoryError", "any.module", "killed") == Severity.LOW

    def test_custom_rules_explicit_copy(self):
        """Passing DEFAULT_RULES.copy() gives a mutable working set."""
        rules = DEFAULT_RULES.copy()
        engine = RuleEngine(rules=rules)
        # Sanity check
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL


# ---------------------------------------------------------------------------
# RuleEngine — override file handling
# ---------------------------------------------------------------------------


class TestRuleEngineOverrides:
    """Override-path handling: valid / missing / malformed / wrong ext."""

    def test_no_override_path_is_noop(self):
        """override_path=None must not raise and must use DEFAULT_RULES."""
        engine = RuleEngine(override_path=None)
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL

    def test_missing_override_file_is_silent_noop(self, tmp_path: Path):
        """Nonexistent override file → silently ignored, DEFAULT_RULES used."""
        missing = tmp_path / "no_such_file.json"
        engine = RuleEngine(override_path=missing)
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL

    def test_malformed_json_override_is_silent_noop(self, tmp_path: Path):
        """Malformed JSON in override file must NOT raise — fall through."""
        bad = tmp_path / "bad.json"
        bad.write_text("not valid json{{{", encoding="utf-8")
        engine = RuleEngine(override_path=bad)
        # DEFAULT_RULES still applies
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL

    def test_valid_json_override_replaces_rules(self, tmp_path: Path):
        """A valid JSON override's 'rules' list pre-pends to DEFAULT_RULES."""
        ovr = tmp_path / "ovr.json"
        ovr.write_text(
            json.dumps(
                {
                    "rules": [
                        ["FooBar", ".*", ".*", Severity.LOW],
                    ]
                }
            ),
            encoding="utf-8",
        )
        engine = RuleEngine(override_path=ovr)
        # Override matches first
        assert engine.classify("FooBar", "x.y", "z") == Severity.LOW
        # Default rules still apply for unmatched
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL

    def test_override_without_rules_key_is_noop(self, tmp_path: Path):
        """Valid JSON but missing 'rules' key → silently ignored."""
        ovr = tmp_path / "no_rules.json"
        ovr.write_text(json.dumps({"other": "stuff"}), encoding="utf-8")
        engine = RuleEngine(override_path=ovr)
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL

    def test_override_empty_json_object_is_noop(self, tmp_path: Path):
        """Empty JSON object → 'rules' key absent → silently ignored."""
        ovr = tmp_path / "empty.json"
        ovr.write_text("{}", encoding="utf-8")
        engine = RuleEngine(override_path=ovr)
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL

    def test_override_unknown_extension_is_silent_noop(self, tmp_path: Path):
        """Override file with unknown extension is silently ignored."""
        ovr = tmp_path / "ovr.txt"
        ovr.write_text("rules go here", encoding="utf-8")
        engine = RuleEngine(override_path=ovr)
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL

    def test_override_yaml_extension_without_yaml_module(self, tmp_path: Path, monkeypatch):
        """YAML override with yaml import failing → silently ignored."""
        # Simulate yaml not being importable
        import bin.error_severity_classifier as m

        monkeypatch.setattr(m, "YAML_AVAILABLE", False)
        ovr = tmp_path / "ovr.yaml"
        ovr.write_text("rules: []", encoding="utf-8")
        engine = RuleEngine(override_path=ovr)
        # Defaults still apply
        assert engine.classify("OutOfMemoryError", "any.module", "") == Severity.CRITICAL


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """argparse wrapper: required flags, defaults, choices, --help exits."""

    def test_required_flags(self):
        """--error-class and --module are required."""
        with pytest.raises(SystemExit):
            parse_args([])
        with pytest.raises(SystemExit):
            parse_args(["--error-class", "ValueError"])  # missing --module

    def test_defaults(self):
        """Optional flags have the documented defaults."""
        args = parse_args(["--error-class", "ValueError", "--module", "api.handler"])
        assert args.error_class == "ValueError"
        assert args.module == "api.handler"
        assert args.traceback == ""
        assert args.override is None
        assert args.format == "text"
        assert args.verbose is False

    def test_explicit_flags(self):
        """All optional flags populate the namespace correctly."""
        args = parse_args(
            [
                "--error-class",
                "RuntimeError",
                "--module",
                "db.pool",
                "--traceback",
                "pool exhausted",
                "--override",
                "/tmp/x.json",
                "--format",
                "json",
                "--verbose",
            ]
        )
        assert args.error_class == "RuntimeError"
        assert args.module == "db.pool"
        assert args.traceback == "pool exhausted"
        assert args.override == Path("/tmp/x.json")
        assert args.format == "json"
        assert args.verbose is True

    def test_short_flags(self):
        """Short flag forms -e -m -t -o -f -v are accepted."""
        args = parse_args(
            [
                "-e",
                "Exception",
                "-m",
                "auth.session",
                "-t",
                "unauthorized",
                "-o",
                "/tmp/x.yaml",
                "-f",
                "json",
                "-v",
            ]
        )
        assert args.error_class == "Exception"
        assert args.module == "auth.session"
        assert args.traceback == "unauthorized"
        assert args.override == Path("/tmp/x.yaml")
        assert args.format == "json"
        assert args.verbose is True

    def test_invalid_format_choice_rejected(self):
        """--format only accepts 'text' or 'json'."""
        with pytest.raises(SystemExit):
            parse_args(
                [
                    "--error-class",
                    "ValueError",
                    "--module",
                    "x.y",
                    "--format",
                    "yaml",
                ]
            )


# ---------------------------------------------------------------------------
# main() CLI entry point
# ---------------------------------------------------------------------------


class TestMain:
    """End-to-end CLI behaviour via main()."""

    def test_main_text_output(self, capsys):
        """Default text format prints the severity on its own line."""
        rc = main(
            [
                "--error-class",
                "ValueError",
                "--module",
                "payment.service",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert Severity.CRITICAL in out

    def test_main_json_output(self, capsys):
        """--format json prints a JSON object with error_class / module / severity."""
        rc = main(
            [
                "--error-class",
                "ValueError",
                "--module",
                "api.handler",
                "--traceback",
                "timeout occurred",
                "--format",
                "json",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["error_class"] == "ValueError"
        assert payload["module"] == "api.handler"
        assert payload["severity"] == Severity.MEDIUM

    def test_main_verbose_adds_description(self, capsys):
        """--verbose adds a 'description' field in JSON output."""
        rc = main(
            [
                "--error-class",
                "OutOfMemoryError",
                "--module",
                "renderer.core",
                "--format",
                "json",
                "--verbose",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert "description" in payload
        assert payload["description"] == Severity.DESCRIPTIONS[Severity.CRITICAL]

    def test_main_verbose_text_includes_description(self, capsys):
        """--verbose in text mode adds a 2nd line with the description."""
        rc = main(
            [
                "--error-class",
                "NullPointerException",
                "--module",
                "api.handler",
                "--verbose",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert lines[0] == Severity.HIGH
        assert Severity.DESCRIPTIONS[Severity.HIGH] in out

    def test_main_missing_required_args_returns_1(self, capsys):
        """Missing required args → argparse SystemExit → main returns 1."""
        rc = main([])
        assert rc == 1
        # argparse wrote to stderr; we don't pin the message
        _ = capsys.readouterr()

    def test_main_unknown_classification_still_exits_0(self, capsys):
        """UNKNOWN is a valid result, not an error → exit 0."""
        rc = main(
            [
                "--error-class",
                "WeirdError",
                "--module",
                "no.match",
            ]
        )
        assert rc == 0
        assert Severity.UNKNOWN in capsys.readouterr().out

    def test_main_with_override_file(self, tmp_path: Path, capsys):
        """--override loads a JSON override and uses its rule."""
        ovr = tmp_path / "ovr.json"
        ovr.write_text(
            json.dumps({"rules": [["CustomError", ".*", ".*", Severity.LOW]]}),
            encoding="utf-8",
        )
        rc = main(
            [
                "--error-class",
                "CustomError",
                "--module",
                "anywhere",
                "--override",
                str(ovr),
                "--format",
                "json",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["severity"] == Severity.LOW

    def test_main_with_missing_override_file_does_not_crash(self, capsys):
        """Missing override file → silently ignored, falls back to defaults."""
        rc = main(
            [
                "--error-class",
                "OutOfMemoryError",
                "--module",
                "x.y",
                "--override",
                "/tmp/definitely_does_not_exist_xyz.json",
            ]
        )
        assert rc == 0
        assert Severity.CRITICAL in capsys.readouterr().out

    def test_main_with_malformed_override_does_not_crash(self, tmp_path: Path, capsys):
        """Malformed override JSON must NOT crash main()."""
        bad = tmp_path / "bad.json"
        bad.write_text("not json{{{", encoding="utf-8")
        rc = main(
            [
                "--error-class",
                "OutOfMemoryError",
                "--module",
                "x.y",
                "--override",
                str(bad),
            ]
        )
        assert rc == 0
        # DEFAULT_RULES still produced CRITICAL
        assert Severity.CRITICAL in capsys.readouterr().out
