#!/usr/bin/env python3
"""
Tests for G231-G240 · bin/error_report_service.py
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bin import error_report_service as ers  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    s = ers.ErrorReportStore(":memory:")
    yield s
    s.close()


def _minimal(**over: Any) -> dict[str, Any]:
    base = {
        "recorder_version": "v0.28.0-rc19.0.1",
        "os": "windows-11-build-22631",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            "  File \"recorder.py\", line 42, in main\n"
            "    raise RuntimeError('boom')\n"
            "RuntimeError: boom\n"
        ),
        "context": {"game": "minecraft"},
        "anon_id": "abc-123",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidate:
    def test_minimal_passes(self):
        r = ers.validate_report(_minimal())
        assert r.recorder_version == "v0.28.0-rc19.0.1"
        assert r.os == "windows-11-build-22631"
        assert r.severity == "crash"

    def test_missing_recorder_version_fails(self):
        body = _minimal()
        del body["recorder_version"]
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)

    def test_bad_recorder_version_format(self):
        body = _minimal(recorder_version="not-a-version")
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)

    def test_missing_stack_fails(self):
        body = _minimal()
        del body["stack_trace"]
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)

    def test_oversize_stack_fails(self):
        body = _minimal(stack_trace="A" * (ers.MAX_STACK_BYTES + 1))
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)

    def test_oversize_context_fails(self):
        big_ctx = {"k": "v" * (ers.MAX_CONTEXT_BYTES + 10)}
        body = _minimal(context=big_ctx)
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)

    def test_non_dict_context_fails(self):
        body = _minimal(context=["not", "a", "dict"])
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)

    def test_bad_anon_id_chars_fail(self):
        body = _minimal(anon_id="hello world!!")
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)

    def test_anon_id_optional(self):
        body = _minimal()
        del body["anon_id"]
        r = ers.validate_report(body)
        assert r.anon_id is None

    def test_severity_must_be_allowed(self):
        body = _minimal(severity="catastrophic")
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)

    def test_os_with_disallowed_chars_fails(self):
        body = _minimal(os="windows; rm -rf /")
        with pytest.raises(ers.ErrorReportError):
            ers.validate_report(body)


# ---------------------------------------------------------------------------
# PII scrub
# ---------------------------------------------------------------------------


class TestScrubPii:
    def test_windows_user_path_redacted(self):
        s = "C:\\Users\\Howard\\AppData\\Local\\OysterRecorder\\foo.dll"
        out = ers.scrub_pii(s)
        assert "Howard" not in out
        assert "<USER>" in out

    def test_unix_users_path_redacted(self):
        s = "/Users/howard/Downloads/clip.tar.gz"
        out = ers.scrub_pii(s)
        assert "howard" not in out

    def test_home_path_redacted(self):
        s = "/home/bob/work/oyster-pipeline/bin/foo.py"
        out = ers.scrub_pii(s)
        assert "bob" not in out

    def test_tmp_path_redacted(self):
        s = "Error reading /tmp/recorder-abc123/buffer.dat"
        out = ers.scrub_pii(s)
        assert "abc123" not in out
        assert "<PATH>" in out

    def test_ipv4_redacted(self):
        s = "Connection refused from 192.168.1.42"
        out = ers.scrub_pii(s)
        assert "192.168.1.42" not in out
        assert "<IP>" in out

    def test_email_redacted(self):
        s = "Reported by howard.li@berkeley.edu"
        out = ers.scrub_pii(s)
        assert "howard.li" not in out
        assert "<EMAIL>" in out

    def test_multiple_pii_in_one_blob(self):
        s = (
            "Traceback:\n"
            "  File \"C:\\Users\\Howard\\AppData\\Local\\OysterRecorder\\bin\\foo.py\", line 42\n"
            "  contact: howard.li@berkeley.edu  ip: 10.0.0.5\n"
        )
        out = ers.scrub_pii(s)
        assert "Howard" not in out
        assert "howard.li" not in out
        assert "10.0.0.5" not in out
        assert "<USER>" in out
        assert "<EMAIL>" in out
        assert "<IP>" in out

    def test_context_walk(self):
        ctx = {
            "user_home": "/Users/foo/projects",
            "ip": "10.0.0.1",
            "nested": {"path": "C:\\Users\\Bob\\file.exe"},
            "items": ["/tmp/leaked", "ok"],
            "count": 7,
            "flag": True,
        }
        out = ers.scrub_context(ctx)
        assert "foo" not in json.dumps(out)
        assert "Bob" not in json.dumps(out)
        assert "leaked" not in json.dumps(out)
        # non-string fields preserved
        assert out["count"] == 7
        assert out["flag"] is True

    def test_no_uuids_redacted(self):
        # UUIDs are explicitly NOT scrubbed — anon_id propagation relies on them.
        s = "anon_id=a3b9c8d7-1234-5678-9abc-def012345678"
        out = ers.scrub_pii(s)
        assert "a3b9c8d7-1234-5678-9abc-def012345678" in out


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_identical_inputs_same_fp(self):
        a = ers.fingerprint_stack("trace A", "windows-11", "v0.28.0-rc19.0.1")
        b = ers.fingerprint_stack("trace A", "windows-11", "v0.28.0-rc19.0.1")
        assert a == b

    def test_different_stack_different_fp(self):
        a = ers.fingerprint_stack("trace A", "windows-11", "v0.28.0-rc19.0.1")
        b = ers.fingerprint_stack("trace B", "windows-11", "v0.28.0-rc19.0.1")
        assert a != b

    def test_os_family_collapse(self):
        a = ers.fingerprint_stack("trace", "windows-11-build-22631", "v0.28.0")
        b = ers.fingerprint_stack("trace", "windows-10", "v0.28.0")
        # Both collapse to 'windows' family
        assert a == b

    def test_macos_vs_windows_different(self):
        a = ers.fingerprint_stack("trace", "windows-11", "v0.28.0")
        b = ers.fingerprint_stack("trace", "darwin-23.0.0", "v0.28.0")
        assert a != b

    def test_patch_suffix_collapses(self):
        # rc19.0.0 and rc19.0.1 share fingerprint
        a = ers.fingerprint_stack("trace", "windows-11", "v0.28.0-rc19.0.0")
        b = ers.fingerprint_stack("trace", "windows-11", "v0.28.0-rc19.0.1")
        assert a == b

    def test_rc_bump_changes_fp(self):
        # rc19 and rc20 do NOT share
        a = ers.fingerprint_stack("trace", "windows-11", "v0.28.0-rc19.0.0")
        b = ers.fingerprint_stack("trace", "windows-11", "v0.28.0-rc20")
        assert a != b

    def test_minor_bump_changes_fp(self):
        a = ers.fingerprint_stack("trace", "windows-11", "v0.28.0-rc19.0.0")
        b = ers.fingerprint_stack("trace", "windows-11", "v0.29.0-rc1")
        assert a != b

    def test_fp_is_32_hex_chars(self):
        fp = ers.fingerprint_stack("trace", "windows-11", "v0.28.0")
        assert len(fp) == 32
        int(fp, 16)  # raises if not hex


# ---------------------------------------------------------------------------
# Store: record + dedup
# ---------------------------------------------------------------------------


class TestStoreRecord:
    def test_first_insert(self, store):
        report = ers.validate_report(_minimal())
        result = store.record(report)
        assert result["count"] == 1
        assert result["duplicate"] is False
        assert len(result["fingerprint"]) == 32

    def test_duplicate_increments_count(self, store):
        report = ers.validate_report(_minimal())
        store.record(report)
        store.record(report)
        result = store.record(report)
        assert result["count"] == 3
        assert result["duplicate"] is True

    def test_distinct_stacks_distinct_rows(self, store):
        a = ers.validate_report(_minimal(stack_trace="trace A\n"))
        b = ers.validate_report(_minimal(stack_trace="trace B\n"))
        store.record(a)
        store.record(b)
        rows = store.summary()
        assert len(rows) == 2

    def test_pii_actually_scrubbed_in_storage(self, store):
        report = ers.validate_report(
            _minimal(
                stack_trace=(
                    "File C:\\Users\\Howard\\foo.py, line 1\n"
                    "  contact howard.li@berkeley.edu\n"
                )
            )
        )
        result = store.record(report)
        row = store.get(result["fingerprint"])
        assert row is not None
        assert "Howard" not in row.stack_trace
        assert "howard.li" not in row.stack_trace
        assert "<USER>" in row.stack_trace
        assert "<EMAIL>" in row.stack_trace

    def test_dedup_collapses_pii_variants(self, store):
        # Two crashes with DIFFERENT usernames but identical structure
        # should dedup to one row after PII scrub.
        a = ers.validate_report(
            _minimal(stack_trace="C:\\Users\\Alice\\foo.py crashed\n")
        )
        b = ers.validate_report(
            _minimal(stack_trace="C:\\Users\\Bob\\foo.py crashed\n")
        )
        store.record(a)
        result = store.record(b)
        assert result["duplicate"] is True
        assert result["count"] == 2


# ---------------------------------------------------------------------------
# Store: summary
# ---------------------------------------------------------------------------


class TestStoreSummary:
    def test_empty_store(self, store):
        assert store.summary() == []

    def test_top_n_ordering(self, store):
        # Record three distinct crashes with different counts.
        a = ers.validate_report(_minimal(stack_trace="A\n"))
        b = ers.validate_report(_minimal(stack_trace="B\n"))
        c = ers.validate_report(_minimal(stack_trace="C\n"))
        # A: 5 hits, B: 2, C: 1
        for _ in range(5):
            store.record(a)
        for _ in range(2):
            store.record(b)
        store.record(c)

        rows = store.summary()
        assert len(rows) == 3
        assert rows[0]["count"] == 5
        assert rows[1]["count"] == 2
        assert rows[2]["count"] == 1

    def test_limit_respected(self, store):
        for i in range(20):
            r = ers.validate_report(_minimal(stack_trace=f"crash {i}\n"))
            store.record(r)
        rows = store.summary(limit=5)
        assert len(rows) == 5

    def test_since_filter(self, store):
        # Record a few crashes at a fixed timestamp, then check the
        # since-filter excludes them.
        when = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
        r = ers.validate_report(_minimal(stack_trace="old\n"))
        store.record(r, now=when)
        # Record a NEW crash at a much later time
        later = _dt.datetime(2026, 5, 1, tzinfo=_dt.timezone.utc)
        r2 = ers.validate_report(_minimal(stack_trace="recent\n"))
        store.record(r2, now=later)

        cutoff = _dt.datetime(2026, 4, 1, tzinfo=_dt.timezone.utc)
        rows = store.summary(since=cutoff)
        assert len(rows) == 1
        assert "recent" in rows[0]["stack_trace_preview"]


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------


class TestParseSince:
    def test_hours(self):
        now = _dt.datetime(2026, 5, 13, 12, 0, tzinfo=_dt.timezone.utc)
        out = ers.parse_since("24h", now=now)
        assert out == _dt.datetime(2026, 5, 12, 12, 0, tzinfo=_dt.timezone.utc)

    def test_days(self):
        now = _dt.datetime(2026, 5, 13, tzinfo=_dt.timezone.utc)
        out = ers.parse_since("7d", now=now)
        assert out == _dt.datetime(2026, 5, 6, tzinfo=_dt.timezone.utc)

    def test_minutes(self):
        now = _dt.datetime(2026, 5, 13, 12, 0, tzinfo=_dt.timezone.utc)
        out = ers.parse_since("30m", now=now)
        assert out == _dt.datetime(2026, 5, 13, 11, 30, tzinfo=_dt.timezone.utc)

    def test_empty_returns_none(self):
        assert ers.parse_since("") is None
        assert ers.parse_since(None) is None

    def test_malformed_returns_none(self):
        assert ers.parse_since("forever") is None
        assert ers.parse_since("24 hours") is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_record_and_summary_roundtrip(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "errors.db"
        # Pipe a JSON body through stdin to the `record` subcommand.
        body = json.dumps(_minimal())
        monkeypatch.setattr("sys.stdin", _StringIO(body))
        rc = ers.main(["record", "--db", str(db)])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 1

        rc = ers.main(["summary", "--db", str(db)])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 1
        assert out["rows"][0]["count"] == 1


# Minimal stdin helper that supports both `.read()` and being passed to json.load.
class _StringIO:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


# ---------------------------------------------------------------------------
# PII scrub parity with TS implementation
# ---------------------------------------------------------------------------


class TestScrubParity:
    """Sanity tests that capture the algorithmic shape so the TS mirror
    (web-buyer/lib/error-report.ts) stays in sync.

    These are not full cross-runtime tests (we don't shell out to node),
    but they document the expected substitutions in a way that's easy
    to manually verify against the TS regex patterns.
    """

    def test_windows_user_canonical_output(self):
        out = ers.scrub_pii("C:\\Users\\Howard\\AppData\\Local\\foo.exe")
        assert out.startswith("C:\\Users\\<USER>")

    def test_unix_user_canonical_output(self):
        out = ers.scrub_pii("/Users/howard/Downloads/x")
        assert out.startswith("/Users/<USER>")

    def test_appdata_casing_preserved(self):
        out = ers.scrub_pii("C:\\Users\\Howard\\appdata\\local\\bug.log")
        # AppData casing is canonicalised
        assert "AppData\\Local" in out
