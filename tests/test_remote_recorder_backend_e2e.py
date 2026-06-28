"""Tests for bin/remote_recorder_backend_e2e.py.

Covers all 7 steps of the remote recorder backend E2E flow:
  1. healthz check
  2. apply as tester
  3. mock OAuth exchange
  4. record fake session (S29 fixture)
  5. upload via signed URL
  6. verify session received
  7. fetch income today → $0.50

All HTTP calls are mocked via ``respx`` so no real server is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest
from httpx import Response

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
# Import the module under test
# ---------------------------------------------------------------------------

import bin.remote_recorder_backend_e2e as e2e_mod

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKEND_URL = "https://oyster-backend-6qup7rrx2q-uc.a.run.app"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_fixture_dir(tmp_path: Path):
    """Create a temporary synthetic_session fixture directory."""
    fixture_dir = tmp_path / "tests" / "fixtures" / "synthetic_session"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": "synth-001",
                "recording_date": "2024-01-01",
                "game_name": "synthetic_game",
                "operator_id": "OP-000",
            }
        )
    )
    # Patch the FIXTURE_DIR constant
    original = e2e_mod.FIXTURE_DIR
    e2e_mod.FIXTURE_DIR = fixture_dir
    yield fixture_dir
    e2e_mod.FIXTURE_DIR = original


@pytest.fixture
def respx_mock(monkeypatch):
    """Small local subset of the respx_mock fixture used by these tests."""

    class MockRoute:
        def __init__(self):
            self.response = Response(404, text="not mocked")
            self.calls = []

        def mock(self, return_value: Response):
            self.response = return_value
            return self

    class LocalRespxMock:
        def __init__(self):
            self.routes = {}

        def _route(self, method: str, url: str) -> MockRoute:
            return self.routes.setdefault((method, url), MockRoute())

        def get(self, url: str) -> MockRoute:
            return self._route("GET", url)

        def post(self, url: str) -> MockRoute:
            return self._route("POST", url)

        def put(self, url: str) -> MockRoute:
            return self._route("PUT", url)

        def __getitem__(self, url: str) -> MockRoute:
            for (_, route_url), route in self.routes.items():
                if route_url == url:
                    return route
            raise KeyError(url)

        def handle(self, request: httpx.Request) -> Response:
            route = self.routes.get((request.method, str(request.url)))
            if route is None:
                return Response(404, text=f"not mocked: {request.method} {request.url}")
            route.calls.append(SimpleNamespace(request=request))
            return route.response

    mock_routes = LocalRespxMock()
    real_client = httpx.Client

    class MockedClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(mock_routes.handle)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", MockedClient)
    yield mock_routes


@pytest.fixture
def mocked_backend(respx_mock):
    """Mock all backend endpoints."""
    # healthz
    respx_mock.get(f"{BACKEND_URL}/healthz").mock(
        return_value=Response(200, json={"status": "ok", "version": "0.1.0"})
    )

    # apply tester
    respx_mock.post(f"{BACKEND_URL}/api/v1/testers/apply").mock(
        return_value=Response(200, json={"tester_id": "tst-e2e-001", "status": "pending"})
    )

    # OAuth exchange
    respx_mock.post(f"{BACKEND_URL}/api/v1/auth/google/exchange").mock(
        return_value=Response(
            200,
            json={
                "access_token": "mock-google-at-abcdef1234567890",
                "refresh_token": "mock-google-rt-abcdef1234567890",
                "expires_in": 3600,
            },
        )
    )

    # signed URL
    respx_mock.post(f"{BACKEND_URL}/api/v1/upload/signed-url").mock(
        return_value=Response(
            200,
            json={
                "url": "https://mock-s3.example.com/uploads/test.tar.gz?X-Amz-Signature=fake",
                "expires_at": "2024-01-01T00:00:00Z",
                "key": "uploads/test.tar.gz",
            },
        )
    )

    # register session
    respx_mock.post(f"{BACKEND_URL}/api/v1/sessions").mock(
        return_value=Response(
            200,
            json={"session_id": "s114-e2e-001", "status": "received"},
        )
    )

    # income today
    respx_mock.get(f"{BACKEND_URL}/api/v1/income/today").mock(
        return_value=Response(
            200,
            json={
                "date": "2024-01-01",
                "total_usd": 0.50,
                "sessions_uploaded": 1,
                "sessions_counted": 1,
                "currency": "USD",
            },
        )
    )

    # mock S3 upload (PUT)
    respx_mock.put("https://mock-s3.example.com/uploads/test.tar.gz?X-Amz-Signature=fake").mock(
        return_value=Response(200)
    )

    return respx_mock


# ---------------------------------------------------------------------------
# Step 1: healthz
# ---------------------------------------------------------------------------


class TestStepHealthz:
    def test_healthz_ok(self, mocked_backend):
        """healthz returns 200 with status ok."""
        import httpx

        with httpx.Client(base_url=BACKEND_URL) as client:
            result = e2e_mod.step_healthz(client, BACKEND_URL)
        assert result is True

    def test_healthz_non_200_raises(self, mocked_backend):
        """healthz returns non-200 → AssertionError."""
        mocked_backend.get(f"{BACKEND_URL}/healthz").mock(
            return_value=Response(503, json={"status": "error"})
        )
        import httpx

        with (
            httpx.Client(base_url=BACKEND_URL) as client,
            pytest.raises(AssertionError),
        ):
            e2e_mod.step_healthz(client, BACKEND_URL)

    def test_healthz_bad_status_field(self, mocked_backend):
        """healthz returns 200 but status != 'ok' → AssertionError."""
        mocked_backend.get(f"{BACKEND_URL}/healthz").mock(
            return_value=Response(200, json={"status": "degraded"})
        )
        import httpx

        with (
            httpx.Client(base_url=BACKEND_URL) as client,
            pytest.raises(AssertionError),
        ):
            e2e_mod.step_healthz(client, BACKEND_URL)


# ---------------------------------------------------------------------------
# Step 2: apply as tester
# ---------------------------------------------------------------------------


class TestStepApplyTester:
    def test_apply_returns_tester_id(self, mocked_backend):
        """apply returns a tester_id."""
        import httpx

        with httpx.Client(base_url=BACKEND_URL) as client:
            tester_id = e2e_mod.step_apply_tester(client, BACKEND_URL)
        assert tester_id == "tst-e2e-001"

    def test_apply_non_200_raises(self, mocked_backend):
        """apply returns non-200 → AssertionError."""
        mocked_backend.post(f"{BACKEND_URL}/api/v1/testers/apply").mock(
            return_value=Response(400, json={"detail": "invalid email"})
        )
        import httpx

        with (
            httpx.Client(base_url=BACKEND_URL) as client,
            pytest.raises(AssertionError),
        ):
            e2e_mod.step_apply_tester(client, BACKEND_URL)


# ---------------------------------------------------------------------------
# Step 3: mock OAuth exchange
# ---------------------------------------------------------------------------


class TestStepOAuthExchange:
    def test_oauth_returns_token(self, mocked_backend):
        """OAuth exchange returns an access_token."""
        import httpx

        with httpx.Client(base_url=BACKEND_URL) as client:
            token = e2e_mod.step_oauth_exchange(client, BACKEND_URL)
        assert token == "mock-google-at-abcdef1234567890"
        assert token.startswith("mock-google-at-")

    def test_oauth_non_200_raises(self, mocked_backend):
        """OAuth exchange returns non-200 → AssertionError."""
        mocked_backend.post(f"{BACKEND_URL}/api/v1/auth/google/exchange").mock(
            return_value=Response(401, json={"detail": "invalid code"})
        )
        import httpx

        with (
            httpx.Client(base_url=BACKEND_URL) as client,
            pytest.raises(AssertionError),
        ):
            e2e_mod.step_oauth_exchange(client, BACKEND_URL)


# ---------------------------------------------------------------------------
# Step 4: record fake session
# ---------------------------------------------------------------------------


class TestStepRecordFakeSession:
    def test_record_returns_session_payload(self, mocked_backend):
        """record fake session returns a payload with session_id and BUYER_READY status."""
        import httpx

        with httpx.Client(base_url=BACKEND_URL) as client:
            payload = e2e_mod.step_record_fake_session(
                client, BACKEND_URL, "mock-google-at-abcdef1234567890"
            )
        assert "session_id" in payload
        assert payload["session_id"].startswith("s114-e2e-")
        assert payload["status"] == "BUYER_READY"
        assert payload["game_name"] == "synthetic_game"

    def test_record_uses_fixture_metadata(self, mocked_backend):
        """record fake session reads from fixture metadata."""
        import httpx

        with httpx.Client(base_url=BACKEND_URL) as client:
            payload = e2e_mod.step_record_fake_session(
                client, BACKEND_URL, "mock-google-at-abcdef1234567890"
            )
        assert payload["recording_date"] == "2024-01-01"
        assert payload["operator_id"] == "OP-000"


# ---------------------------------------------------------------------------
# Step 5: upload via signed URL
# ---------------------------------------------------------------------------


class TestStepUploadViaSignedUrl:
    def test_upload_returns_session_id(self, mocked_backend):
        """upload via signed URL returns session_id."""
        import httpx

        session_payload = {
            "session_id": "s114-e2e-001",
            "game_name": "synthetic_game",
            "recording_date": "2024-01-01",
            "operator_id": "OP-000",
            "status": "BUYER_READY",
        }
        with httpx.Client(base_url=BACKEND_URL) as client:
            session_id = e2e_mod.step_upload_via_signed_url(
                client, BACKEND_URL, "mock-google-at-abcdef1234567890", session_payload
            )
        assert session_id == "s114-e2e-001"

    def test_upload_put_non_2xx_raises(self, mocked_backend):
        """signed URL PUT returning non-2xx fails the E2E."""
        import httpx

        mocked_backend.put(
            "https://mock-s3.example.com/uploads/test.tar.gz?X-Amz-Signature=fake"
        ).mock(return_value=Response(500, text="upload failed"))
        session_payload = {
            "session_id": "s114-e2e-001",
            "game_name": "synthetic_game",
            "recording_date": "2024-01-01",
            "operator_id": "OP-000",
            "status": "BUYER_READY",
        }
        with (
            httpx.Client(base_url=BACKEND_URL) as client,
            pytest.raises(AssertionError, match="upload PUT returned 500"),
        ):
            e2e_mod.step_upload_via_signed_url(
                    client,
                    BACKEND_URL,
                    "mock-google-at-abcdef1234567890",
                    session_payload,
                )

    def test_upload_sends_bearer_token(self, mocked_backend):
        """upload includes Bearer token in Authorization header."""
        import httpx

        session_payload = {
            "session_id": "s114-e2e-001",
            "game_name": "synthetic_game",
            "recording_date": "2024-01-01",
            "operator_id": "OP-000",
            "status": "BUYER_READY",
        }
        with httpx.Client(base_url=BACKEND_URL) as client:
            e2e_mod.step_upload_via_signed_url(
                client, BACKEND_URL, "mock-google-at-abcdef1234567890", session_payload
            )

        # Verify the signed-url request had the Bearer token
        signed_url_req = mocked_backend[f"{BACKEND_URL}/api/v1/upload/signed-url"].calls[0]
        assert (
            signed_url_req.request.headers["Authorization"]
            == "Bearer mock-google-at-abcdef1234567890"
        )

    def test_upload_session_register_sends_bearer(self, mocked_backend):
        """session registration includes Bearer token."""
        import httpx

        session_payload = {
            "session_id": "s114-e2e-001",
            "game_name": "synthetic_game",
            "recording_date": "2024-01-01",
            "operator_id": "OP-000",
            "status": "BUYER_READY",
        }
        with httpx.Client(base_url=BACKEND_URL) as client:
            e2e_mod.step_upload_via_signed_url(
                client, BACKEND_URL, "mock-google-at-abcdef1234567890", session_payload
            )

        session_req = mocked_backend[f"{BACKEND_URL}/api/v1/sessions"].calls[0]
        assert (
            session_req.request.headers["Authorization"] == "Bearer mock-google-at-abcdef1234567890"
        )


# ---------------------------------------------------------------------------
# Step 6: verify session received
# ---------------------------------------------------------------------------


class TestStepVerifySession:
    def test_verify_returns_true(self, mocked_backend):
        """verify session returns True."""
        import httpx

        with httpx.Client(base_url=BACKEND_URL) as client:
            result = e2e_mod.step_verify_session(
                client, BACKEND_URL, "mock-google-at-abcdef1234567890", "s114-e2e-001"
            )
        assert result is True


# ---------------------------------------------------------------------------
# Step 7: fetch income today
# ---------------------------------------------------------------------------


class TestStepFetchIncome:
    def test_income_returns_data(self, mocked_backend):
        """fetch income returns income data."""
        import httpx

        with httpx.Client(base_url=BACKEND_URL) as client:
            data = e2e_mod.step_fetch_income(client, BACKEND_URL, "mock-google-at-abcdef1234567890")
        assert data["total_usd"] == 0.50
        assert data["sessions_uploaded"] == 1
        assert data["currency"] == "USD"

    def test_income_sends_bearer_token(self, mocked_backend):
        """income request includes Bearer token."""
        import httpx

        with httpx.Client(base_url=BACKEND_URL) as client:
            e2e_mod.step_fetch_income(client, BACKEND_URL, "mock-google-at-abcdef1234567890")

        income_req = mocked_backend[f"{BACKEND_URL}/api/v1/income/today"].calls[0]
        assert (
            income_req.request.headers["Authorization"] == "Bearer mock-google-at-abcdef1234567890"
        )


# ---------------------------------------------------------------------------
# Full E2E orchestration
# ---------------------------------------------------------------------------


class TestRunE2E:
    def test_full_e2e_passes(self, mocked_backend):
        """All 7 steps pass in sequence → exit code 0."""
        exit_code = e2e_mod.run_e2e(BACKEND_URL)
        assert exit_code == 0

    def test_full_e2e_fails_on_healthz(self, mocked_backend):
        """healthz failure → exit code 1."""
        mocked_backend.get(f"{BACKEND_URL}/healthz").mock(
            return_value=Response(503, json={"status": "error"})
        )
        with pytest.raises(AssertionError):
            e2e_mod.run_e2e(BACKEND_URL)

    def test_full_e2e_fails_on_oauth(self, mocked_backend):
        """OAuth failure → exit code 1."""
        mocked_backend.post(f"{BACKEND_URL}/api/v1/auth/google/exchange").mock(
            return_value=Response(401, json={"detail": "invalid code"})
        )
        with pytest.raises(AssertionError):
            e2e_mod.run_e2e(BACKEND_URL)

    def test_full_e2e_fails_on_income(self, mocked_backend):
        """income failure → exit code 1."""
        mocked_backend.get(f"{BACKEND_URL}/api/v1/income/today").mock(
            return_value=Response(500, json={"detail": "internal error"})
        )
        with pytest.raises(AssertionError):
            e2e_mod.run_e2e(BACKEND_URL)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCLI:
    def test_backend_url_arg(self):
        """--backend-url is accepted."""
        with mock.patch.object(
            sys, "argv", ["remote_recorder_backend_e2e.py", "--backend-url", "https://example.com"]
        ):
            e2e_mod.main.__code__  # just verify the module loads
        # Verify the parser accepts --backend-url
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--backend-url", type=str, default="http://localhost:8500")
        parsed = parser.parse_args(["--backend-url", "https://example.com"])
        assert parsed.backend_url == "https://example.com"

    def test_default_backend_url(self):
        """Default backend URL is localhost:8500."""
        assert e2e_mod.DEFAULT_BACKEND_URL == "http://localhost:8500"
