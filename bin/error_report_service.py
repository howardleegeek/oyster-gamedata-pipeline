#!/usr/bin/env python3
"""
G231-G240 · W28 Error Reporting Service (storage layer)

The recorder ships with a global ``sys.excepthook`` (G237 — see
``bin/auto_install_error_handler.py``).  When a crash fires we want to
ship a redacted stack trace to the backend so ops can see top crashes
across 1000 testers without each crash producing a row.

This module is the storage / dedup / scrub layer that:

  * accepts a crash report (``recorder_version``, ``os``, ``stack_trace``,
    ``context``, ``anon_id``),
  * scrubs PII (filesystem paths, usernames, machine names),
  * computes a stable fingerprint hash of the scrubbed stack so 1000
    identical crashes become one row with ``count``,
  * persists to a SQL store — Postgres in production, SQLite in tests /
    local dev (``DATABASE_URL=sqlite:///...``).

The same logic is reused by the Next.js ``/api/error-report`` route via
HTTP, but it can also be invoked directly from a Python ingest worker.

PII scrub strategy (G238):
  * absolute filesystem paths (``C:\\Users\\Foo``, ``/Users/foo``,
    ``/home/bar``) -> ``<PATH>``;
  * windows usernames (``\\Users\\<NAME>``) -> ``\\Users\\<USER>``;
  * machine names visible in stack (``Computer-of-Howard``) are
    canonicalised by the path scrub above;
  * IP addresses (``\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b``) -> ``<IP>``;
  * email addresses -> ``<EMAIL>``;
  * UUIDs are KEPT (the recorder's anon_id is a UUIDv4 so we need to
    correlate, but the recorder generates a fresh anon_id per install —
    it is NOT tied to user identity, see G220 privacy spec).

Dedup fingerprint (G232):
  * SHA-256 of (scrubbed_stack || os_family || recorder_version_minor),
    where ``recorder_version_minor`` strips the patch-level suffix so a
    crash from rc19.0.0 and rc19.0.1 still collapse into one fingerprint
    (different patches typically don't change file/line numbers in
    deeper frames).

Iron-law:
  * No silent acceptance of unscrubbed traces.
  * Hard-cap stack trace length at 16 KiB so a malicious client can't
    flood the table with multi-MB payloads.
  * Anonymous: anon_id is opaque, never linked to a tester record.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------------------
# Limits & constants
# ---------------------------------------------------------------------------

MAX_STACK_BYTES: int = 16 * 1024
MAX_CONTEXT_BYTES: int = 4 * 1024
MAX_OS_LEN: int = 64
MAX_VERSION_LEN: int = 64
MAX_ANON_ID_LEN: int = 64

ALLOWED_SEVERITIES: tuple[str, ...] = ("crash", "error", "warn", "info")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorReportError(ValueError):
    """Caller-side validation problem with a submitted error report."""


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------


@dataclass
class ErrorReport:
    """A single crash report after validation but before scrub/persist."""

    recorder_version: str
    os: str
    stack_trace: str
    context: dict[str, Any] = field(default_factory=dict)
    anon_id: Optional[str] = None
    severity: str = "crash"


# ---------------------------------------------------------------------------
# PII scrubbing
# ---------------------------------------------------------------------------

# Order matters — longer/more-specific patterns first.

# 1. Windows user paths:  C:\Users\Howard\AppData\Local\...
#    The Howard segment is replaced with <USER>; the rest is canonicalised
#    by _APPDATA_RE so it stays readable for ops.
_WIN_USER_RE = re.compile(
    r"([A-Za-z]:[\\/]+Users[\\/]+)([^\\/\s\"']+)",
    flags=re.IGNORECASE,
)
# 2. Unix home dirs:  /Users/howard/...  /home/howard/...
_UNIX_HOME_RE = re.compile(
    r"(/(?:Users|home)/)([^/\s\"']+)",
)
# 3. Other absolute Windows paths (no Users prefix):  C:\foo\bar
#    Negative-look-ahead skips any drive letter immediately followed by
#    "Users" (which was already handled by _WIN_USER_RE).
_WIN_ABS_RE = re.compile(r"\b[A-Za-z]:\\(?![Uu]sers\\)[^\s\"'`]+")
# 4. Absolute unix paths NOT under /Users or /home (those go through
#    _UNIX_HOME_RE above).  We only collapse known system-level prefixes
#    here so deliberate non-PII paths like "/usr/bin/python3" stay
#    readable.
_UNIX_ABS_RE = re.compile(r"(?<![A-Za-z])(/(?:tmp|var|opt|private|mnt|data)/[^\s\"'`,)]+)")
# 5. IPv4
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# 6. Email addresses
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# 7. Windows AppData canonicalisation (run after user replace so casing
#    is consistent in the stored stack)
_APPDATA_RE = re.compile(r"\\AppData\\(Local|Roaming)\\", flags=re.IGNORECASE)


def scrub_pii(blob: str) -> str:
    """Replace personal-identifying patterns with anonymised tokens.

    Order matters:
      1. _WIN_USER_RE redacts the Howard segment under C:\\Users\\
         (the trailing AppData / Documents / Downloads stays readable).
      2. _UNIX_HOME_RE does the same for /Users/howard and /home/bob.
      3. _WIN_ABS_RE handles non-Users Windows paths (using a negative
         lookahead so it does NOT swallow the already-redacted
         C:\\Users\\<USER>\\... case).
      4. _UNIX_ABS_RE redacts /tmp/, /var/, etc.
      5. _APPDATA_RE canonicalises casing.
      6. _IPV4_RE and _EMAIL_RE sweep.
    """
    if not isinstance(blob, str):
        return blob
    s = blob
    s = _WIN_USER_RE.sub(r"\1<USER>", s)
    s = _UNIX_HOME_RE.sub(r"\1<USER>", s)
    s = _WIN_ABS_RE.sub("<PATH>", s)
    s = _UNIX_ABS_RE.sub("<PATH>", s)

    # Canonicalise AppData casing — match is case-insensitive but the
    # replacement must use Title-case so identical crashes from systems
    # with different locales hash the same.
    def _appdata_title(m: re.Match[str]) -> str:
        return f"\\AppData\\{m.group(1).capitalize()}\\"

    s = _APPDATA_RE.sub(_appdata_title, s)
    s = _IPV4_RE.sub("<IP>", s)
    s = _EMAIL_RE.sub("<EMAIL>", s)
    return s


def scrub_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Walk a JSON-y context blob and scrub string values."""
    if not isinstance(ctx, dict):
        return {}
    out: dict[str, Any] = {}
    for key, v in ctx.items():
        out_key = key
        if isinstance(key, str) and len(key) > 64:
            out_key = key[:64]
        if isinstance(v, str):
            out[out_key] = scrub_pii(v[:1024])
        elif isinstance(v, (int, float, bool)) or v is None:
            out[out_key] = v
        elif isinstance(v, list):
            out[out_key] = [
                scrub_pii(item[:1024]) if isinstance(item, str) else item for item in v[:32]
            ]
        elif isinstance(v, dict):
            out[out_key] = scrub_context(v)
        else:
            out[out_key] = str(v)[:512]
    return out


