#!/usr/bin/env python3
"""
tests/test_first_run_consent.py

Tests for bin/first_run_consent.py and bin/consent_dialog_cli.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bin.consent_dialog_cli import _ask, run_dialog
from bin.first_run_consent import (
    CONSENT_VERSION,
    ConsentRecord,
    _compute_sig,
    build_consent,
    consent_exists,
    load_consent,
    run_consent_flow,
    save_consent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_consent_path(tmp_path: Path) -> Path:
    """Provide a temporary consent.json path."""
    return tmp_path / "consent.json"


@pytest.fixture
def sample_record() -> ConsentRecord:
    """A valid ConsentRecord for testing."""
    ts = "2025-01-15T10:00:00+00:00"
    return build_consent(
        screen_record=True,
        upload=True,
        oauth=True,
        auto_update=True,
        telemetry=False,
        timestamp=ts,
    )


# ---------------------------------------------------------------------------
# _compute_sig
# ---------------------------------------------------------------------------


class TestComputeSig:
    def test_deterministic(self) -> None:
        sig1 = _compute_sig("2025-01-01T00:00:00+00:00", "v0.5.0")
        sig2 = _compute_sig("2025-01-01T00:00:00+00:00", "v0.5.0")
        assert sig1 == sig2

    def test_matches_raw_sha256(self) -> None:
        ts = "2025-06-01T12:00:00+00:00"
        ver = "v0.5.0"
        expected = hashlib.sha256(f"{ts}{ver}".encode("utf-8")).hexdigest()
        assert _compute_sig(ts, ver) == expected

    def test_different_inputs_different_sig(self) -> None:
        s1 = _compute_sig("t1", "v1")
        s2 = _compute_sig("t2", "v1")
        s3 = _compute_sig("t1", "v2")
        assert s1 != s2
        assert s1 != s3


# ---------------------------------------------------------------------------
# build_consent
# ---------------------------------------------------------------------------


class TestBuildConsent:
    def test_all_fields_set(self) -> None:
        ts = "2025-01-01T00:00:00+00:00"
        rec = build_consent(
            screen_record=True,
            upload=False,
            oauth=True,
            auto_update=False,
            telemetry=False,
            timestamp=ts,
        )
        assert rec.version == CONSENT_VERSION
        assert rec.timestamp == ts
        assert rec.screen_record is True
        assert rec.upload is False
        assert rec.oauth is True
        assert rec.auto_update is False
        assert rec.telemetry is False
        assert rec.user_sig == _compute_sig(ts, CONSENT_VERSION)

    def test_default_timestamp(self) -> None:
        rec = build_consent(
            screen_record=True,
            upload=True,
            oauth=True,
            auto_update=True,
            telemetry=False,
        )
        assert rec.timestamp is not None
        assert "T" in rec.timestamp  # ISO-8601 contains 'T'

    def test_default_telemetry_false(self) -> None:
        rec = build_consent(
            screen_record=True,
            upload=True,
            oauth=True,
            auto_update=True,
        )
        assert rec.telemetry is False


# ---------------------------------------------------------------------------
# save_consent / load_consent / consent_exists
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load(self, tmp_consent_path: Path, sample_record: ConsentRecord) -> None:
        save_consent(sample_record, tmp_consent_path)
        assert tmp_consent_path.exists()

        loaded = load_consent(tmp_consent_path)
        assert loaded is not None
        assert loaded.version == sample_record.version
        assert loaded.timestamp == sample_record.timestamp
        assert loaded.screen_record == sample_record.screen_record
        assert loaded.user_sig == sample_record.user_sig

    def test_consent_exists_true(
        self, tmp_consent_path: Path, sample_record: ConsentRecord
    ) -> None:
        save_consent(sample_record, tmp_consent_path)
        assert consent_exists(tmp_consent_path) is True

    def test_consent_exists_false_no_file(self, tmp_consent_path: Path) -> None:
        assert consent_exists(tmp_consent_path) is False

    def test_consent_exists_false_invalid_json(self, tmp_consent_path: Path) -> None:
        tmp_consent_path.write_text("not json")
        assert consent_exists(tmp_consent_path) is False

    def test_consent_exists_false_missing_keys(self, tmp_consent_path: Path) -> None:
        tmp_consent_path.write_text(json.dumps({"version": "v0.5.0"}))
        assert consent_exists(tmp_consent_path) is False

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "consent.json"
        rec = build_consent(True, True, True, True, False)
        save_consent(rec, nested)
        assert nested.exists()

    def test_json_structure(self, tmp_consent_path: Path, sample_record: ConsentRecord) -> None:
        save_consent(sample_record, tmp_consent_path)
        with open(tmp_consent_path, "r") as f:
            data = json.load(f)
        expected_keys = {
            "version",
            "timestamp",
            "screen_record",
            "upload",
            "oauth",
            "auto_update",
            "telemetry",
            "user_sig",
        }
        assert set(data.keys()) == expected_keys


# ---------------------------------------------------------------------------
# run_consent_flow
# ---------------------------------------------------------------------------


class TestConsentFlow:
    def test_fast_path_existing_consent(
        self, tmp_consent_path: Path, sample_record: ConsentRecord
    ) -> None:
        save_consent(sample_record, tmp_consent_path)
        result = run_consent_flow(dialog_fn=lambda: {}, consent_path=tmp_consent_path)
        assert result == 0

    def test_reject_screen_record_exits_1(self, tmp_consent_path: Path) -> None:
        def fake_dialog() -> dict:
            return {
                "screen_record": False,
                "upload": True,
                "oauth": True,
                "auto_update": True,
                "telemetry": False,
            }

        result = run_consent_flow(dialog_fn=fake_dialog, consent_path=tmp_consent_path)
        assert result == 1
        assert not consent_exists(tmp_consent_path)

    def test_accept_all_writes_consent(self, tmp_consent_path: Path) -> None:
        def fake_dialog() -> dict:
            return {
                "screen_record": True,
                "upload": True,
                "oauth": True,
                "auto_update": True,
                "telemetry": False,
            }

        result = run_consent_flow(dialog_fn=fake_dialog, consent_path=tmp_consent_path)
        assert result == 0
        assert consent_exists(tmp_consent_path)

        loaded = load_consent(tmp_consent_path)
        assert loaded is not None
        assert loaded.screen_record is True
        assert loaded.upload is True
        assert loaded.oauth is True
        assert loaded.auto_update is True
        assert loaded.telemetry is False

    def test_telemetry_defaults_false_in_flow(self, tmp_consent_path: Path) -> None:
        def fake_dialog() -> dict:
            return {
                "screen_record": True,
                "upload": True,
                "oauth": True,
                "auto_update": True,
                "telemetry": False,
            }

        run_consent_flow(dialog_fn=fake_dialog, consent_path=tmp_consent_path)
        loaded = load_consent(tmp_consent_path)
        assert loaded is not None
        assert loaded.telemetry is False

    def test_partial_accept(self, tmp_consent_path: Path) -> None:
        def fake_dialog() -> dict:
            return {
                "screen_record": True,
                "upload": False,
                "oauth": False,
                "auto_update": True,
                "telemetry": False,
            }

        result = run_consent_flow(dialog_fn=fake_dialog, consent_path=tmp_consent_path)
        assert result == 0
        loaded = load_consent(tmp_consent_path)
        assert loaded is not None
        assert loaded.screen_record is True
        assert loaded.upload is False
        assert loaded.oauth is False
        assert loaded.auto_update is True

    def test_signature_is_valid(self, tmp_consent_path: Path) -> None:
        def fake_dialog() -> dict:
            return {
                "screen_record": True,
                "upload": True,
                "oauth": True,
                "auto_update": True,
                "telemetry": False,
            }

        run_consent_flow(dialog_fn=fake_dialog, consent_path=tmp_consent_path)
        loaded = load_consent(tmp_consent_path)
        assert loaded is not None
        expected_sig = _compute_sig(loaded.timestamp, loaded.version)
        assert loaded.user_sig == expected_sig


# ---------------------------------------------------------------------------
# consent_dialog_cli — _ask
# ---------------------------------------------------------------------------


class TestAsk:
    def test_yes_response(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value="y"):
            assert _ask("Test?", True) is True

    def test_no_response(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value="n"):
            assert _ask("Test?", True) is False

    def test_yes_full(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value="yes"):
            assert _ask("Test?", False) is True

    def test_no_full(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value="no"):
            assert _ask("Test?", True) is False

    def test_default_yes(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value=""):
            assert _ask("Test?", True) is True

    def test_default_no(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value=""):
            assert _ask("Test?", False) is False

    def test_case_insensitive(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value="Y"):
            assert _ask("Test?", False) is True
        with patch("bin.consent_dialog_cli.input", return_value="N"):
            assert _ask("Test?", True) is False

    def test_eof_returns_false(self) -> None:
        with patch("bin.consent_dialog_cli.input", side_effect=EOFError):
            assert _ask("Test?", True) is False

    def test_invalid_then_valid(self) -> None:
        with patch("bin.consent_dialog_cli.input", side_effect=["maybe", "y"]):
            assert _ask("Test?", False) is True


# ---------------------------------------------------------------------------
# consent_dialog_cli — run_dialog
# ---------------------------------------------------------------------------


class TestRunDialog:
    def test_returns_all_keys(self) -> None:
        expected_keys = {"screen_record", "upload", "oauth", "auto_update", "telemetry"}
        with patch("bin.consent_dialog_cli.input", return_value="y"):
            result = run_dialog()
        assert set(result.keys()) == expected_keys

    def test_all_yes(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value="y"):
            result = run_dialog()
        assert all(result.values()) is True

    def test_all_no(self) -> None:
        with patch("bin.consent_dialog_cli.input", return_value="n"):
            result = run_dialog()
        assert all(v is False for v in result.values())

    def test_telemetry_default_no(self) -> None:
        # Simulate user pressing Enter (empty) for all prompts
        with patch("bin.consent_dialog_cli.input", return_value=""):
            result = run_dialog()
        # First 4 default to yes, telemetry defaults to no
        assert result["screen_record"] is True
        assert result["upload"] is True
        assert result["oauth"] is True
        assert result["auto_update"] is True
        assert result["telemetry"] is False

    def test_mixed_responses(self) -> None:
        responses = ["y", "n", "y", "n", "y"]
        with patch("bin.consent_dialog_cli.input", side_effect=responses):
            result = run_dialog()
        assert result["screen_record"] is True
        assert result["upload"] is False
        assert result["oauth"] is True
        assert result["auto_update"] is False
        assert result["telemetry"] is True


# ---------------------------------------------------------------------------
# ConsentRecord serialization
# ---------------------------------------------------------------------------


class TestConsentRecordSerialization:
    def test_to_dict(self, sample_record: ConsentRecord) -> None:
        d = sample_record.to_dict()
        assert isinstance(d, dict)
        assert d["version"] == sample_record.version
        assert d["screen_record"] is True

    def test_from_dict(self, sample_record: ConsentRecord) -> None:
        d = sample_record.to_dict()
        restored = ConsentRecord.from_dict(d)
        assert restored == sample_record

    def test_roundtrip(self, sample_record: ConsentRecord) -> None:
        d = sample_record.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        restored = ConsentRecord.from_dict(parsed)
        assert restored == sample_record


# ---------------------------------------------------------------------------
# Integration: full flow with mocked I/O
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_full_first_run(self, tmp_consent_path: Path) -> None:
        """Simulate a complete first-run: no consent → dialog → save."""
        assert consent_exists(tmp_consent_path) is False

        def fake_dialog() -> dict:
            return {
                "screen_record": True,
                "upload": True,
                "oauth": True,
                "auto_update": True,
                "telemetry": False,
            }

        rc = run_consent_flow(dialog_fn=fake_dialog, consent_path=tmp_consent_path)
        assert rc == 0
        assert consent_exists(tmp_consent_path) is True

        # Second run should fast-path
        rc2 = run_consent_flow(dialog_fn=fake_dialog, consent_path=tmp_consent_path)
        assert rc2 == 0

    def test_reject_then_accept(self, tmp_consent_path: Path) -> None:
        """Reject screen_record first, then accept on second attempt."""

        def reject() -> dict:
            return {
                "screen_record": False,
                "upload": True,
                "oauth": True,
                "auto_update": True,
                "telemetry": False,
            }

        def accept() -> dict:
            return {
                "screen_record": True,
                "upload": True,
                "oauth": True,
                "auto_update": True,
                "telemetry": False,
            }

        # First attempt: reject
        rc1 = run_consent_flow(dialog_fn=reject, consent_path=tmp_consent_path)
        assert rc1 == 1
        assert consent_exists(tmp_consent_path) is False

        # Second attempt: accept
        rc2 = run_consent_flow(dialog_fn=accept, consent_path=tmp_consent_path)
        assert rc2 == 0
        assert consent_exists(tmp_consent_path) is True
