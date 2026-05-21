"""Tests for scripts/verify_deployed_backend.py using mocked httpx."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from scripts.verify_deployed_backend import (
    SmokeReport,
    check_appcast,
    check_healthz,
    check_income_today,
    check_testers_apply,
    run,
)

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status_code: int = 200,
    json_body: dict | None = None,
    text_body: str = "",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = text_body
    return resp


def _make_mock_client(responses: list[MagicMock]) -> MagicMock:
    """Create a mock httpx.Client whose get/post return responses in order."""
    client = MagicMock()
    idx = [0]

    def _next(*_args, **_kwargs):
        r = responses[idx[0]]
        idx[0] += 1
        return r

    client.get.side_effect = _next
    client.post.side_effect = _next
    return client


# ---------------------------------------------------------------------------
# SmokeReport
# ---------------------------------------------------------------------------


class TestSmokeReport:
    def test_all_passed_empty(self):
        assert SmokeReport().all_passed is True

    def test_all_passed_true(self):
        r = SmokeReport()
        r.add("a", True)
        r.add("b", True)
        assert r.all_passed is True

    def test_all_passed_false(self):
        r = SmokeReport()
        r.add("a", True)
        r.add("b", False, "boom")
        assert r.all_passed is False

    def test_summary_format(self):
        r = SmokeReport()
        r.add("GET /healthz", True)
        r.add("POST /api/v1/testers/apply", False, "status=500")
        s = r.summary()
        assert "[PASS] GET /healthz" in s
        assert "[FAIL] POST /api/v1/testers/apply" in s
        assert "status=500" in s
        assert "1/2 checks passed" in s


# ---------------------------------------------------------------------------
# check_healthz
# ---------------------------------------------------------------------------


class TestCheckHealthz:
    def test_success(self):
        resp = _make_mock_response(200, {"status": "ok"})
        client = _make_mock_client([resp])
        result = check_healthz(client, verbose=False)
        assert result.passed is True
        assert result.name == "GET /healthz"

    def test_wrong_status(self):
        resp = _make_mock_response(500, {"status": "error"})
        client = _make_mock_client([resp])
        result = check_healthz(client, verbose=False)
        assert result.passed is False
        assert "status=500" in result.detail

    def test_wrong_body(self):
        resp = _make_mock_response(200, {"status": "degraded"})
        client = _make_mock_client([resp])
        result = check_healthz(client, verbose=False)
        assert result.passed is False
        assert "unexpected body" in result.detail

    def test_exception(self):
        client = MagicMock()
        client.get.side_effect = ConnectionError("refused")
        result = check_healthz(client, verbose=False)
        assert result.passed is False
        assert "refused" in result.detail


# ---------------------------------------------------------------------------
# check_testers_apply
# ---------------------------------------------------------------------------


class TestCheckTestersApply:
    def test_success(self):
        resp = _make_mock_response(200, {"tester_id": "abc-123"})
        client = _make_mock_client([resp])
        result = check_testers_apply(client, verbose=False)
        assert result.passed is True
        assert result.name == "POST /api/v1/testers/apply"

    def test_missing_tester_id(self):
        resp = _make_mock_response(200, {"email": "x"})
        client = _make_mock_client([resp])
        result = check_testers_apply(client, verbose=False)
        assert result.passed is False
        assert "missing tester_id" in result.detail

    def test_wrong_status(self):
        resp = _make_mock_response(400, {"detail": "bad"})
        client = _make_mock_client([resp])
        result = check_testers_apply(client, verbose=False)
        assert result.passed is False
        assert "status=400" in result.detail

    def test_exception(self):
        client = MagicMock()
        client.post.side_effect = httpx.ConnectError("timeout")
        result = check_testers_apply(client, verbose=False)
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_income_today
# ---------------------------------------------------------------------------


class TestCheckIncomeToday:
    def test_success(self):
        body = {
            "date": "2026-05-20",
            "total_usd": 0.0,
            "sessions_uploaded": 0,
            "currency": "USD",
        }
        resp = _make_mock_response(200, body)
        client = _make_mock_client([resp])
        result = check_income_today(client, verbose=False)
        assert result.passed is True
        assert result.name == "GET /api/v1/income/today"

    def test_missing_keys(self):
        resp = _make_mock_response(200, {"date": "2026-05-20"})
        client = _make_mock_client([resp])
        result = check_income_today(client, verbose=False)
        assert result.passed is False
        assert "missing keys" in result.detail

    def test_wrong_status(self):
        resp = _make_mock_response(401, {"detail": "unauthorized"})
        client = _make_mock_client([resp])
        result = check_income_today(client, verbose=False)
        assert result.passed is False
        assert "status=401" in result.detail

    def test_exception(self):
        client = MagicMock()
        client.get.side_effect = httpx.ReadTimeout("slow")
        result = check_income_today(client, verbose=False)
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_appcast
# ---------------------------------------------------------------------------


class TestCheckAppcast:
    VALID_XML = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel><title>Test</title></channel></rss>'
    )

    def test_success(self):
        resp = _make_mock_response(200, text_body=self.VALID_XML)
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False)
        assert result.passed is True
        assert result.name == "GET /api/v1/updates/appcast.xml"

    def test_invalid_xml(self):
        resp = _make_mock_response(200, text_body="<not xml><broken>")
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False)
        assert result.passed is False
        assert "XML parse error" in result.detail

    def test_wrong_status(self):
        resp = _make_mock_response(404, text_body="not found")
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False)
        assert result.passed is False
        assert "status=404" in result.detail

    def test_exception(self):
        client = MagicMock()
        client.get.side_effect = httpx.ConnectError("dns")
        result = check_appcast(client, verbose=False)
        assert result.passed is False


# ---------------------------------------------------------------------------
# run() integration
# ---------------------------------------------------------------------------


class TestRun:
    def test_all_pass_returns_0(self):
        responses = [
            _make_mock_response(200, {"status": "ok"}),
            _make_mock_response(200, {"tester_id": "t1"}),
            _make_mock_response(
                200,
                {
                    "date": "2026-05-20",
                    "total_usd": 0.0,
                    "sessions_uploaded": 0,
                    "currency": "USD",
                },
            ),
            _make_mock_response(200, text_body=TestCheckAppcast.VALID_XML),
        ]
        mock_client = _make_mock_client(responses)

        with patch("scripts.verify_deployed_backend.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            code = run("https://example.com", verbose=False)

        assert code == 0

    def test_one_fail_returns_1(self):
        responses = [
            _make_mock_response(200, {"status": "ok"}),
            _make_mock_response(500, {"detail": "internal"}),
            _make_mock_response(
                200,
                {
                    "date": "2026-05-20",
                    "total_usd": 0.0,
                    "sessions_uploaded": 0,
                    "currency": "USD",
                },
            ),
            _make_mock_response(200, text_body=TestCheckAppcast.VALID_XML),
        ]
        mock_client = _make_mock_client(responses)

        with patch("scripts.verify_deployed_backend.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            code = run("https://example.com", verbose=False)

        assert code == 1


class TestBackendRemoteSmokeWorkflow:
    def test_workflow_exists_and_runs_verify_script(self):
        workflow = ROOT / ".github" / "workflows" / "backend-remote-smoke.yml"
        text = workflow.read_text()

        assert "workflow_dispatch:" in text
        assert "schedule:" in text
        assert "BACKEND_SMOKE_URL" in text
        assert "scripts/verify_deployed_backend.py" in text
        assert '--url "$BACKEND_URL"' in text

    def test_scheduled_run_skips_without_backend_url_variable(self):
        workflow = ROOT / ".github" / "workflows" / "backend-remote-smoke.yml"
        text = workflow.read_text()

        assert "github.event_name == 'workflow_dispatch'" in text
        assert "vars.BACKEND_SMOKE_URL != ''" in text
