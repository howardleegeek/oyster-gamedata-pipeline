"""Tests for the recorder local smoke pipeline.

Covers:
  - ``bin.mock_game_detector`` — fake detection
  - ``bin.mock_obs_recorder`` — fake mp4 + metadata.json
  - ``bin.recorder_local_smoke`` — orchestrator (with mocked HTTP)
  - ``bin.backend_stub`` — FastAPI stub endpoints

All HTTP calls are mocked via ``httpx`` respx so no real server is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Repo paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# mock_game_detector tests
# ---------------------------------------------------------------------------


class TestMockGameDetector:
    """Tests for bin.mock_game_detector."""

    def test_detect_game_returns_expected_keys(self):
        from bin.mock_game_detector import detect_game

        result = detect_game()
        assert "game" in result
        assert "pid" in result
        assert "window_title" in result

    def test_detect_game_default_values(self):
        from bin.mock_game_detector import detect_game

        result = detect_game()
        assert result["game"] == "minecraft"
        assert result["pid"] == 12345
        assert result["window_title"] == "MC 1.21.4"

    def test_detect_game_override(self):
        from bin.mock_game_detector import detect_game

        result = detect_game(override={"game": "cs2", "pid": 99999})
        assert result["game"] == "cs2"
        assert result["pid"] == 99999
        assert result["window_title"] == "MC 1.21.4"  # unchanged

    def test_main_cli(self, capsys):
        from bin.mock_game_detector import main

        rc = main([])
        assert rc == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["game"] == "minecraft"


# ---------------------------------------------------------------------------
# mock_obs_recorder tests
# ---------------------------------------------------------------------------


class TestMockObsRecorder:
    """Tests for bin.mock_obs_recorder."""

    def test_write_fake_recording_creates_files(self, tmp_path: Path):
        from bin.mock_obs_recorder import write_fake_recording

        result = write_fake_recording(tmp_path, session_id="test-sid-123")
        assert result["video"].exists()
        assert result["metadata"].exists()

    def test_fake_mp4_has_valid_header(self, tmp_path: Path):
        from bin.mock_obs_recorder import write_fake_recording

        result = write_fake_recording(tmp_path)
        data = result["video"].read_bytes()
        # ftyp box starts at offset 4
        assert data[4:8] == b"ftyp"
        assert data[8:12] == b"isom"

    def test_fake_mp4_has_zero_payload(self, tmp_path: Path):
        from bin.mock_obs_recorder import (
            _MP4_FTYPE_HEADER,
            _ZERO_PAYLOAD_SIZE,
            write_fake_recording,
        )

        result = write_fake_recording(tmp_path)
        data = result["video"].read_bytes()
        expected_size = len(_MP4_FTYPE_HEADER) + _ZERO_PAYLOAD_SIZE
        assert len(data) == expected_size
        # After header, all zeros
        assert data[len(_MP4_FTYPE_HEADER) :] == b"\x00" * _ZERO_PAYLOAD_SIZE

    def test_metadata_json_content(self, tmp_path: Path):
        from bin.mock_obs_recorder import write_fake_recording

        result = write_fake_recording(
            tmp_path,
            session_id="test-sid-456",
            game="cs2",
            pid=99999,
            window_title="Counter-Strike 2",
        )
        meta = json.loads(result["metadata"].read_text())
        assert meta["session_id"] == "test-sid-456"
        assert meta["game"] == "cs2"
        assert meta["pid"] == 99999
        assert meta["window_title"] == "Counter-Strike 2"
        assert meta["location"] == "anonymous"
        assert "device_id" in meta
        assert "timestamp" in meta
        assert meta["recorder"] == "mock_obs_recorder"
        assert meta["duration_sec"] == 30

    def test_metadata_has_required_fields(self, tmp_path: Path):
        from bin.mock_obs_recorder import write_fake_recording

        result = write_fake_recording(tmp_path)
        meta = json.loads(result["metadata"].read_text())
        required = [
            "timestamp",
            "location",
            "device_id",
            "session_id",
            "game",
            "pid",
            "window_title",
            "recorder",
            "duration_sec",
        ]
        for field in required:
            assert field in meta, f"Missing field: {field}"

    def test_main_cli(self, tmp_path: Path, capsys):
        from bin.mock_obs_recorder import main

        rc = main(["--output-dir", str(tmp_path)])
        assert rc == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "video" in result
        assert "metadata" in result


# ---------------------------------------------------------------------------
# backend_stub tests
# ---------------------------------------------------------------------------


class TestBackendStub:
    """Tests for bin.backend_stub FastAPI endpoints."""

    @pytest.fixture
    def app(self):
        from bin.backend_stub import create_app

        app = create_app()
        app._reset_store()
        return app

    @pytest.fixture
    def client(self, app):
        from fastapi.testclient import TestClient

        return TestClient(app)

    def test_health(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_create_session(self, client):
        resp = client.post(
            "/v1/sessions",
            data={
                "session_id": "test-sid-001",
                "game": "minecraft",
                "pid": "12345",
                "window_title": "MC 1.21.4",
                "device_id": "abc123",
            },
            files={"video": ("recording.mp4", b"fake-video-data")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["session_id"] == "test-sid-001"
        assert body["status"] == "received"
        assert body["video_size_bytes"] == len(b"fake-video-data")

    def test_get_session(self, client):
        # Create first
        client.post(
            "/v1/sessions",
            data={"session_id": "test-sid-002", "game": "minecraft"},
            files={"video": ("recording.mp4", b"x" * 100)},
        )
        # Then get
        resp = client.get("/v1/sessions/test-sid-002")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "test-sid-002"
        assert body["status"] == "received"

    def test_get_session_not_found(self, client):
        resp = client.get("/v1/sessions/nonexistent")
        assert resp.status_code == 404

    def test_list_sessions(self, client):
        client.post(
            "/v1/sessions",
            data={"session_id": "sid-a", "game": "minecraft"},
            files={"video": ("a.mp4", b"a")},
        )
        client.post(
            "/v1/sessions",
            data={"session_id": "sid-b", "game": "cs2"},
            files={"video": ("b.mp4", b"b")},
        )
        resp = client.get("/v1/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 2
        sids = {s["session_id"] for s in sessions}
        assert "sid-a" in sids
        assert "sid-b" in sids

    def test_create_session_with_metadata_json(self, client):
        meta = json.dumps({"custom_field": "custom_value"})
        resp = client.post(
            "/v1/sessions",
            data={
                "session_id": "test-sid-meta",
                "metadata_json": meta,
            },
            files={"video": ("v.mp4", b"v")},
        )
        assert resp.status_code == 201

        # Verify metadata was stored
        resp = client.get("/v1/sessions/test-sid-meta")
        assert resp.status_code == 200
        body = resp.json()
        assert body["metadata"]["custom_field"] == "custom_value"

    def test_create_session_invalid_metadata_json(self, client):
        resp = client.post(
            "/v1/sessions",
            data={
                "session_id": "test-sid-bad",
                "metadata_json": "not valid json{{{",
            },
            files={"video": ("v.mp4", b"v")},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# recorder_local_smoke orchestrator tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestRecorderLocalSmoke:
    """Tests for bin.recorder_local_smoke orchestrator with mocked HTTP."""

    @pytest.fixture
    def backend_url(self):
        return "http://localhost:8500"

    def _make_mock_client(self, session_id: str):
        """Create a mock httpx.Client that simulates the backend stub."""
        mock_response_upload = mock.MagicMock()
        mock_response_upload.status_code = 201
        mock_response_upload.json.return_value = {
            "session_id": session_id,
            "status": "received",
            "video_size_bytes": 1060,
        }
        mock_response_upload.raise_for_status = mock.MagicMock()

        mock_response_verify = mock.MagicMock()
        mock_response_verify.status_code = 200
        mock_response_verify.json.return_value = {
            "session_id": session_id,
            "status": "received",
            "metadata": {
                "game": "minecraft",
                "pid": 12345,
                "window_title": "MC 1.21.4",
            },
            "video_size_bytes": 1060,
        }
        mock_response_verify.raise_for_status = mock.MagicMock()

        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.post.return_value = mock_response_upload
        mock_client.get.return_value = mock_response_verify

        return mock_client

    def test_step_detect_game(self):
        from bin.recorder_local_smoke import step_detect_game

        result = step_detect_game()
        assert result["game"] == "minecraft"
        assert result["pid"] == 12345

    def test_step_record(self, tmp_path: Path):
        from bin.recorder_local_smoke import step_record

        detection = {"game": "minecraft", "pid": 12345, "window_title": "MC 1.21.4"}
        result = step_record(tmp_path, detection, "test-session-id")
        assert result["video"].exists()
        assert result["metadata"].exists()

    def test_step_upload_mocked(self, tmp_path: Path, backend_url: str):
        from bin.mock_obs_recorder import write_fake_recording
        from bin.recorder_local_smoke import step_upload

        files = write_fake_recording(tmp_path, session_id="test-sid-upload")
        mock_client = self._make_mock_client("test-sid-upload")

        with mock.patch("httpx.Client", return_value=mock_client):
            result = step_upload(backend_url, "test-sid-upload", files["video"], files["metadata"])
            assert result["session_id"] == "test-sid-upload"
            assert result["status"] == "received"

    def test_step_verify_mocked(self, backend_url: str):
        from bin.recorder_local_smoke import step_verify

        mock_client = self._make_mock_client("test-sid-verify")

        with mock.patch("httpx.Client", return_value=mock_client):
            result = step_verify(backend_url, "test-sid-verify")
            assert result["session_id"] == "test-sid-verify"
            assert result["status"] == "received"

    def test_run_smoke_full_mocked(self, backend_url: str, capsys):
        """Full pipeline with mocked HTTP — should print BUYER_READY."""
        from bin.recorder_local_smoke import run_smoke

        mock_client = self._make_mock_client("mocked-session-id")

        with mock.patch("httpx.Client", return_value=mock_client):
            rc = run_smoke(backend_url)

        assert rc == 0
        captured = capsys.readouterr()
        assert "BUYER_READY" in captured.out

    def test_run_smoke_upload_failure(self, backend_url: str, capsys):
        """Upload step fails — should print FAIL: upload."""
        from bin.recorder_local_smoke import run_smoke

        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.post.side_effect = Exception("Connection refused")

        with mock.patch("httpx.Client", return_value=mock_client):
            rc = run_smoke(backend_url)

        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: upload" in captured.out

    def test_run_smoke_verify_failure(self, backend_url: str, capsys):
        """Verify step fails — should print FAIL: verify."""
        from bin.recorder_local_smoke import run_smoke

        mock_response_upload = mock.MagicMock()
        mock_response_upload.status_code = 201
        mock_response_upload.json.return_value = {
            "session_id": "fail-verify-sid",
            "status": "received",
            "video_size_bytes": 1060,
        }
        mock_response_upload.raise_for_status = mock.MagicMock()

        mock_response_verify = mock.MagicMock()
        mock_response_verify.raise_for_status.side_effect = Exception("404 Not Found")

        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.post.return_value = mock_response_upload
        mock_client.get.return_value = mock_response_verify

        with mock.patch("httpx.Client", return_value=mock_client):
            rc = run_smoke(backend_url)

        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: verify" in captured.out

    def test_run_smoke_backend_unavailable(self, capsys):
        """Backend not running — should exit 1 with error."""
        from bin.recorder_local_smoke import run_smoke

        mock_client = mock.MagicMock()
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_client.post.side_effect = Exception("Connection refused")

        with mock.patch("httpx.Client", return_value=mock_client):
            rc = run_smoke("http://localhost:9999")

        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: upload" in captured.out


# ---------------------------------------------------------------------------
# Integration test: real backend stub + smoke orchestrator
# ---------------------------------------------------------------------------


class TestSmokeIntegration:
    """Integration test using the real backend stub (no HTTP mocking)."""

    @pytest.fixture
    def backend_server(self):
        """Start the backend stub on a random port, yield URL, then stop."""
        import socket

        from bin.backend_stub import create_app

        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        app = create_app()
        app._reset_store()

        import threading

        import uvicorn

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Wait for server to be ready
        import time

        import httpx

        for _ in range(30):
            try:
                with httpx.Client(timeout=1.0) as client:
                    resp = client.get(f"http://127.0.0.1:{port}/v1/health")
                    if resp.status_code == 200:
                        break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            pytest.skip("Backend stub did not start in time")

        yield f"http://127.0.0.1:{port}"

        server.should_exit = True
        thread.join(timeout=2)

    def test_full_pipeline_with_real_backend(self, backend_server: str, capsys):
        """Run the full smoke pipeline against a real backend stub."""
        from bin.recorder_local_smoke import run_smoke

        rc = run_smoke(backend_server)
        assert rc == 0
        captured = capsys.readouterr()
        assert "BUYER_READY" in captured.out