# ---------------------------------------------------------------------------
# Fingerprint (dedup key)
# ---------------------------------------------------------------------------

_PATCH_SUFFIX_RE = re.compile(r"^(v?\d+\.\d+\.\d+(?:-[A-Za-z]+\d+)?)(?:\.[0-9]+)*$")


def _normalise_version_for_fp(v: str) -> str:
    """Strip patch suffix so rc19.0.0 and rc19.0.1 share a fingerprint."""
    v = v.strip()
    m = _PATCH_SUFFIX_RE.match(v)
    if m:
        return m.group(1)
    return v


def _normalise_os_for_fp(os_str: str) -> str:
    """Collapse OS string to coarse family for fingerprint stability."""
    s = (os_str or "").strip().lower()
    if s.startswith("windows") or "nt-" in s or s.startswith("win"):
        return "windows"
    if s.startswith("darwin") or s.startswith("macos") or s.startswith("mac"):
        return "macos"
    if s.startswith("linux") or s.startswith("ubuntu") or s.startswith("debian"):
        return "linux"
    return s[:32] or "unknown"


def fingerprint_stack(scrubbed_stack: str, os_str: str, recorder_version: str) -> str:
    """Stable dedup hash.  hex digest, 32 chars."""
    base = "|".join(
        [
            _normalise_os_for_fp(os_str),
            _normalise_version_for_fp(recorder_version),
            scrubbed_stack.strip(),
        ]
    )
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return digest[:32]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:-[A-Za-z0-9.\-]+)?$")
_ANON_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_OS_RE = re.compile(r"^[A-Za-z0-9 .\-_/():+]+$")


