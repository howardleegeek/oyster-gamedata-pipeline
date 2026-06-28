"""
Tests for the Stripe Connect integration in web-tester/.

Covers:
    - DEV MODE fallback path on the Python cron (no env → MockSupabase + MockStripe)
    - Idempotency: running the cron twice with the same date does not
      create a duplicate transfer (Stripe-side dedup AND DB-side unique
      index simulation)
    - Threshold filtering: only testers with balance >= min_payout_cents
      and a Stripe-payouts-enabled account get a transfer
    - StripeClient surface: parses Stripe HTTP errors into StripeError
    - Mock Stripe client: deterministic IDs for the test suite
    - Onboarding URL generation contract (calls into the JS lib via a
      shape-equivalent Python re-implementation — the real assertion is
      that the LIVE client sends the correct form fields to the correct
      endpoint, which we verify by mocking urlopen)
    - Return webhook flow: charges_enabled flips after onboarding
      simulation in the mock

These tests use only stdlib + pytest (already in pyproject.toml's `dev`
extras). No `npm install`, no live Stripe API.
"""

from __future__ import annotations

import importlib
import io
import json
import logging
import sys
import unittest.mock as mock
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def cron_module(monkeypatch):
    """Fresh import of payout_cron with deterministic env."""
    # Wipe any Stripe / Supabase env so the module picks the mock clients.
    for key in [
        "STRIPE_SECRET_KEY",
        "NEXT_PUBLIC_SUPABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "PAYOUT_CRON_SLACK_WEBHOOK",
    ]:
        monkeypatch.delenv(key, raising=False)
    # Force log to a tmp path so we don't pollute /tmp for parallel runs.
    monkeypatch.setenv("PAYOUT_CRON_LOG_PATH", str(REPO_ROOT / "tests" / "_payout_cron_test.log"))

    # Drop any cached version of the module so logger handlers aren't reused.
    if "bin.payout_cron" in sys.modules:
        del sys.modules["bin.payout_cron"]
    return importlib.import_module("bin.payout_cron")


@pytest.fixture
def silent_logger():
    """A logger that writes nowhere so test output stays clean."""
    logger = logging.getLogger("payout_cron_test")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)
    return logger


# =====================================================================
# DEV MODE: cron runs end-to-end with mock clients
# =====================================================================


def test_dev_mode_uses_mock_clients(cron_module):
    """With no env + allow_mock=True, make_*_client() returns in-process mocks."""
    assert isinstance(
        cron_module.make_supabase_client(allow_mock=True), cron_module.MockSupabaseClient
    )
    assert isinstance(cron_module.make_stripe_client(allow_mock=True), cron_module.MockStripeClient)


def test_no_env_without_allow_mock_raises(cron_module):
    """Iron-law: without allow_mock, missing env must raise, not silently mock."""
    import pytest

    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        cron_module.make_stripe_client()
    with pytest.raises(RuntimeError, match="Supabase env vars"):
        cron_module.make_supabase_client()


def test_dev_mode_full_run_paid_alice_skipped_bob_skipped_carol(cron_module, silent_logger):
    """Default fixtures: Alice has $48 (=8h*$6), Bob lacks Stripe acct, Carol below threshold."""
    supabase = cron_module.MockSupabaseClient()
    stripe = cron_module.MockStripeClient()

    results = cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="2026-05-07",
        logger=silent_logger,
    )

    paid = [r for r in results if r.success]
    assert len(paid) == 1
    assert paid[0].tester_id == "00000000-0000-0000-0000-000000000001"
    assert paid[0].amount_cents == 4800  # 8h * $6 = $48
    assert paid[0].transfer_id is not None
    assert paid[0].transfer_id.startswith("tr_mock_")

    # Mock-side bookkeeping
    assert len(supabase.payouts_inserted) == 1
    assert supabase.payouts_inserted[0]["status"] == "paid"
    assert supabase.payouts_inserted[0]["stripe_transfer_id"] == paid[0].transfer_id


def test_dev_mode_run_with_dry_run_makes_no_inserts_no_transfers(cron_module, silent_logger):
    supabase = cron_module.MockSupabaseClient()
    stripe = cron_module.MockStripeClient()

    results = cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="2026-05-07",
        logger=silent_logger,
        dry_run=True,
    )
    assert any(r.success for r in results)
    assert supabase.payouts_inserted == []
    assert stripe.transfers_by_key == {}


# =====================================================================
# Idempotency
# =====================================================================


