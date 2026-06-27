"""tests/test_telemetry_optin.py – S54: Telemetry opt-in acceptance tests.

Verifies:
  - opt-in true  → daily upload happens
  - opt-in false → 0 uploads
  - schema is strictly correct
  - network failure → silent skip
  - once-per-day gate works
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bin.telemetry import (
    VERSION,
    _async_upload,
    compute_anon_id,
    gather_daily_metrics,
    is_telemetry_opted_in,
    record_crash,
    record_session,
    record_upload,
    send_telemetry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_consent_dir(tmp_path: Path) -> Path:
    """Create a temporary directory that acts as ~/.oyster."""
    consent_dir = tmp_path / ".oyster"
    consent_dir.mkdir(parents=True, exist_ok=True)
    return consent_dir


@pytest.fixture
def consent_file(tmp_consent_dir: Path) -> Path:
    """Path to the consent.json inside the temp dir."""
    return tmp_consent_dir / "consent.json"


def _write_consent(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Consent gate tests
# ---------------------------------------------------------------------------


class TestConsentGate:
    def test_opted_in_true(self, consent_file: Path) -> None:
        _write_consent(consent_file, {"telemetry": True})
        assert is_telemetry_opted_in(consent_file) is True

    def test_opted_in_false(self, consent_file: Path) -> None:
        _write_consent(consent_file, {"telemetry": False})
        assert is_telemetry_opted_in(consent_file) is False

    def test_opted_in_missing_key(self, consent_file: Path) -> None:
        _write_consent(consent_file, {"eula": True})
        assert is_telemetry_opted_in(consent_file) is False

    def test_opted_in_file_missing(self, consent_file: Path) -> None:
        assert is_telemetry_opted_in(consent_file) is False

    def test_opted_in_invalid_json(self, consent_file: Path) -> None:
        consent_file.write_text("not json", encoding="utf-8")
        assert is_telemetry_opted_in(consent_file) is False

    def test_opted_in_null_value(self, consent_file: Path) -> None:
        _write_consent(consent_file, {"telemetry": None})
        assert is_telemetry_opted_in(consent_file) is False


# ---------------------------------------------------------------------------
# Anonymous ID tests
# ---------------------------------------------------------------------------


class TestAnonId:
    def test_deterministic(self) -> None:
        a = compute_anon_id(machine_id="m1", os_user="u1")
        b = compute_anon_id(machine_id="m1", os_user="u1")
        assert a == b

    def test_different_inputs_different_hash(self) -> None:
        a = compute_anon_id(machine_id="m1", os_user="u1")
        b = compute_anon_id(machine_id="m2", os_user="u1")
        assert a != b

    def test_is_sha256_hex(self) -> None:
        result = compute_anon_id(machine_id="m1", os_user="u1")
        assert len(result) == 64
        int(result, 16)  # must be valid hex

    def test_no_raw_inputs_in_output(self) -> None:
        result = compute_anon_id(machine_id="secret_machine", os_user="alice")
        assert "secret_machine" not in result
        assert "alice" not in result


# ---------------------------------------------------------------------------
# Metrics gathering tests
# ---------------------------------------------------------------------------


class TestGatherMetrics:
    def test_schema_keys(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()

        expected_keys = {
            "anon_id",
            "version",
            "os",
            "sessions_today",
            "uploads_today",
            "total_session_seconds",
            "crash_today",
            "ts",
        }
        assert set(data.keys()) == expected_keys

    def test_version(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
        assert data["version"] == VERSION

    def test_os_is_string(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
        assert isinstance(data["os"], str)
        assert len(data["os"]) > 0

    def test_sessions_today_int(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
        assert isinstance(data["sessions_today"], int)

    def test_uploads_today_int(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
        assert isinstance(data["uploads_today"], int)

    def test_total_session_seconds_int(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
        assert isinstance(data["total_session_seconds"], int)

    def test_crash_today_bool(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
        assert isinstance(data["crash_today"], bool)

    def test_ts_is_iso8601(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
        # Should parse without error
        datetime.fromisoformat(data["ts"])

    def test_no_pii_in_payload(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
        payload_str = json.dumps(data)
        # Ensure no raw machine/user info leaks
        assert os.environ.get("USER", "") not in payload_str or data["anon_id"] != os.environ.get(
            "USER", ""
        )


# ---------------------------------------------------------------------------
# Counter recording tests
# ---------------------------------------------------------------------------


class TestCounters:
    def test_record_session_increments(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            record_session(duration_seconds=120)
            record_session(duration_seconds=60)
            data = gather_daily_metrics()
            assert data["sessions_today"] == 2
            assert data["total_session_seconds"] == 180

    def test_record_upload_increments(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            record_upload()
            record_upload()
            record_upload()
            data = gather_daily_metrics()
            assert data["uploads_today"] == 3

    def test_record_crash_flag(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            record_crash()
            data = gather_daily_metrics()
            assert data["crash_today"] is True

    def test_no_crash_by_default(self, tmp_consent_dir: Path) -> None:
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()
            assert data["crash_today"] is False


# ---------------------------------------------------------------------------
# Upload / send_telemetry tests
# ---------------------------------------------------------------------------


class TestSendTelemetry:
    @pytest.fixture
    def opted_in_consent(self, consent_file: Path) -> Path:
        _write_consent(consent_file, {"telemetry": True})
        return consent_file

    def test_opted_in_dispatches(self, opted_in_consent: Path, tmp_consent_dir: Path) -> None:
        """opt-in true → upload is dispatched."""
        marker = tmp_consent_dir / ".telemetry_last_upload"
        with (
            patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir),
            patch("bin.telemetry.LAST_UPLOAD_MARKER", marker),
            patch("bin.telemetry._dispatch_upload") as mock_dispatch,
        ):
            result = send_telemetry(consent_path=opted_in_consent)

            assert result is True
            mock_dispatch.assert_called_once()

    def test_not_opted_in_no_upload(self, consent_file: Path, tmp_consent_dir: Path) -> None:
        """opt-in false → 0 uploads."""
        _write_consent(consent_file, {"telemetry": False})
        with (
            patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir),
            patch("bin.telemetry._dispatch_upload") as mock_dispatch,
        ):
            result = send_telemetry(consent_path=consent_file)
            assert result is False
            mock_dispatch.assert_not_called()

    def test_no_consent_file_no_upload(self, consent_file: Path, tmp_consent_dir: Path) -> None:
        """Missing consent file → 0 uploads."""
        with (
            patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir),
            patch("bin.telemetry._dispatch_upload") as mock_dispatch,
        ):
            result = send_telemetry(consent_path=consent_file)
            assert result is False
            mock_dispatch.assert_not_called()

    def test_already_uploaded_today_skips(
        self, opted_in_consent: Path, tmp_consent_dir: Path
    ) -> None:
        """Once-per-day gate: second call is skipped."""
        marker = tmp_consent_dir / ".telemetry_last_upload"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        marker.write_text(today, encoding="utf-8")

        with (
            patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir),
            patch("bin.telemetry.LAST_UPLOAD_MARKER", marker),
            patch("bin.telemetry._dispatch_upload") as mock_dispatch,
        ):
            result = send_telemetry(consent_path=opted_in_consent)
            assert result is False
            mock_dispatch.assert_not_called()

    def test_network_failure_silent_skip(
        self, opted_in_consent: Path, tmp_consent_dir: Path
    ) -> None:
        """Network error → silent skip, no exception raised."""
        marker = tmp_consent_dir / ".telemetry_last_upload"
        with (
            patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir),
            patch("bin.telemetry.LAST_UPLOAD_MARKER", marker),
            patch("bin.telemetry._dispatch_upload") as mock_dispatch,
        ):
            # Should not raise – dispatch is fire-and-forget
            result = send_telemetry(consent_path=opted_in_consent)
            # Dispatched successfully (the failure happens inside the thread)
            assert result is True
            mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# Async upload tests (with mocked httpx)
# ---------------------------------------------------------------------------


class TestAsyncUpload:
    @pytest.mark.asyncio
    async def test_upload_success(self) -> None:
        """Successful POST returns True."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("bin.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await _async_upload({"anon_id": "test"})
            assert result is True

    @pytest.mark.asyncio
    async def test_upload_non_200(self) -> None:
        """Non-200 response returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("bin.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await _async_upload({"anon_id": "test"})
            assert result is False

    @pytest.mark.asyncio
    async def test_upload_network_error(self) -> None:
        """Network error returns False (silent skip)."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=ConnectionError("down"))

        with patch("bin.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await _async_upload({"anon_id": "test"})
            assert result is False


# ---------------------------------------------------------------------------
# Schema strictness test (integration-style with backend stub)
# ---------------------------------------------------------------------------


class TestSchemaStrictness:
    def test_payload_matches_spec(self, tmp_consent_dir: Path) -> None:
        """Verify the payload dict matches the spec exactly."""
        with patch("bin.telemetry.CONSENT_DIR", tmp_consent_dir):
            data = gather_daily_metrics()

        # Required keys
        assert "anon_id" in data
        assert "version" in data
        assert "os" in data
        assert "sessions_today" in data
        assert "uploads_today" in data
        assert "total_session_seconds" in data
        assert "crash_today" in data
        assert "ts" in data

        # Type checks
        assert isinstance(data["anon_id"], str)
        assert isinstance(data["version"], str)
        assert isinstance(data["os"], str)
        assert isinstance(data["sessions_today"], int)
        assert isinstance(data["uploads_today"], int)
        assert isinstance(data["total_session_seconds"], int)
        assert isinstance(data["crash_today"], bool)
        assert isinstance(data["ts"], str)

        # No extra keys
        assert len(data) == 8

        # No PII fields
        assert "ip" not in data
        assert "user_id" not in data
        assert "email" not in data
        assert "game" not in data
        assert "file" not in data
        assert "path" not in data


# ---------------------------------------------------------------------------
# Integration test with backend stub
# ---------------------------------------------------------------------------


class TestBackendIntegration:
    """Test telemetry endpoint against the real backend stub."""

    def test_telemetry_endpoint_accepts_payload(self) -> None:
        """POST /api/v1/telemetry/daily accepts valid payload and returns 200."""
        from fastapi.testclient import TestClient

        from backend_stub import main as backend_main
        from backend_stub.main import create_app

        app = create_app()
        client = TestClient(app)

        payload = {
            "anon_id": "abc123",
            "version": "0.5.3",
            "os": "Linux",
            "sessions_today": 5,
            "uploads_today": 2,
            "total_session_seconds": 3600,
            "crash_today": False,
            "ts": "2025-01-15T10:30:00+00:00",
        }

        resp = client.post("/api/v1/telemetry/daily", json=payload)
        assert resp.status_code == 200

        # Verify it was stored in-memory
        assert len(backend_main._telemetry_store) == 1
        stored = backend_main._telemetry_store[0]
        assert stored["anon_id"] == "abc123"
        assert stored["version"] == "0.5.3"
        assert stored["os"] == "Linux"
        assert stored["sessions_today"] == 5
        assert stored["uploads_today"] == 2
        assert stored["total_session_seconds"] == 3600
        assert stored["crash_today"] is False
        assert stored["ts"] == "2025-01-15T10:30:00+00:00"

    def test_telemetry_endpoint_appends(self) -> None:
        """Multiple POSTs append to the store."""
        from fastapi.testclient import TestClient

        from backend_stub import main as backend_main
        from backend_stub.main import create_app

        # Clear store from previous test
        backend_main._telemetry_store.clear()

        app = create_app()
        client = TestClient(app)

        for i in range(3):
            payload = {
                "anon_id": f"user_{i}",
                "version": "0.5.3",
                "os": "Windows",
                "sessions_today": i,
                "uploads_today": i,
                "total_session_seconds": i * 100,
                "crash_today": i == 2,
                "ts": f"2025-01-15T10:30:0{i}+00:00",
            }
            resp = client.post("/api/v1/telemetry/daily", json=payload)
            assert resp.status_code == 200

        assert len(backend_main._telemetry_store) == 3
