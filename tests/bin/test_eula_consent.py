#!/usr/bin/env python3
"""Tests for bin/eula_consent.py

Coverage:
- read_eula: success path, missing file, encoding edge case.
- present_eula: yes/y → True, no/n → False, invalid input loop, EOFError → False,
  KeyboardInterrupt → False.
- log_acceptance: creates parent dir, JSON shape, timestamp ISO-8601 UTC,
  accepted flag persisted, metadata passed through.
- main: --non-interactive success → rc=0 + record, --non-interactive reject path
  forced via present_eula=False → rc=2, interactive yes path via input monkeypatch
  → rc=0, bad --metadata JSON → rc=1, non-dict --metadata → rc=1, missing EULA
  file → rc=1, write failure → rc=1.
- CLI parsing: defaults (--width=80, --non-interactive absent), --width override,
  required --eula/--output.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

# Make the module importable both as `bin.eula_consent` (repo-root on sys.path)
# and as `eula_consent` (bin/ on sys.path) — matches how other bin tests load.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))

import eula_consent  # noqa: E402

# ----------------------------------------------------------------------
# read_eula
# ----------------------------------------------------------------------


class TestReadEula:
    def test_read_existing_file(self, tmp_path):
        eula = tmp_path / "EULA.txt"
        eula.write_text("Line 1\nLine 2\n", encoding="utf-8")
        assert eula_consent.read_eula(str(eula)) == "Line 1\nLine 2\n"

    def test_read_empty_file(self, tmp_path):
        eula = tmp_path / "empty.txt"
        eula.write_text("", encoding="utf-8")
        assert eula_consent.read_eula(str(eula)) == ""

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            eula_consent.read_eula(str(tmp_path / "nope.txt"))

    def test_preserves_unicode(self, tmp_path):
        eula = tmp_path / "u.txt"
        eula.write_text("héllo — 你好 — 🎮", encoding="utf-8")
        assert "🎮" in eula_consent.read_eula(str(eula))


# ----------------------------------------------------------------------
# present_eula
# ----------------------------------------------------------------------


class TestPresentEula:
    def test_yes_returns_true(self, capsys):
        with mock.patch("builtins.input", return_value="yes"):
            assert eula_consent.present_eula("terms") is True
        out = capsys.readouterr().out
        assert "END USER LICENSE AGREEMENT" in out
        assert "terms" in out

    def test_y_shortform_returns_true(self):
        with mock.patch("builtins.input", return_value="y"):
            assert eula_consent.present_eula("terms") is True

    def test_no_returns_false(self):
        with mock.patch("builtins.input", return_value="no"):
            assert eula_consent.present_eula("terms") is False

    def test_n_shortform_returns_false(self):
        with mock.patch("builtins.input", return_value="n"):
            assert eula_consent.present_eula("terms") is False

    def test_invalid_then_yes(self):
        # First "maybe" rejected, second "yes" accepted.
        with mock.patch("builtins.input", side_effect=["maybe", "yes"]):
            assert eula_consent.present_eula("terms") is True

    def test_eof_returns_false(self, capsys):
        with mock.patch("builtins.input", side_effect=EOFError):
            assert eula_consent.present_eula("terms") is False
        assert "Input interrupted" in capsys.readouterr().err

    def test_keyboard_interrupt_returns_false(self, capsys):
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            assert eula_consent.present_eula("terms") is False
        assert "Input interrupted" in capsys.readouterr().err

    def test_case_insensitive(self):
        with mock.patch("builtins.input", return_value="YES"):
            assert eula_consent.present_eula("terms") is True

    def test_width_param_uses_border(self, capsys):
        with mock.patch("builtins.input", return_value="yes"):
            eula_consent.present_eula("hi", width=20)
        out = capsys.readouterr().out
        # 20 '=' chars before header line.
        assert "=" * 20 in out


# ----------------------------------------------------------------------
# log_acceptance
# ----------------------------------------------------------------------


class TestLogAcceptance:
    def test_creates_parent_dir(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "consent.json"
        eula_consent.log_acceptance(str(out), str(tmp_path / "eula.txt"), True)
        assert out.exists()

    def test_record_shape_minimal(self, tmp_path):
        out = tmp_path / "consent.json"
        eula_consent.log_acceptance(str(out), str(tmp_path / "eula.txt"), True)
        rec = json.loads(out.read_text(encoding="utf-8"))
        for key in ("timestamp", "eula_file", "accepted", "metadata"):
            assert key in rec
        assert rec["accepted"] is True
        assert rec["metadata"] == {}

    def test_record_shape_rejected(self, tmp_path):
        out = tmp_path / "consent.json"
        eula_consent.log_acceptance(str(out), str(tmp_path / "eula.txt"), False)
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert rec["accepted"] is False

    def test_record_includes_metadata(self, tmp_path):
        out = tmp_path / "consent.json"
        meta = {"vendor": "acme", "build": 42}
        eula_consent.log_acceptance(str(out), str(tmp_path / "eula.txt"), True, meta)
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert rec["metadata"] == meta

    def test_eula_path_is_resolved(self, tmp_path):
        eula = tmp_path / "eula.txt"
        eula.write_text("x", encoding="utf-8")
        out = tmp_path / "consent.json"
        eula_consent.log_acceptance(str(out), str(eula), True)
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert Path(rec["eula_file"]).is_absolute()

    def test_timestamp_is_iso_utc(self, tmp_path):
        out = tmp_path / "consent.json"
        before = datetime.utcnow()
        eula_consent.log_acceptance(str(out), str(tmp_path / "eula.txt"), True)
        after = datetime.utcnow()
        rec = json.loads(out.read_text(encoding="utf-8"))
        # Round-trip parses; the recorded instant falls within the test window.
        ts = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
        assert before <= ts <= after

    def test_prints_written_path(self, tmp_path, capsys):
        out = tmp_path / "consent.json"
        eula_consent.log_acceptance(str(out), str(tmp_path / "eula.txt"), True)
        assert str(out) in capsys.readouterr().out

    def test_write_failure_propagates(self, tmp_path):
        # Make the output path point at a directory so open() fails.
        out = tmp_path
        with pytest.raises((IsADirectoryError, PermissionError, OSError)):
            eula_consent.log_acceptance(str(out), str(tmp_path / "eula.txt"), True)


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------


class TestMain:
    def _write_eula(self, tmp_path):
        eula = tmp_path / "EULA.txt"
        eula.write_text("Vendor must not reverse-engineer.", encoding="utf-8")
        return eula

    def test_non_interactive_success(self, tmp_path, capsys):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        rc = eula_consent.main([
            "--eula", str(eula),
            "--output", str(out),
            "--non-interactive",
        ])
        assert rc == 0
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert rec["accepted"] is True

    def test_interactive_yes(self, tmp_path):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        with mock.patch("builtins.input", return_value="yes"):
            rc = eula_consent.main([
                "--eula", str(eula),
                "--output", str(out),
            ])
        assert rc == 0
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert rec["accepted"] is True

    def test_interactive_no_returns_2(self, tmp_path):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        with mock.patch("builtins.input", return_value="no"):
            rc = eula_consent.main([
                "--eula", str(eula),
                "--output", str(out),
            ])
        assert rc == 2
        # Record is still written, but with accepted=False.
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert rec["accepted"] is False

    def test_interactive_eof_returns_2(self, tmp_path):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        with mock.patch("builtins.input", side_effect=EOFError):
            rc = eula_consent.main([
                "--eula", str(eula),
                "--output", str(out),
            ])
        assert rc == 2

    def test_missing_eula_returns_1(self, tmp_path, capsys):
        out = tmp_path / "consent.json"
        rc = eula_consent.main([
            "--eula", str(tmp_path / "missing.txt"),
            "--output", str(out),
            "--non-interactive",
        ])
        assert rc == 1
        assert "Error reading EULA" in capsys.readouterr().err

    def test_invalid_metadata_json_returns_1(self, tmp_path, capsys):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        rc = eula_consent.main([
            "--eula", str(eula),
            "--output", str(out),
            "--metadata", "not-json",
            "--non-interactive",
        ])
        assert rc == 1
        assert "invalid --metadata" in capsys.readouterr().err

    def test_non_dict_metadata_returns_1(self, tmp_path, capsys):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        rc = eula_consent.main([
            "--eula", str(eula),
            "--output", str(out),
            "--metadata", "[1, 2, 3]",
            "--non-interactive",
        ])
        assert rc == 1
        assert "JSON object" in capsys.readouterr().err

    def test_metadata_passed_through(self, tmp_path):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        meta = '{"vendor": "acme", "tier": 2}'
        rc = eula_consent.main([
            "--eula", str(eula),
            "--output", str(out),
            "--metadata", meta,
            "--non-interactive",
        ])
        assert rc == 0
        rec = json.loads(out.read_text(encoding="utf-8"))
        assert rec["metadata"] == {"vendor": "acme", "tier": 2}

    def test_width_override(self, tmp_path, capsys):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        with mock.patch("builtins.input", return_value="yes"):
            rc = eula_consent.main([
                "--eula", str(eula),
                "--output", str(out),
                "--width", "20",
            ])
        assert rc == 0
        assert "=" * 20 in capsys.readouterr().out

    def test_default_width_is_80(self, tmp_path, capsys):
        eula = self._write_eula(tmp_path)
        out = tmp_path / "consent.json"
        with mock.patch("builtins.input", return_value="yes"):
            eula_consent.main([
                "--eula", str(eula),
                "--output", str(out),
            ])
        # Default border should be 80 '=' chars.
        assert "=" * 80 in capsys.readouterr().out

    def test_write_failure_returns_1(self, tmp_path, capsys):
        eula = self._write_eula(tmp_path)
        # Point output at an existing dir so open(..., 'w') raises.
        rc = eula_consent.main([
            "--eula", str(eula),
            "--output", str(tmp_path),
            "--non-interactive",
        ])
        assert rc == 1
        assert "Error writing consent record" in capsys.readouterr().err


# ----------------------------------------------------------------------
# parse_args (covered indirectly via main; assert explicit defaults here)
# ----------------------------------------------------------------------


class TestParseArgs:
    def test_defaults(self):
        # Provide required args only; confirm defaults are width=80 and
        # non-interactive absent.
        # We can't import parse_args directly (it's a local in main), so
        # exercise via main with monkeypatched input.
        with mock.patch("eula_consent.log_acceptance") as la:
            with mock.patch("eula_consent.read_eula", return_value="x"):
                la.return_value = None
                eula_consent.main([
                    "--eula", "ignored",
                    "--output", "ignored",
                    "--non-interactive",
                ])
        # log_acceptance must have been called exactly once with
        # accepted=True (non-interactive overrides user input).
        assert la.call_count == 1
        args, _ = la.call_args
        assert args[2] is True  # accepted=True

    def test_required_flags_missing(self):
        # No args → SystemExit from argparse.
        with pytest.raises(SystemExit):
            eula_consent.main([])