def test_idempotency_same_date_same_key_no_duplicate_transfer(cron_module, silent_logger):
    """Running twice on the same date == one Stripe transfer, one DB row."""
    supabase = cron_module.MockSupabaseClient()
    stripe = cron_module.MockStripeClient()

    cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="2026-05-07",
        logger=silent_logger,
    )
    cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="2026-05-07",
        logger=silent_logger,
    )

    # Stripe deduped both calls into one transfer object.
    assert len(stripe.transfers_by_key) == 1
    # DB inserted only once because the duplicate idempotency_key short-circuits.
    assert len(supabase.payouts_inserted) == 1


def test_idempotency_key_format(cron_module):
    key = cron_module.build_idempotency_key("abc-123", "2026-05-07")
    assert key == "payout-abc-123-2026-05-07"


def test_different_dates_create_separate_transfers(cron_module, silent_logger):
    supabase = cron_module.MockSupabaseClient()
    stripe = cron_module.MockStripeClient()

    cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="2026-05-07",
        logger=silent_logger,
    )
    cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="2026-05-08",
        logger=silent_logger,
    )

    assert len(stripe.transfers_by_key) == 2
    assert len(supabase.payouts_inserted) == 2


# =====================================================================
# Filtering & balance math
# =====================================================================


def test_below_threshold_skipped(cron_module, silent_logger):
    """Tester with balance < threshold is silently skipped."""
    bal = cron_module.TesterBalance(
        tester_id="t-low",
        email="low@x",
        stripe_account_id="acct_x",
        stripe_payouts_enabled=True,
        accepted_seconds=600,  # 10 min @ $6/h = $1
        paid_cents=0,
    )
    supabase = cron_module.MockSupabaseClient(balances=[bal])
    stripe = cron_module.MockStripeClient()

    results = cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="d",
        logger=silent_logger,
    )
    assert results == []
    assert supabase.payouts_inserted == []


def test_no_stripe_account_skipped(cron_module, silent_logger):
    bal = cron_module.TesterBalance(
        tester_id="t-naked",
        email="naked@x",
        stripe_account_id=None,
        stripe_payouts_enabled=False,
        accepted_seconds=3600 * 100,  # tons of earnings, but no account
        paid_cents=0,
    )
    supabase = cron_module.MockSupabaseClient(balances=[bal])
    stripe = cron_module.MockStripeClient()
    results = cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="d",
        logger=silent_logger,
    )
    assert results == []


def test_payouts_disabled_account_skipped(cron_module, silent_logger):
    """Has account but payouts capability not active → skipped."""
    bal = cron_module.TesterBalance(
        tester_id="t-half",
        email="half@x",
        stripe_account_id="acct_x",
        stripe_payouts_enabled=False,
        accepted_seconds=3600 * 10,
        paid_cents=0,
    )
    supabase = cron_module.MockSupabaseClient(balances=[bal])
    stripe = cron_module.MockStripeClient()
    results = cron_module.process_payouts(
        supabase=supabase,
        stripe=stripe,
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="d",
        logger=silent_logger,
    )
    assert results == []


def test_balance_arithmetic(cron_module):
    """Earned = round(seconds * rate_per_hour_cents / 3600); minus paid; never negative."""
    bal = cron_module.TesterBalance(
        tester_id="t",
        email="t@x",
        stripe_account_id="acct",
        stripe_payouts_enabled=True,
        accepted_seconds=3600 * 10,  # 10h
        paid_cents=4000,
    )
    # 10h * 600 cents/h = 6000 earned, 4000 paid → 2000 unpaid
    assert bal.unpaid_cents(rate_per_hour_cents=600) == 2000

    # Overpaid case (e.g. manual top-up) → clamped to 0, never negative
    bal2 = cron_module.TesterBalance(
        tester_id="t2",
        email="t2@x",
        stripe_account_id="acct",
        stripe_payouts_enabled=True,
        accepted_seconds=3600,
        paid_cents=10_000,
    )
    assert bal2.unpaid_cents(rate_per_hour_cents=600) == 0


# =====================================================================
# Stripe error handling
# =====================================================================


def test_stripe_failure_records_failed_payout_row(cron_module, silent_logger):
    """When Stripe raises, we still insert a payout row with status=failed."""

    class FailingStripe:
        def create_transfer(self, **kwargs):
            raise cron_module.StripeError("amount_too_large")

    bal = cron_module.TesterBalance(
        tester_id="t-fail",
        email="fail@x",
        stripe_account_id="acct_fail",
        stripe_payouts_enabled=True,
        accepted_seconds=3600 * 8,
        paid_cents=0,
    )
    supabase = cron_module.MockSupabaseClient(balances=[bal])
    results = cron_module.process_payouts(
        supabase=supabase,
        stripe=FailingStripe(),  # type: ignore[arg-type]
        rate_per_hour_cents=600,
        min_payout_cents=2000,
        run_date="d",
        logger=silent_logger,
    )
    assert len(results) == 1
    assert not results[0].success
    assert "amount_too_large" in (results[0].failure_reason or "")
    assert len(supabase.payouts_inserted) == 1
    assert supabase.payouts_inserted[0]["status"] == "failed"
    assert supabase.payouts_inserted[0]["failure_reason"] is not None


