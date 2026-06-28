"""Tests for scripts/verify_deployed_backend.py using mocked httpx."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from scripts.verify_deployed_backend import (
    SmokeReport,
    check_admin_state,
    check_appcast,
    check_appcast_with_retry,
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

    def test_success_with_expected_mode_and_real_providers(self):
        resp = _make_mock_response(
            200,
            {
                "status": "ok",
                "mode": "production",
                "providers": {"oauth": "google", "storage": "r2", "payout": "stripe"},
            },
        )
        client = _make_mock_client([resp])
        result = check_healthz(
            client,
            verbose=False,
            expected_backend_mode="production",
            require_real_providers=True,
        )
        assert result.passed is True

    def test_expected_mode_mismatch_fails(self):
        resp = _make_mock_response(200, {"status": "ok", "mode": "internal"})
        client = _make_mock_client([resp])
        result = check_healthz(client, verbose=False, expected_backend_mode="production")
        assert result.passed is False
        assert "expected backend mode production" in result.detail

    def test_real_provider_requirement_rejects_stub_providers(self):
        resp = _make_mock_response(
            200,
            {
                "status": "ok",
                "mode": "internal",
                "providers": {"oauth": "mock", "storage": "local", "payout": "simulator"},
            },
        )
        client = _make_mock_client([resp])
        result = check_healthz(client, verbose=False, require_real_providers=True)
        assert result.passed is False
        assert "stub providers" in result.detail
        assert "oauth=mock" in result.detail

    def test_real_provider_requirement_fails_without_provider_object(self):
        resp = _make_mock_response(200, {"status": "ok"})
        client = _make_mock_client([resp])
        result = check_healthz(client, verbose=False, require_real_providers=True)
        assert result.passed is False
        assert "missing providers object" in result.detail

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

    def test_uses_real_apply_payload_schema(self):
        resp = _make_mock_response(200, {"tester_id": "abc-123"})
        client = _make_mock_client([resp])
        result = check_testers_apply(client, verbose=False)
        assert result.passed is True

        _, kwargs = client.post.call_args
        assert kwargs["json"] == {
            "email": "smoke@test.com",
            "discord_user": "smoke#0000",
            "why_interested": "deployment smoke test",
        }

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
        '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" '
        'version="2.0"><channel><title>Test</title><item>'
        '<enclosure url="https://github.com/howardleegeek/oyster-gamedata-pipeline/'
        'releases/download/v0.8.11/OysterRecorder-setup-v2.6.0.exe" '
        'sparkle:version="0.8.11" '
        'sparkle:sha256="bb1e3f12bc71fca9089e14fe3c40ca278af76fce042e4328bf2e8ab1d0d451e5" '
        'type="application/octet-stream"/>'
        "</item></channel></rss>"
    )

    def test_success(self):
        resp = _make_mock_response(200, text_body=self.VALID_XML)
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False)
        assert result.passed is True
        assert result.name == "GET /api/v1/updates/appcast.xml"

    def test_expected_recorder_tag_success(self):
        resp = _make_mock_response(200, text_body=self.VALID_XML)
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False, expected_recorder_tag="v0.8.11")
        assert result.passed is True

    def test_expected_recorder_tag_mismatch_fails(self):
        stale_xml = self.VALID_XML.replace("v0.8.11", "v0.8.10").replace(
            'sparkle:version="0.8.11"',
            'sparkle:version="0.8.10"',
        )
        resp = _make_mock_response(200, text_body=stale_xml)
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False, expected_recorder_tag="v0.8.11")
        assert result.passed is False
        assert "expected v0.8.11 release URL" in result.detail

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

    def test_missing_enclosure_fails(self):
        resp = _make_mock_response(
            200,
            text_body='<rss version="2.0"><channel><title>Test</title></channel></rss>',
        )
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False)
        assert result.passed is False
        assert "missing enclosure" in result.detail

    def test_unresolved_metadata_fails(self):
        xml = self.VALID_XML.replace(
            "bb1e3f12bc71fca9089e14fe3c40ca278af76fce042e4328bf2e8ab1d0d451e5",
            "PLACE" + "HOLDER_SHA256",
        )
        resp = _make_mock_response(200, text_body=xml)
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False)
        assert result.passed is False
        assert "unresolved" in result.detail

    def test_bad_release_url_fails(self):
        xml = self.VALID_XML.replace(
            "https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/",
            "https://example.com/",
        )
        resp = _make_mock_response(200, text_body=xml)
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False)
        assert result.passed is False
        assert "unexpected enclosure URL" in result.detail

    def test_bad_sha_fails(self):
        xml = self.VALID_XML.replace(
            "bb1e3f12bc71fca9089e14fe3c40ca278af76fce042e4328bf2e8ab1d0d451e5",
            "deadbeef",
        )
        resp = _make_mock_response(200, text_body=xml)
        client = _make_mock_client([resp])
        result = check_appcast(client, verbose=False)
        assert result.passed is False
        assert "invalid sha256" in result.detail

    def test_exception(self):
        client = MagicMock()
        client.get.side_effect = httpx.ConnectError("dns")
        result = check_appcast(client, verbose=False)
        assert result.passed is False

    def test_retry_allows_release_appcast_sync_race(self):
        stale_xml = self.VALID_XML.replace("v0.8.11", "v0.8.10").replace(
            'sparkle:version="0.8.11"',
            'sparkle:version="0.8.10"',
        )
        stale_resp = _make_mock_response(200, text_body=stale_xml)
        fresh_resp = _make_mock_response(200, text_body=self.VALID_XML)
        client = _make_mock_client([stale_resp, fresh_resp])

        with patch("scripts.verify_deployed_backend.time.sleep") as sleep:
            result = check_appcast_with_retry(
                client,
                verbose=False,
                expected_recorder_tag="v0.8.11",
                retry_seconds=1,
                retry_interval_seconds=0,
            )

        assert result.passed is True
        assert sleep.called


# ---------------------------------------------------------------------------
# check_admin_state
# ---------------------------------------------------------------------------


class TestCheckAdminState:
    VALID_BODY = {
        "status": "ok",
        "counts": {
            "income_days": 1,
            "sessions": 2,
            "sessions_today": 1,
            "uploads": 2,
            "uploads_today": 1,
            "telemetry_events": 3,
            "testers": 4,
            "tester_statuses": {"invited": 1},
        },
        "income_today": {
            "date": "2026-05-22",
            "total_usd": 5.0,
            "sessions_uploaded": 10,
            "currency": "USD",
        },
        "recorder_release": {"tag": "v0.9.1", "version": "0.9.1"},
    }

    def test_success(self):
        resp = _make_mock_response(200, self.VALID_BODY)
        client = _make_mock_client([resp])
        result = check_admin_state(
            client,
            verbose=False,
            admin_token="admin-token-value",
            expected_recorder_tag="v0.9.1",
        )

        assert result.passed is True
        assert result.name == "GET /api/v1/admin/state"
        _, kwargs = client.get.call_args
        assert kwargs["headers"] == {"Authorization": "Bearer admin-token-value"}

    def test_missing_keys_fails(self):
        resp = _make_mock_response(200, {"status": "ok", "counts": {}})
        client = _make_mock_client([resp])
        result = check_admin_state(client, verbose=False, admin_token="admin-token-value")
        assert result.passed is False
        assert "missing keys" in result.detail

    def test_at_sign_marker_fails(self):
        body = self.VALID_BODY | {"operator_email": "ops@example.com"}
        resp = _make_mock_response(200, body)
        client = _make_mock_client([resp])
        result = check_admin_state(client, verbose=False, admin_token="admin-token-value")
        assert result.passed is False
        assert "PII marker" in result.detail

    def test_download_url_marker_fails(self):
        body = self.VALID_BODY | {
            "recorder_release": {
                "tag": "v0.9.1",
                "version": "0.9.1",
                "download_url": "https://github.com/example/installer.exe",
            }
        }
        resp = _make_mock_response(200, body)
        client = _make_mock_client([resp])
        result = check_admin_state(client, verbose=False, admin_token="admin-token-value")
        assert result.passed is False
        assert "PII marker" in result.detail

    def test_expected_recorder_tag_mismatch_fails(self):
        resp = _make_mock_response(200, self.VALID_BODY)
        client = _make_mock_client([resp])
        result = check_admin_state(
            client,
            verbose=False,
            admin_token="admin-token-value",
            expected_recorder_tag="v0.9.2",
        )
        assert result.passed is False
        assert "expected recorder tag v0.9.2" in result.detail

    def test_exception_masks_admin_token(self):
        client = MagicMock()
        client.get.side_effect = RuntimeError("failed with admin-token-value")
        result = check_admin_state(client, verbose=False, admin_token="admin-token-value")
        assert result.passed is False
        assert "<redacted>" in result.detail
        assert "admin-token-value" not in result.detail


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
        assert mock_client.get.call_count == 3
        assert mock_client.post.call_count == 1

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

    def test_admin_state_passes_when_token_env_is_set(self, monkeypatch):
        monkeypatch.setenv("SMOKE_ADMIN_TOKEN", "admin-token-value")
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
            _make_mock_response(
                200,
                text_body=TestCheckAppcast.VALID_XML.replace("v0.8.11", "v0.9.1").replace(
                    "0.8.11", "0.9.1"
                ),
            ),
            _make_mock_response(200, TestCheckAdminState.VALID_BODY),
        ]
        mock_client = _make_mock_client(responses)

        with patch("scripts.verify_deployed_backend.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            code = run(
                "https://example.com",
                verbose=False,
                expected_recorder_tag="v0.9.1",
                admin_token_env="SMOKE_ADMIN_TOKEN",
            )

        assert code == 0
        assert mock_client.get.call_args_list[-1].args == ("/api/v1/admin/state",)
        assert mock_client.get.call_args_list[-1].kwargs["headers"] == {
            "Authorization": "Bearer admin-token-value"
        }

    def test_admin_state_fails_closed_when_token_env_missing(self, monkeypatch):
        monkeypatch.delenv("SMOKE_ADMIN_TOKEN", raising=False)
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
            code = run(
                "https://example.com",
                verbose=False,
                admin_token_env="SMOKE_ADMIN_TOKEN",
            )

        assert code == 1
        assert mock_client.get.call_count == 3

    def test_run_retries_stale_appcast_once(self):
        stale_xml = TestCheckAppcast.VALID_XML.replace("v0.8.11", "v0.8.10").replace(
            'sparkle:version="0.8.11"',
            'sparkle:version="0.8.10"',
        )
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
            _make_mock_response(200, text_body=stale_xml),
            _make_mock_response(200, text_body=TestCheckAppcast.VALID_XML),
        ]
        mock_client = _make_mock_client(responses)

        with (
            patch("scripts.verify_deployed_backend.httpx.Client") as MockClient,
            patch("scripts.verify_deployed_backend.time.sleep"),
        ):
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            code = run(
                "https://example.com",
                verbose=False,
                expected_recorder_tag="v0.8.11",
                appcast_retry_seconds=1,
                appcast_retry_interval=0,
            )

        assert code == 0
        assert mock_client.get.call_count == 4


class TestBackendRemoteSmokeWorkflow:
    def test_workflow_exists_and_runs_verify_script(self):
        workflow = ROOT / ".github" / "workflows" / "backend-remote-smoke.yml"
        text = workflow.read_text()

        assert "workflow_dispatch:" in text
        assert "schedule:" in text
        assert "BACKEND_SMOKE_URL" in text
        assert "scripts/verify_deployed_backend.py" in text
        assert '--url "$BACKEND_URL"' in text
        assert "expected_backend_mode" in text
        assert "--expected-backend-mode" in text
        assert "require_real_providers" in text
        assert "--require-real-providers" in text
        assert "--appcast-retry-seconds 120" in text

    def test_scheduled_run_skips_without_backend_url_variable(self):
        workflow = ROOT / ".github" / "workflows" / "backend-remote-smoke.yml"
        text = workflow.read_text()

        assert "github.event_name == 'workflow_dispatch'" in text
        assert "vars.BACKEND_SMOKE_URL != ''" in text


class TestBackendFlyDeployWorkflow:
    def test_manual_deploy_workflow_exists_and_verifies_after_deploy(self):
        workflow = ROOT / ".github" / "workflows" / "deploy-backend-fly.yml"
        text = workflow.read_text()

        assert "workflow_dispatch:" in text
        assert "FLY_API_TOKEN" in text
        assert "superfly/flyctl-actions/setup-flyctl" in text
        assert "flyctl deploy backend_stub" in text
        assert "--remote-only" in text
        assert "scripts/verify_deployed_backend.py" in text
        assert '--url "$BACKEND_URL"' in text

    def test_manual_deploy_workflow_does_not_autodeploy_without_intent(self):
        workflow = ROOT / ".github" / "workflows" / "deploy-backend-fly.yml"
        text = workflow.read_text()

        assert "workflow_dispatch:" in text
        assert "\npush:" not in text
        assert "\nschedule:" not in text
        assert "timeout-minutes: 15" in text
        assert "contents: read" in text


def test_normalise_release_tag_handles_both_consumer_schemes() -> None:
    from scripts.verify_deployed_backend import _normalise_release_tag

    # R05E single-file line must pass through untouched — blindly prefixing
    # "v" produced "vrecorder-v2.6.15" and failed the live appcast check.
    assert _normalise_release_tag("recorder-v2.6.15") == "recorder-v2.6.15"
    assert _normalise_release_tag("v0.16.0") == "v0.16.0"
    assert _normalise_release_tag("2.6.15") == "v2.6.15"


def test_version_from_tag_strips_both_scheme_prefixes() -> None:
    from scripts.verify_deployed_backend import _version_from_tag

    assert _version_from_tag("recorder-v2.6.15") == "2.6.15"
    assert _version_from_tag("v0.16.0") == "0.16.0"
    assert _version_from_tag("2.6.15") == "2.6.15"
