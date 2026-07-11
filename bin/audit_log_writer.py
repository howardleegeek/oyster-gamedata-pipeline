#!/usr/bin/env python3
"""
G126 · bin/audit_log_writer.py

Production append-only newline-delimited JSON audit log writer.
Records every capture / lint / upload event with timestamps,
action metadata, and optional payload details.

Usage:
    python3 bin/audit_log_writer.py --log-path audit.log --action capture --status ok
    python3 bin/audit_log_writer.py --log-path audit.log --action lint --status \\
        error --detail "syntax error"
    python3 bin/audit_log_writer.py --log-path audit.log --action upload --status \\
        ok --file report.xlsx
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _build_record(
    action: str,
    status: str,
    detail: Optional[str] = None,
    file_path: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a single audit-log record dict."""
    record: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "action": action,
        "status": status,
    }
    if detail is not None:
        record["detail"] = detail
    if file_path is not None:
        record["file"] = file_path
    if extra is not None:
        record["extra"] = extra
    return record


def append_record(
    log_path: str,
    action: str,
    status: str,
    detail: Optional[str] = None,
    file_path: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a single JSON line to the audit log file atomically.

    Args:
        log_path: Path to the newline-delimited JSON log file.
        action: Event verb (capture / lint / upload).
        status: 'ok' or 'error'.
        detail: Optional human-readable detail string.
        file_path: Optional associated file path.
        extra: Optional free-form metadata dict.
    """
    allowed_actions = {"capture", "lint", "upload"}
    if action not in allowed_actions:
        raise ValueError(f"action must be one of {sorted(allowed_actions)}, got {action!r}")
    allowed_status = {"ok", "error"}
    if status not in allowed_status:
        raise ValueError(f"status must be one of {sorted(allowed_status)}, got {status!r}")

    record = _build_record(action, status, detail, file_path, extra)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"

    log_dir = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(log_dir, exist_ok=True)

    # Atomic append via temp file in same directory
    fd, tmp_path = tempfile.mkstemp(dir=log_dir, prefix=".audit_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(line)
        with open(log_path, "a", encoding="utf-8") as log_fh:
            log_fh.write(line)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry-point. Parses arguments and appends one audit record.

    Returns:
        0 on success, 1 on validation / I/O error.
    """
    parser = argparse.ArgumentParser(
        description="Append a newline-delimited JSON audit log record.",
    )
    parser.add_argument("--log-path", required=True, help="Path to the audit log file.")
    parser.add_argument(
        "--action", required=True, choices=["capture", "lint", "upload"],
        help="Event verb.",
    )
    parser.add_argument(
        "--status", required=True, choices=["ok", "error"],
        help="Event outcome.",
    )
    parser.add_argument("--detail", default=None, help="Optional detail string.")
    parser.add_argument("--file", default=None, dest="file_path", help="Optional file path.")
    parser.add_argument("--extra", default=None, help="Optional JSON-encoded metadata dict.")

    args = parser.parse_args(argv)

    extra_dict: Optional[Dict[str, Any]] = None
    if args.extra is not None:
        try:
            extra_dict = json.loads(args.extra)
        except json.JSONDecodeError as exc:
            print(f"error: --extra must be valid JSON: {exc}", file=sys.stderr)
            return 1

    try:
        append_record(
            log_path=args.log_path,
            action=args.action,
            status=args.status,
            detail=args.detail,
            file_path=args.file_path,
            extra=extra_dict,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