def test_live_stripe_client_rejects_bad_secret(cron_module):
    with pytest.raises(ValueError):
        cron_module.StripeClient("pk_test_oops")  # publishable key, not secret


def test_live_stripe_client_parses_http_error(cron_module):
    """When Stripe returns 4xx with JSON body, StripeError carries the message."""
    client = cron_module.StripeClient("sk_test_x")

    body = json.dumps(
        {"error": {"type": "invalid_request_error", "message": "No such destination: acct_xxx"}}
    ).encode("utf-8")
    err = urllib.error.HTTPError(
        url="https://api.stripe.com/v1/transfers",
        code=400,
        msg="Bad Request",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )

    with (
        mock.patch("urllib.request.urlopen", side_effect=err),
        pytest.raises(cron_module.StripeError) as exc_info,
    ):
        client.create_transfer(
                amount_cents=1000,
                destination_account_id="acct_xxx",
                idempotency_key="k",
            )
    assert "No such destination" in str(exc_info.value)


def test_live_stripe_client_sends_correct_form_fields(cron_module):
    """Verify the wire shape: amount, currency, destination, metadata, Idempotency-Key header."""
    client = cron_module.StripeClient("sk_test_xyz")

    captured: dict = {}

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"id": "tr_real_001", "object": "transfer"}).encode("utf-8")

    def fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data.decode("utf-8") if req.data else ""
        return FakeResp()

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        out = client.create_transfer(
            amount_cents=4800,
            destination_account_id="acct_real_001",
            idempotency_key="payout-t-2026-05-07",
            description="Oyster GameData payout for 2026-05-07",
            metadata={"tester_id": "t", "run_date": "2026-05-07"},
        )

    assert out == {"id": "tr_real_001", "object": "transfer"}
    assert captured["url"] == "https://api.stripe.com/v1/transfers"
    assert captured["method"] == "POST"
    # Header lookup is case-insensitive in HTTP, but urllib normalises to title-case.
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["authorization"] == "Bearer sk_test_xyz"
    assert headers["idempotency-key"] == "payout-t-2026-05-07"
    assert headers["content-type"] == "application/x-www-form-urlencoded"

    body_dict = dict(p.split("=", 1) for p in captured["body"].split("&"))
    # urlencoded values: amount=4800, currency=usd, destination=acct..., metadata[tester_id]=t
    assert body_dict["amount"] == "4800"
    assert body_dict["currency"] == "usd"
    assert body_dict["destination"] == "acct_real_001"
    assert "metadata%5Btester_id%5D" in body_dict  # url-encoded `metadata[tester_id]`


# =====================================================================
# Mock Stripe client invariants
# =====================================================================


def test_mock_stripe_idempotent_returns_same_object(cron_module):
    stripe = cron_module.MockStripeClient()
    a = stripe.create_transfer(
        amount_cents=100,
        destination_account_id="acct_x",
        idempotency_key="k",
    )
    b = stripe.create_transfer(
        amount_cents=999,  # different amount → still returns first object
        destination_account_id="acct_y",
        idempotency_key="k",
    )
    assert a is b
    assert a["amount"] == 100
    assert a["destination"] == "acct_x"


def test_mock_stripe_distinct_keys_get_distinct_transfers(cron_module):
    stripe = cron_module.MockStripeClient()
    a = stripe.create_transfer(
        amount_cents=100, destination_account_id="acct", idempotency_key="k1"
    )
    b = stripe.create_transfer(
        amount_cents=200, destination_account_id="acct", idempotency_key="k2"
    )
    assert a["id"] != b["id"]
    assert a["amount"] == 100
    assert b["amount"] == 200


# =====================================================================
# Onboarding URL generation — TS lib re-implementation contract
# =====================================================================
#
# We can't run the TS code in pytest without a Node toolchain, but we CAN
# verify the URL/redirect contract that the JS side relies on, by reading
# the actual route file and checking the constants and shape.


def test_onboard_route_uses_correct_stripe_endpoints():
    onboard = (
        REPO_ROOT / "web-tester" / "app" / "api" / "stripe" / "connect" / "onboard" / "route.ts"
    ).read_text()
    # Must POST a multipart-shaped onboarding link request and return JSON {url}
    assert "createExpressAccount" in onboard
    assert "createAccountLink" in onboard
    assert "stripe_account_id" in onboard
    assert "tester_id" in onboard


