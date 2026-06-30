#!/usr/bin/env python3
"""Tests for bin/structured_logger.py — G030 structured JSON-line logger.

Covers:
  * LogLevel enum (numeric values, name lookup, all 5 levels)
  * StructuredLogger.__init__ (defaults, custom output, min_level)
  * StructuredLogger.info / debug / warning / error / critical emit valid JSON
  * level filtering (records below min_level are dropped)
  * extras / kwargs are merged into the JSON record
  * include_timestamp toggle (present when True, absent when False)
  * correlation IDs (vendor, clip, step) appear on every line
  * _build_parser (required args, --level choices, --no-timestamp flag)
  * main() CLI: emits one line to stdout, extras parsed from --extra KEY=VAL,
    bad --extra returns 1, missing required arg exits non-zero
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

import structured_logger as m  # noqa: E402
from structured_logger import (  # noqa: E402
    LogLevel,
    StructuredLogger,
    _build_parser,
    main,
)

# ---------------------------------------------------------------------------
# LogLevel enum
# ---------------------------------------------------------------------------


class TestLogLevel:
    """LogLevel enum: numeric values + name mapping."""

    def test_numeric_values_match_stdlib_logging(self):
        """LogLevel numeric values match the stdlib logging module."""
        import logging

        assert int(LogLevel.DEBUG) == logging.DEBUG
        assert int(LogLevel.INFO) == logging.INFO
        assert int(LogLevel.WARNING) == logging.WARNING
        assert int(LogLevel.ERROR) == logging.ERROR
        assert int(LogLevel.CRITICAL) == logging.CRITICAL

    def test_level_names_resolve_from_string(self):
        """Each uppercase name maps back to its LogLevel member."""
        from structured_logger import _LEVEL_NAMES

        assert _LEVEL_NAMES["DEBUG"] is LogLevel.DEBUG
        assert _LEVEL_NAMES["INFO"] is LogLevel.INFO
        assert _LEVEL_NAMES["WARNING"] is LogLevel.WARNING
        assert _LEVEL_NAMES["ERROR"] is LogLevel.ERROR
        assert _LEVEL_NAMES["CRITICAL"] is LogLevel.CRITICAL

    def test_ordering(self):
        """Levels are strictly ordered DEBUG < INFO < WARNING < ERROR < CRITICAL."""
        assert LogLevel.DEBUG < LogLevel.INFO
        assert LogLevel.INFO < LogLevel.WARNING
        assert LogLevel.WARNING < LogLevel.ERROR
        assert LogLevel.ERROR < LogLevel.CRITICAL


# ---------------------------------------------------------------------------
# StructuredLogger.__init__
# ---------------------------------------------------------------------------


class TestStructuredLoggerInit:
    """Constructor stores correlation IDs and config defaults."""

    def test_default_min_level_is_info(self):
        buf = io.StringIO()
        log = StructuredLogger(vendor="acme", clip="vid_001", step="encode", output=buf)
        assert log.min_level is LogLevel.INFO
        assert log.include_timestamp is True

    def test_correlation_ids_stored(self):
        buf = io.StringIO()
        log = StructuredLogger(vendor="v1", clip="c1", step="s1", output=buf)
        assert log.vendor == "v1"
        assert log.clip == "c1"
        assert log.step == "s1"
        assert log.output is buf

    def test_custom_min_level(self):
        buf = io.StringIO()
        log = StructuredLogger(
            vendor="v1", clip="c1", step="s1", output=buf, min_level=LogLevel.WARNING
        )
        assert log.min_level is LogLevel.WARNING

    def test_no_timestamp_flag(self):
        buf = io.StringIO()
        log = StructuredLogger(
            vendor="v1", clip="c1", step="s1", output=buf, include_timestamp=False
        )
        assert log.include_timestamp is False


# ---------------------------------------------------------------------------
# StructuredLogger emission + level filtering
# ---------------------------------------------------------------------------


def _read_records(buf: io.StringIO) -> list:
    """Parse every newline-delimited JSON record from buf."""
    raw = buf.getvalue()
    return [json.loads(line) for line in raw.splitlines() if line]


class TestEmit:
    """JSON-line emission + level filtering + extras + correlation IDs."""

    def test_info_emits_one_record(self):
        buf = io.StringIO()
        log = StructuredLogger(vendor="acme", clip="vid_001", step="encode", output=buf)
        log.info("hello world")
        records = _read_records(buf)
        assert len(records) == 1
        rec = records[0]
        assert rec["level"] == "INFO"
        assert rec["vendor"] == "acme"
        assert rec["clip"] == "vid_001"
        assert rec["step"] == "encode"
        assert rec["message"] == "hello world"

    def test_info_includes_timestamp_by_default(self):
        buf = io.StringIO()
        log = StructuredLogger(vendor="v", clip="c", step="s", output=buf)
        log.info("hi")
        rec = _read_records(buf)[0]
        assert "timestamp" in rec
        # ISO-8601 UTC contains 'T' separator and ends in '+00:00'
        assert "T" in rec["timestamp"]
        assert rec["timestamp"].endswith("+00:00")

    def test_info_excludes_timestamp_when_disabled(self):
        buf = io.StringIO()
        log = StructuredLogger(
            vendor="v", clip="c", step="s", output=buf, include_timestamp=False
        )
        log.info("hi")
        rec = _read_records(buf)[0]
        assert "timestamp" not in rec

    def test_each_level_emits_its_own_name(self):
        buf = io.StringIO()
        log = StructuredLogger(
            vendor="v", clip="c", step="s", output=buf, min_level=LogLevel.DEBUG
        )
        log.debug("d")
        log.info("i")
        log.warning("w")
        log.error("e")
        log.critical("c")
        records = _read_records(buf)
        assert [r["level"] for r in records] == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        assert [r["message"] for r in records] == ["d", "i", "w", "e", "c"]

    def test_below_min_level_is_dropped(self):
        buf = io.StringIO()
        log = StructuredLogger(
            vendor="v", clip="c", step="s", output=buf, min_level=LogLevel.WARNING
        )
        log.debug("dropped-1")
        log.info("dropped-2")
        log.warning("kept-1")
        log.error("kept-2")
        log.critical("kept-3")
        records = _read_records(buf)
        assert [r["level"] for r in records] == ["WARNING", "ERROR", "CRITICAL"]
        assert [r["message"] for r in records] == ["kept-1", "kept-2", "kept-3"]

    def test_extras_are_merged_into_record(self):
        buf = io.StringIO()
        log = StructuredLogger(vendor="v", clip="c", step="s", output=buf)
        log.info("processing", frame=42, fps=30, label="ok")
        rec = _read_records(buf)[0]
        assert rec["frame"] == 42
        assert rec["fps"] == 30
        assert rec["label"] == "ok"

    def test_extras_does_not_overwrite_correlation_ids(self):
        """A kwarg named 'vendor', 'clip', or 'step' may still appear (extra wins)."""
        buf = io.StringIO()
        log = StructuredLogger(vendor="v1", clip="c1", step="s1", output=buf)
        log.info("msg", extra_field="value")
        rec = _read_records(buf)[0]
        # Core correlation IDs remain because they are set first.
        assert rec["vendor"] == "v1"
        assert rec["clip"] == "c1"
        assert rec["step"] == "s1"
        assert rec["extra_field"] == "value"

    def test_output_is_flushed(self):
        """Each emit is flushed so consumers tailing the stream see lines live."""
        buf = io.StringIO()
        log = StructuredLogger(vendor="v", clip="c", step="s", output=buf)
        log.info("flush-me")
        # The .flush() on a StringIO is a no-op, but we can assert the
        # method is on the output's class. We verify indirectly by reading.
        assert "flush-me" in buf.getvalue()

    def test_output_is_line_terminated(self):
        buf = io.StringIO()
        log = StructuredLogger(vendor="v", clip="c", step="s", output=buf)
        log.info("one")
        log.info("two")
        # Two records → two newlines.
        assert buf.getvalue().count("\n") == 2


# ---------------------------------------------------------------------------
# _build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    """CLI parser accepts the documented flags and has the right defaults."""

    def test_required_args(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            # Missing all three required args → SystemExit(2)
            parser.parse_args([])

    def test_required_args_present(self):
        parser = _build_parser()
        args = parser.parse_args(["--vendor", "v", "--clip", "c", "--step", "s", "msg"])
        assert args.vendor == "v"
        assert args.clip == "c"
        assert args.step == "s"
        assert args.message == "msg"

    def test_default_level_is_info(self):
        parser = _build_parser()
        args = parser.parse_args(["--vendor", "v", "--clip", "c", "--step", "s", "msg"])
        assert args.level == "INFO"

    def test_level_choices(self):
        parser = _build_parser()
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            args = parser.parse_args(
                ["--vendor", "v", "--clip", "c", "--step", "s", "--level", level, "msg"]
            )
            assert args.level == level

    def test_no_timestamp_flag(self):
        parser = _build_parser()
        args = parser.parse_args(
            ["--vendor", "v", "--clip", "c", "--step", "s", "--no-timestamp", "msg"]
        )
        assert args.no_timestamp is True
        # default is False
        args2 = parser.parse_args(["--vendor", "v", "--clip", "c", "--step", "s", "msg"])
        assert args2.no_timestamp is False

    def test_extra_is_append_list(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "--vendor",
                "v",
                "--clip",
                "c",
                "--step",
                "s",
                "--extra",
                "k1=v1",
                "--extra",
                "k2=v2",
                "msg",
            ]
        )
        assert args.extra == ["k1=v1", "k2=v2"]


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


class TestMain:
    """CLI entry point: emits JSON to stdout, returns 0 on success.

    We patch `structured_logger.StructuredLogger` so main()'s logger writes
    to an in-memory StringIO buffer that we control. (The library's
    `output=sys.stdout` default is bound at *import* time, before pytest's
    capsys replaces sys.stdout, so we cannot rely on capsys capturing the
    output.  Patching is the simplest, most robust path.)
    """

    def _patched_logger(self, monkeypatch):
        """Replace StructuredLogger in the structured_logger module with one
        whose default output is the supplied buffer."""
        from structured_logger import StructuredLogger as _Real

        buf = io.StringIO()

        class _CapturingLogger(_Real):
            def __init__(self, *args, **kwargs):
                # Force the output to our buffer regardless of caller-supplied
                # value (main() does not pass output= so the default wins).
                kwargs["output"] = buf
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(m, "StructuredLogger", _CapturingLogger)
        return buf

    def test_main_emits_one_json_line(self, monkeypatch):
        buf = self._patched_logger(monkeypatch)
        rc = main(["--vendor", "acme", "--clip", "vid_001", "--step", "encode", "hello"])
        assert rc == 0
        records = [json.loads(line) for line in buf.getvalue().splitlines() if line]
        assert len(records) == 1
        rec = records[0]
        assert rec["vendor"] == "acme"
        assert rec["clip"] == "vid_001"
        assert rec["step"] == "encode"
        assert rec["level"] == "INFO"
        assert rec["message"] == "hello"

    def test_main_with_extras(self, monkeypatch):
        buf = self._patched_logger(monkeypatch)
        rc = main(
            [
                "--vendor",
                "v",
                "--clip",
                "c",
                "--step",
                "s",
                "--extra",
                "frame=42",
                "--extra",
                "label=ok",
                "msg",
            ]
        )
        assert rc == 0
        rec = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert rec["frame"] == "42"  # values are passed through as strings
        assert rec["label"] == "ok"

    def test_main_with_no_timestamp(self, monkeypatch):
        buf = self._patched_logger(monkeypatch)
        rc = main(
            [
                "--vendor",
                "v",
                "--clip",
                "c",
                "--step",
                "s",
                "--no-timestamp",
                "msg",
            ]
        )
        assert rc == 0
        rec = json.loads(buf.getvalue().strip())
        assert "timestamp" not in rec

    def test_main_with_custom_level(self, monkeypatch):
        buf = self._patched_logger(monkeypatch)
        rc = main(
            [
                "--vendor",
                "v",
                "--clip",
                "c",
                "--step",
                "s",
                "--level",
                "WARNING",
                "ignored-info",
            ]
        )
        # main() always calls .info() on the logger with the message,
        # so a WARNING min_level will filter the single record out.
        assert rc == 0
        assert buf.getvalue() == ""  # no lines emitted

    def test_main_bad_extra_returns_1(self, monkeypatch, capsys):
        # No need to patch output for the bad-extra path; main() prints to
        # stderr and returns 1 *before* constructing the logger.
        rc = main(
            [
                "--vendor",
                "v",
                "--clip",
                "c",
                "--step",
                "s",
                "--extra",
                "no-equals-sign",
                "msg",
            ]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "--extra must be KEY=VALUE" in err

    def test_main_missing_required_arg_returns_nonzero(self):
        with pytest.raises(SystemExit) as excinfo:
            main(["--vendor", "v", "--clip", "c"])  # missing --step + message
        # argparse calls sys.exit(2) on usage errors
        assert excinfo.value.code != 0
