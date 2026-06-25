#!/usr/bin/env python3
"""
G030 · Structured JSON-line logger with correlation IDs.

Provides a JSON-lines logger that embeds vendor, clip, and step correlation
IDs into every log entry.  Designed for pipeline tracing and downstream
log aggregation (e.g. ELK, CloudWatch).

Usage (CLI):
    python bin/structured_logger.py --vendor acme --clip vid_001 --step encode \
        --level INFO "Starting encode step"

Usage (library):
    from bin.structured_logger import StructuredLogger
    log = StructuredLogger(vendor="acme", clip="vid_001", step="encode")
    log.info("Processing frame", frame=42, fps=30)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, Optional, TextIO


class LogLevel(IntEnum):
    """Numeric log levels compatible with stdlib logging."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


_LEVEL_NAMES: Dict[str, LogLevel] = {lv.name: lv for lv in LogLevel}


class StructuredLogger:
    """JSON-line structured logger with vendor/clip/step correlation IDs.

    Every emitted line is a single JSON object containing at minimum:
      - level:   uppercase log level name
      - vendor:  vendor identifier
      - clip:    clip identifier
      - step:    pipeline step name
      - message: human-readable log message
      - timestamp: ISO-8601 UTC timestamp (when enabled)

    Additional keyword arguments passed to any log method are merged into
    the JSON object as extra fields.
    """

    def __init__(
        self,
        vendor: str,
        clip: str,
        step: str,
        output: TextIO = sys.stdout,
        min_level: LogLevel = LogLevel.INFO,
        include_timestamp: bool = True,
    ) -> None:
        self.vendor = vendor
        self.clip = clip
        self.step = step
        self.output = output
        self.min_level = min_level
        self.include_timestamp = include_timestamp

    # -- public log methods ------------------------------------------------

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log a DEBUG-level message."""
        self._emit(LogLevel.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log an INFO-level message."""
        self._emit(LogLevel.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log a WARNING-level message."""
        self._emit(LogLevel.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log an ERROR-level message."""
        self._emit(LogLevel.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """Log a CRITICAL-level message."""
        self._emit(LogLevel.CRITICAL, message, **kwargs)

    # -- internal ----------------------------------------------------------

    def _emit(self, level: LogLevel, message: str, **kwargs: Any) -> None:
        """Build and write a single JSON log line if level >= min_level."""
        if level < self.min_level:
            return
        record: Dict[str, Any] = {
            "level": level.name,
            "vendor": self.vendor,
            "clip": self.clip,
            "step": self.step,
            "message": message,
        }
        if self.include_timestamp:
            record["timestamp"] = datetime.now(timezone.utc).isoformat()
        record.update(kwargs)
        self.output.write(json.dumps(record, default=str) + "\n")
        self.output.flush()


def _build_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Emit a structured JSON log line to stdout.",
    )
    parser.add_argument("--vendor", required=True, help="Vendor identifier")
    parser.add_argument("--clip", required=True, help="Clip identifier")
    parser.add_argument("--step", required=True, help="Pipeline step name")
    parser.add_argument(
        "--level",
        default="INFO",
        choices=list(_LEVEL_NAMES),
        help="Minimum log level (default: INFO)",
    )
    parser.add_argument(
        "--no-timestamp",
        action="store_true",
        help="Omit the timestamp field",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra key=value pairs to include in the log record",
    )
    parser.add_argument("message", help="Log message text")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry-point: parse args, emit one JSON log line, return exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    extra: Dict[str, Any] = {}
    for pair in args.extra:
        if "=" not in pair:
            print(f"error: --extra must be KEY=VALUE, got: {pair}", file=sys.stderr)
            return 1
        key, value = pair.split("=", 1)
        extra[key] = value

    min_level = _LEVEL_NAMES[args.level]
    logger = StructuredLogger(
        vendor=args.vendor,
        clip=args.clip,
        step=args.step,
        min_level=min_level,
        include_timestamp=not args.no_timestamp,
    )
    logger.info(args.message, **extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