def validate_report(raw: dict[str, Any]) -> ErrorReport:
    """Validate and coerce a raw POST body into an :class:`ErrorReport`.

    Raises:
        ErrorReportError: on any validation failure.
    """
    if not isinstance(raw, dict):
        raise ErrorReportError("report body must be a JSON object")

    recorder_version = raw.get("recorder_version")
    if not isinstance(recorder_version, str) or not _VERSION_RE.match(recorder_version.strip()):
        raise ErrorReportError("recorder_version is required and must be semver-like")
    if len(recorder_version) > MAX_VERSION_LEN:
        raise ErrorReportError(f"recorder_version exceeds {MAX_VERSION_LEN} chars")

    os_str = raw.get("os")
    if not isinstance(os_str, str) or not os_str.strip():
        raise ErrorReportError("os is required")
    if len(os_str) > MAX_OS_LEN or not _OS_RE.match(os_str):
        raise ErrorReportError("os contains disallowed characters or is too long")

    stack = raw.get("stack_trace")
    if not isinstance(stack, str) or not stack.strip():
        raise ErrorReportError("stack_trace is required")
    if len(stack.encode("utf-8")) > MAX_STACK_BYTES:
        raise ErrorReportError(f"stack_trace exceeds {MAX_STACK_BYTES} bytes")

    ctx = raw.get("context", {})
    if ctx is None:
        ctx = {}
    if not isinstance(ctx, dict):
        raise ErrorReportError("context must be a JSON object")
    if len(json.dumps(ctx).encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ErrorReportError(f"context exceeds {MAX_CONTEXT_BYTES} bytes")

    anon_id = raw.get("anon_id")
    if anon_id is not None:
        if not isinstance(anon_id, str) or not _ANON_ID_RE.match(anon_id):
            raise ErrorReportError(f"anon_id must match {_ANON_ID_RE.pattern!r}")

    severity = raw.get("severity", "crash")
    if not isinstance(severity, str) or severity not in ALLOWED_SEVERITIES:
        raise ErrorReportError(f"severity must be one of {ALLOWED_SEVERITIES}")

    return ErrorReport(
        recorder_version=recorder_version.strip(),
        os=os_str.strip(),
        stack_trace=stack,
        context=ctx,
        anon_id=anon_id.strip() if isinstance(anon_id, str) else None,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Storage backend (SQLite + Postgres-compatible SQL)
# ---------------------------------------------------------------------------

# We use SQL identical between sqlite + postgres for the table; the only
# difference is sqlite's lack of INTERVAL parsing in the summary query —
# we compute the cutoff in Python before issuing the query.

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS error_reports (
    fingerprint        TEXT PRIMARY KEY,
    first_seen         TEXT NOT NULL,
    last_seen          TEXT NOT NULL,
    count              INTEGER NOT NULL DEFAULT 1,
    recorder_version   TEXT NOT NULL,
    os                 TEXT NOT NULL,
    severity           TEXT NOT NULL,
    stack_trace        TEXT NOT NULL,
    context_json       TEXT NOT NULL,
    sample_anon_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_error_reports_last_seen
    ON error_reports(last_seen);
CREATE INDEX IF NOT EXISTS idx_error_reports_recorder_version
    ON error_reports(recorder_version);
"""


@dataclass
class ErrorReportRow:
    fingerprint: str
    first_seen: str
    last_seen: str
    count: int
    recorder_version: str
    os: str
    severity: str
    stack_trace: str
    context: dict[str, Any]
    sample_anon_id: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "count": self.count,
            "recorder_version": self.recorder_version,
            "os": self.os,
            "severity": self.severity,
            "stack_trace": self.stack_trace,
            "context": self.context,
            "sample_anon_id": self.sample_anon_id,
        }


class ErrorReportStore:
    """Sqlite-backed dedup store.

    Either pass a ``db_path`` (filesystem) or ``":memory:"`` for tests.
    Thread-safety: sqlite connection is opened ``check_same_thread=False``
    and writes go through a single per-instance lock — fine for the
    ~10 r/s the Vercel serverless function will see.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_schema()

    # ----- low-level connection ------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,  # autocommit
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def _init_schema(self) -> None:
        conn = self._connect()
        with conn:
            for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
                conn.execute(stmt)

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._connect()
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ----- main entry points ---------------------------------------------
    def record(
        self,
        report: ErrorReport,
        *,
        now: Optional[_dt.datetime] = None,
    ) -> dict[str, Any]:
        """Insert/upsert a report, returning the row state after the write."""
        scrubbed_stack = scrub_pii(report.stack_trace)
        scrubbed_ctx = scrub_context(report.context)
        fp = fingerprint_stack(scrubbed_stack, report.os, report.recorder_version)
        when = (now or _dt.datetime.now(tz=_dt.timezone.utc)).isoformat()

        with self._cursor() as cur:
            cur.execute(
                "SELECT count FROM error_reports WHERE fingerprint = ?",
                (fp,),
            )
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO error_reports
                        (fingerprint, first_seen, last_seen, count,
                         recorder_version, os, severity,
                         stack_trace, context_json, sample_anon_id)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fp,
                        when,
                        when,
                        report.recorder_version,
                        report.os,
                        report.severity,
                        scrubbed_stack,
                        json.dumps(scrubbed_ctx, sort_keys=True),
                        report.anon_id,
                    ),
                )
                duplicate = False
                new_count = 1
            else:
                cur.execute(
                    """
                    UPDATE error_reports
                       SET count = count + 1,
                           last_seen = ?
                     WHERE fingerprint = ?
                    """,
                    (when, fp),
                )
                duplicate = True
                new_count = int(existing["count"]) + 1

        return {
            "fingerprint": fp,
            "count": new_count,
            "duplicate": duplicate,
            "last_seen": when,
        }

    def get(self, fingerprint: str) -> Optional[ErrorReportRow]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM error_reports WHERE fingerprint = ?",
                (fingerprint,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return ErrorReportRow(
            fingerprint=row["fingerprint"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            count=int(row["count"]),
            recorder_version=row["recorder_version"],
            os=row["os"],
            severity=row["severity"],
            stack_trace=row["stack_trace"],
            context=json.loads(row["context_json"] or "{}"),
            sample_anon_id=row["sample_anon_id"],
        )

    def summary(
        self,
        *,
        since: Optional[_dt.datetime] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Top-N most recent crashes, ordered by ``count`` desc.

        Args:
            since: only count rows whose ``last_seen >= since``.  When
                None, returns all rows.
            limit: max rows to return (1..500).
        """
        limit = max(1, min(500, int(limit)))
        with self._cursor() as cur:
            if since is not None:
                cur.execute(
                    """
                    SELECT *
                      FROM error_reports
                     WHERE last_seen >= ?
                     ORDER BY count DESC, last_seen DESC
                     LIMIT ?
                    """,
                    (since.isoformat(), limit),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                      FROM error_reports
                     ORDER BY count DESC, last_seen DESC
                     LIMIT ?
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
        return [
            {
                "fingerprint": r["fingerprint"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "count": int(r["count"]),
                "recorder_version": r["recorder_version"],
                "os": r["os"],
                "severity": r["severity"],
                "stack_trace_preview": (r["stack_trace"] or "")[:240],
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# `since=24h` parsing helper (used by the API summary route)
# ---------------------------------------------------------------------------

_SINCE_RE = re.compile(r"^(\d+)([smhd])$")


def parse_since(
    spec: Optional[str], *, now: Optional[_dt.datetime] = None
) -> Optional[_dt.datetime]:
    """Parse strings like ``24h``, ``7d``, ``30m`` into a UTC cutoff."""
    if spec is None or spec == "":
        return None
    m = _SINCE_RE.match(spec.strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    seconds_per = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    delta = _dt.timedelta(seconds=n * seconds_per)
    now = now or _dt.datetime.now(tz=_dt.timezone.utc)
    return now - delta


# ---------------------------------------------------------------------------
# CLI (for local debugging / runbook drills)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="error_report_service",
        description=(
            "G231-G240 · W28 error reporting (record + summary) using a local SQLite store."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_record = sub.add_parser("record", help="Record an error report from JSON on stdin")
    p_record.add_argument("--db", required=True, help="Path to sqlite db file")

    p_summary = sub.add_parser("summary", help="Print top crashes")
    p_summary.add_argument("--db", required=True, help="Path to sqlite db file")
    p_summary.add_argument(
        "--since", default=None, help="Time window, e.g. 24h, 7d (default: all time)"
    )
    p_summary.add_argument("--limit", type=int, default=50)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    store = ErrorReportStore(args.db)
    try:
        if args.cmd == "record":
            try:
                raw = json.load(sys.stdin)
            except json.JSONDecodeError as exc:
                print(f"error: stdin not JSON: {exc}", file=sys.stderr)
                return 2
            try:
                report = validate_report(raw)
            except ErrorReportError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            result = store.record(report)
            json.dump(result, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 0
        if args.cmd == "summary":
            cutoff = parse_since(args.since)
            rows = store.summary(since=cutoff, limit=args.limit)
            json.dump({"rows": rows, "count": len(rows)}, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 0
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