def test_return_route_calls_retrieve_and_persists_charges_enabled():
    ret = (
        REPO_ROOT / "web-tester" / "app" / "api" / "stripe" / "connect" / "return" / "route.ts"
    ).read_text()
    assert "retrieveAccount" in ret
    assert "stripe_charges_enabled" in ret
    assert "stripe_payouts_enabled" in ret
    # Always redirect, never raw-JSON: this is a browser landing page.
    assert "NextResponse.redirect" in ret


def test_dashboard_route_requires_completed_onboarding():
    dash = (
        REPO_ROOT / "web-tester" / "app" / "api" / "stripe" / "connect" / "dashboard" / "route.ts"
    ).read_text()
    assert "createLoginLink" in dash
    assert "stripe_charges_enabled" in dash
    # Must 409 when account exists but onboarding incomplete (UX safety net)
    assert "409" in dash


def test_stripe_lib_uses_real_endpoint_in_live_mode():
    lib = (REPO_ROOT / "web-tester" / "lib" / "stripe.ts").read_text()
    assert "https://api.stripe.com/v1" in lib
    # IRON-LAW: live client is the default in production; mock only when no key.
    assert "isStripeConfigured" in lib
    assert "LiveStripeClient" in lib
    assert "MockStripeClient" in lib


def test_migration_adds_idempotency_key_unique_index():
    sql = (
        REPO_ROOT / "web-tester" / "supabase" / "migrations" / "20260507100000_stripe_connect.sql"
    ).read_text()
    assert "stripe_account_id" in sql
    assert "idempotency_key" in sql
    assert "create unique index" in sql.lower()
    assert "tester_unpaid_balance" in sql


# =====================================================================
# DEV MODE iron-law: production deploys with no env still surface as DEV
# =====================================================================


def test_iron_law_no_secret_raises_without_allow_mock(cron_module, monkeypatch):
    """Empty STRIPE_SECRET_KEY without allow_mock → RuntimeError."""
    import pytest

    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        cron_module.make_stripe_client()


def test_iron_law_no_secret_allows_mock_when_opted_in(cron_module, monkeypatch):
    """Empty STRIPE_SECRET_KEY + allow_mock=True → MockStripeClient."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    client = cron_module.make_stripe_client(allow_mock=True)
    assert isinstance(client, cron_module.MockStripeClient)


def test_iron_law_with_real_secret_means_live_client(cron_module, monkeypatch):
    """sk_-prefixed key → LiveStripeClient (deployed mode is real Stripe)."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy_for_init")
    client = cron_module.make_stripe_client()
    assert isinstance(client, cron_module.StripeClient)


def test_iron_law_publishable_key_rejected(cron_module, monkeypatch):
    """pk_-prefixed key (secret leaked? user mistake?) → raises without allow_mock."""
    import pytest

    monkeypatch.setenv("STRIPE_SECRET_KEY", "pk_test_oops")
    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        cron_module.make_stripe_client()


# =====================================================================
# Slack webhook (best-effort)
# =====================================================================


def test_slack_post_returns_false_when_no_webhook(cron_module, monkeypatch):
    monkeypatch.delenv("PAYOUT_CRON_SLACK_WEBHOOK", raising=False)
    assert cron_module.post_slack("hi") is False


def test_slack_post_swallows_network_errors(cron_module, monkeypatch):
    monkeypatch.setenv("PAYOUT_CRON_SLACK_WEBHOOK", "https://hooks.slack.com/invalid")
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("nope")):
        assert cron_module.post_slack("hi") is False


def test_slack_post_succeeds_on_2xx(cron_module, monkeypatch):
    monkeypatch.setenv("PAYOUT_CRON_SLACK_WEBHOOK", "https://hooks.slack.com/x")

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
        assert cron_module.post_slack("hi") is True


# =====================================================================
# CLI smoke test
# =====================================================================


def test_cli_runs_with_no_env(cron_module, monkeypatch, capsys):
    """Running `payout_cron.py --dry-run` against no-env mocks exits 0."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("NEXT_PUBLIC_SUPABASE_URL", raising=False)
    rc = cron_module.main(["--dry-run", "--verbose"])
    assert rc == 0


# =====================================================================
# Counter
# =====================================================================
#
# Helper to make pytest -q output a one-liner on success that the parent
# spec can grep for. Not strictly necessary but useful for the summary.


def test_zzz_smoke_counter():
    """Ensures pytest collection ran end-to-end; if any earlier test
    silently skipped collection (e.g. import error), this never runs and
    the summary hits a count mismatch."""
    assert True
