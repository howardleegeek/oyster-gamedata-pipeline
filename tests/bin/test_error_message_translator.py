#!/usr/bin/env python3
"""Tests for bin/error_message_translator.py — exception-trace → vendor-friendly remediation.

Covers:
- RemediationRule dataclass (defaults, custom severity).
- ParsedTrace dataclass (defaults, custom fields).
- _RULES list invariants: non-empty, every rule has a non-empty pattern,
  friendly_title, remediation, and a known severity.
- _FALLBACK rule pattern matches anything.
- parse_traceback:
    * Returns None for a string with no recognisable traceback pattern.
    * Parses a single-line "ExceptionType: message" into a ParsedTrace.
    * Parses a full multi-line traceback and extracts frames.
    * Picks the last exception line (the actual exception, not chained
      cause lines) when multiple ``Exception: ...`` matches are present.
    * Returns an "UnknownError" ParsedTrace when frames are present but
      no exception line matches.
    * Raw text is preserved in the ``raw`` field.
- translate:
    * Returns the matching rule for a known exception type.
    * Uses ``_FALLBACK`` when no rule matches.
    * Is case-insensitive (matches lowercase exception type via rule
      that uses the bare class name).
- format_text:
    * Includes severity, title, exception type, message, remediation.
    * Skips the "Message" line when message is empty.
    * Verbose mode appends an abbreviated call stack (last 5 frames).
- format_json:
    * Returns valid JSON with the documented keys.
    * "message" is None when exception message is empty.
    * Verbose mode adds a "call_stack" list of at most 5 frames.
- main (CLI):
    * Reads traceback from --input file, prints text output, exits 0.
    * Reads from stdin (default), prints text output, exits 0.
    * Returns 1 and prints a stderr error when no recognisable traceback
      is present in the input.
    * --format json emits parseable JSON.
    * --verbose includes the call stack in the output.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import error_message_translator as m  # noqa: E402,I001


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestRemediationRule:
    """RemediationRule is a small frozen-style dataclass with severity default."""

    def test_defaults(self):
        """Severity defaults to 'warning' when not provided."""
        r = m.RemediationRule(
            pattern="X",
            friendly_title="X error",
            remediation="Do X",
        )
        assert r.pattern == "X"
        assert r.friendly_title == "X error"
        assert r.remediation == "Do X"
        assert r.severity == "warning"

    def test_custom_severity(self):
        """Severity can be overridden."""
        r = m.RemediationRule(
            pattern="X",
            friendly_title="X error",
            remediation="Do X",
            severity="critical",
        )
        assert r.severity == "critical"


class TestParsedTrace:
    """ParsedTrace holds the structured result of parsing a traceback string."""

    def test_defaults(self):
        """frames and raw default to empty list / empty string."""
        t = m.ParsedTrace(exception_type="E", exception_message="boom")
        assert t.exception_type == "E"
        assert t.exception_message == "boom"
        assert t.frames == []
        assert t.raw == ""

    def test_explicit_fields(self):
        """All fields are settable via the constructor."""
        t = m.ParsedTrace(
            exception_type="E",
            exception_message="boom",
            frames=['File "a.py", line 1, in f'],
            raw="raw text",
        )
        assert t.frames == ['File "a.py", line 1, in f']
        assert t.raw == "raw text"


# ---------------------------------------------------------------------------
# _RULES invariants
# ---------------------------------------------------------------------------


class TestRulesInvariants:
    """The built-in _RULES list is well-formed."""

    def test_rules_non_empty(self):
        """At least one rule is defined (the module would be useless otherwise)."""
        assert len(m._RULES) > 0

    def test_every_rule_has_fields(self):
        """No rule has an empty pattern / title / remediation."""
        for rule in m._RULES:
            assert rule.pattern, f"empty pattern in {rule!r}"
            assert rule.friendly_title, f"empty title in {rule!r}"
            assert rule.remediation, f"empty remediation in {rule!r}"

    def test_every_rule_severity_known(self):
        """Every rule severity is one of the canonical set."""
        for rule in m._RULES:
            assert rule.severity in {"info", "warning", "critical", "error"}, (
                f"unknown severity {rule.severity!r} in {rule!r}"
            )

    def test_fallback_is_match_all(self):
        """The module-level _FALLBACK matches any string."""
        assert re_matches(m._FALLBACK.pattern, "literally anything")
        assert re_matches(m._FALLBACK.pattern, "")


def re_matches(pattern: str, s: str) -> bool:
    """Tiny helper: does the regex ``pattern`` match ``s``?"""
    import re as _re

    return _re.search(pattern, s) is not None


# ---------------------------------------------------------------------------
# parse_traceback
# ---------------------------------------------------------------------------


class TestParseTraceback:
    """parse_traceback extracts structured data from a Python traceback string."""

    def test_none_for_garbage(self):
        """A string with no traceback pattern and no exception line returns None."""
        assert m.parse_traceback("hello world") is None
        assert m.parse_traceback("") is None

    def test_single_line_exception(self):
        """A bare ``ExceptionType: message`` line is parsed into a ParsedTrace."""
        t = m.parse_traceback("ValueError: bad value")
        assert t is not None
        assert t.exception_type == "ValueError"
        assert t.exception_message == "bad value"
        assert t.frames == []
        assert t.raw == "ValueError: bad value"

    def test_single_line_no_message(self):
        """``ExceptionType:`` with empty message has empty exception_message."""
        t = m.parse_traceback("ValueError:")
        assert t is not None
        assert t.exception_type == "ValueError"
        assert t.exception_message == ""

    def test_multiline_traceback_extracts_frames(self):
        """A standard multi-line traceback yields frames + exception."""
        raw = (
            "Traceback (most recent call last):\n"
            '  File "a.py", line 10, in outer\n'
            '  File "b.py", line 5, in inner\n'
            "ValueError: bad value\n"
        )
        t = m.parse_traceback(raw)
        assert t is not None
        assert t.exception_type == "ValueError"
        assert t.exception_message == "bad value"
        assert t.frames == [
            'File "a.py", line 10, in outer',
            'File "b.py", line 5, in inner',
        ]
        assert t.raw == raw

    def test_picks_last_exception_in_cause_chain(self):
        """When multiple exception lines exist (e.g. raise X from Y), the last wins."""
        raw = (
            "Traceback (most recent call last):\n"
            '  File "a.py", line 1, in f\n'
            "ValueError: first\n"
            "\n"
            "The above exception was the direct cause of the following exception:\n"
            "\n"
            "Traceback (most recent call last):\n"
            '  File "b.py", line 2, in g\n'
            "RuntimeError: second\n"
        )
        t = m.parse_traceback(raw)
        assert t is not None
        # The last exception line is the one returned.
        assert t.exception_type == "RuntimeError"
        assert t.exception_message == "second"

    def test_unknown_error_when_no_exception_line(self):
        """Frames without a recognisable exception line yield 'UnknownError'."""
        raw = (
            "Traceback (most recent call last):\n"
            '  File "a.py", line 1, in f\n'
        )
        t = m.parse_traceback(raw)
        assert t is not None
        assert t.exception_type == "UnknownError"
        assert t.exception_message == ""
        assert len(t.frames) == 1


# ---------------------------------------------------------------------------
# translate
# ---------------------------------------------------------------------------


class TestTranslate:
    """translate maps a ParsedTrace to a RemediationRule."""

    def test_known_exception_type_matches_specific_rule(self):
        """ConnectionRefusedError maps to the 'Service Unreachable' rule."""
        t = m.ParsedTrace(exception_type="ConnectionRefusedError", exception_message="")
        rule = m.translate(t)
        assert rule.friendly_title == "Service Unreachable"
        assert rule.severity == "critical"

    def test_valueerror_maps_to_invalid_input(self):
        """ValueError maps to the 'Invalid Input Value' rule."""
        t = m.ParsedTrace(exception_type="ValueError", exception_message="bad")
        rule = m.translate(t)
        assert rule.friendly_title == "Invalid Input Value"
        assert rule.severity == "info"

    def test_unknown_exception_falls_back(self):
        """An exception type no rule matches yields _FALLBACK."""
        t = m.ParsedTrace(
            exception_type="WeirdCustomThing",
            exception_message="nope",
        )
        rule = m.translate(t)
        assert rule is m._FALLBACK

    def test_case_insensitive(self):
        """Rules are matched case-insensitively (per the translator's contract)."""
        t = m.ParsedTrace(exception_type="valueerror", exception_message="bad")
        rule = m.translate(t)
        # The ValueError rule has a bare class-name pattern; re.IGNORECASE is
        # set in translate(), so the lowercase input should still match.
        assert rule.friendly_title == "Invalid Input Value"

    def test_message_can_drive_match(self):
        """A regex pattern that targets the message also matches via translate()."""
        # The TimeoutError rule's pattern includes "socket.timeout" — so a
        # ParsedTrace with just "socket.timeout" in the message should hit it.
        t = m.ParsedTrace(exception_type="SomeOtherError", exception_message="socket.timeout")
        rule = m.translate(t)
        assert rule.friendly_title == "Operation Timed Out"

    def test_filenotfound_matches_resource_not_found(self):
        """FileNotFoundError maps to the 'Resource Not Found' rule."""
        t = m.ParsedTrace(exception_type="FileNotFoundError", exception_message="missing")
        rule = m.translate(t)
        assert rule.friendly_title == "Resource Not Found"


# ---------------------------------------------------------------------------
# format_text
# ---------------------------------------------------------------------------


class TestFormatText:
    """format_text renders a human-readable vendor-friendly report."""

    def test_includes_severity_and_title(self):
        """Severity is uppercased, title appears in brackets."""
        t = m.ParsedTrace(exception_type="ValueError", exception_message="bad")
        rule = m.translate(t)
        out = m.format_text(rule, t)
        assert "[INFO]" in out
        assert "Invalid Input Value" in out

    def test_includes_exception_type(self):
        """The internal exception type is shown."""
        t = m.ParsedTrace(exception_type="ValueError", exception_message="bad")
        rule = m.translate(t)
        out = m.format_text(rule, t)
        assert "Internal exception : ValueError" in out

    def test_includes_message_when_present(self):
        """The 'Message' line is shown when the message is non-empty."""
        t = m.ParsedTrace(exception_type="ValueError", exception_message="something specific")
        rule = m.translate(t)
        out = m.format_text(rule, t)
        assert "Message            : something specific" in out

    def test_skips_message_line_when_empty(self):
        """No 'Message' line is emitted when exception_message is empty."""
        t = m.ParsedTrace(exception_type="ValueError", exception_message="")
        rule = m.translate(t)
        out = m.format_text(rule, t)
        assert "Message" not in out

    def test_verbose_includes_call_stack(self):
        """Verbose mode appends the call stack (last 5 frames)."""
        frames = [f'File "f{i}.py", line {i}, in fn{i}' for i in range(8)]
        t = m.ParsedTrace(
            exception_type="ValueError",
            exception_message="bad",
            frames=frames,
        )
        rule = m.translate(t)
        out = m.format_text(rule, t, verbose=True)
        assert "Call stack" in out
        # Only the last 5 frames appear.
        for fr in frames[-5:]:
            assert fr in out
        for fr in frames[:-5]:
            assert fr not in out

    def test_non_verbose_omits_call_stack(self):
        """Non-verbose mode does not include the call stack section."""
        t = m.ParsedTrace(
            exception_type="ValueError",
            exception_message="bad",
            frames=['File "a.py", line 1, in f'],
        )
        rule = m.translate(t)
        out = m.format_text(rule, t, verbose=False)
        assert "Call stack" not in out


# ---------------------------------------------------------------------------
# format_json
# ---------------------------------------------------------------------------


class TestFormatJson:
    """format_json renders a machine-readable remediation report."""

    def test_returns_valid_json(self):
        """Output is parseable JSON."""
        t = m.ParsedTrace(exception_type="ValueError", exception_message="bad")
        rule = m.translate(t)
        out = m.format_json(rule, t)
        data = json.loads(out)
        assert isinstance(data, dict)

    def test_keys(self):
        """All documented top-level keys are present."""
        t = m.ParsedTrace(exception_type="ValueError", exception_message="bad")
        rule = m.translate(t)
        out = m.format_json(rule, t)
        data = json.loads(out)
        assert data["severity"] == "info"
        assert data["friendly_title"] == "Invalid Input Value"
        assert "remediation" in data
        assert data["internal_exception"] == "ValueError"
        assert data["message"] == "bad"

    def test_message_is_none_when_empty(self):
        """'message' is JSON null when exception_message is empty."""
        t = m.ParsedTrace(exception_type="ValueError", exception_message="")
        rule = m.translate(t)
        out = m.format_json(rule, t)
        data = json.loads(out)
        assert data["message"] is None

    def test_verbose_includes_call_stack(self):
        """Verbose mode adds a 'call_stack' list of at most 5 frames."""
        frames = [f'File "f{i}.py", line {i}, in fn{i}' for i in range(10)]
        t = m.ParsedTrace(
            exception_type="ValueError",
            exception_message="bad",
            frames=frames,
        )
        rule = m.translate(t)
        out = m.format_json(rule, t, verbose=True)
        data = json.loads(out)
        assert "call_stack" in data
        assert data["call_stack"] == frames[-5:]

    def test_non_verbose_omits_call_stack(self):
        """Non-verbose mode does not include 'call_stack'."""
        t = m.ParsedTrace(
            exception_type="ValueError",
            exception_message="bad",
            frames=['File "a.py", line 1, in f'],
        )
        rule = m.translate(t)
        out = m.format_json(rule, t, verbose=False)
        data = json.loads(out)
        assert "call_stack" not in data


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


class TestMain:
    """main() drives the CLI: parse args, read input, translate, print."""

    def test_text_format_from_file(self, tmp_path: Path, capsys):
        """--format text (default) prints a human-readable report; exits 0."""
        f = tmp_path / "trace.txt"
        f.write_text("ValueError: bad value\n", encoding="utf-8")
        rc = m.main(["--input", str(f)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Invalid Input Value" in captured.out
        assert "ValueError" in captured.out

    def test_json_format_from_file(self, tmp_path: Path, capsys):
        """--format json emits parseable JSON; exits 0."""
        f = tmp_path / "trace.txt"
        f.write_text("ValueError: bad value\n", encoding="utf-8")
        rc = m.main(["--input", str(f), "--format", "json"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["friendly_title"] == "Invalid Input Value"
        assert data["internal_exception"] == "ValueError"

    def test_verbose_includes_call_stack(self, tmp_path: Path, capsys):
        """--verbose adds the call stack to the text output."""
        f = tmp_path / "trace.txt"
        f.write_text(
            "Traceback (most recent call last):\n"
            '  File "a.py", line 1, in f\n'
            "ValueError: bad value\n",
            encoding="utf-8",
        )
        rc = m.main(["--input", str(f), "--verbose"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Call stack" in captured.out
        assert 'File "a.py", line 1, in f' in captured.out

    def test_no_traceback_returns_1(self, tmp_path: Path, capsys):
        """When the input has no recognisable traceback, exits 1 and prints to stderr."""
        f = tmp_path / "garbage.txt"
        f.write_text("just some prose, no traceback here\n", encoding="utf-8")
        rc = m.main(["--input", str(f)])
        captured = capsys.readouterr()
        assert rc == 1
        assert "traceback" in captured.err.lower() or "exception" in captured.err.lower()

    def test_stdin_default(self, monkeypatch, capsys):
        """When --input is not given, the module reads from stdin."""
        import io

        monkeypatch.setattr(sys, "stdin", io.StringIO("ValueError: from stdin\n"))
        rc = m.main([])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Invalid Input Value" in captured.out


# ---------------------------------------------------------------------------
# __main__ guard
# ---------------------------------------------------------------------------


class TestMainGuard:
    """The ``if __name__ == "__main__"`` block wires through main()."""

    def test_module_runs_as_script(self, tmp_path: Path):
        """Invoking the module as a script reads a file and exits 0."""
        f = tmp_path / "trace.txt"
        f.write_text("ValueError: bad value\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(_BIN_DIR / "error_message_translator.py"), "--input", str(f)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "Invalid Input Value" in proc.stdout
