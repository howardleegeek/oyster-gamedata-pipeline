"""
tests/test_payout_simulator.py — Full test suite for the payout simulator.

Covers:
  - POST /api/v1/payouts/queue  (enqueue, validation, daily limit → 429)
  - GET  /api/v1/payouts/{id}   (status lookup)
  - POST /api/v1/payouts/{id}/simulate  (force paid)
  - Worker thread auto-advancing states
  - --accelerate flag behaviour
  - Black + ruff compliance (lint-only, not a test)
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend_stub.main import create_app, register_routes
from backend_stub.payout import (
    DAILY_LIMIT_USD,
    PayoutStore,
    PayoutWorker,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER_TOKEN = "user-token-1"
ADMIN_TOKEN = "admin-secret-token"


@pytest.fixture()
def store():
    """Fresh in-memory store for each test."""
    return PayoutStore()


@pytest.fixture()
def app(store):
    """FastAPI app with injected store (no worker started)."""
    app = create_app(accelerate=1.0, interval=300.0)
    # Replace the module-level store with our fixture
    import backend_stub.main as main_mod

    main_mod.store = store
    register_routes(app)
    return app


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def app_with_worker(store):
    """FastAPI app with a fast worker (accelerate=3600, interval=0.5s)."""
    app = create_app(accelerate=3600.0, interval=0.5)
    import backend_stub.main as main_mod

    main_mod.store = store
    register_routes(app)
    return app


@pytest.fixture()
def client_with_worker(app_with_worker):
    with TestClient(app_with_worker) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/v1/payouts/queue
# ---------------------------------------------------------------------------


class TestQueuePayout:
    def test_queue_success(self, client):
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 50.0},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "payout_id" in data
        assert data["payout_id"].startswith("po-")
        assert "queued_at" in data
        assert "est_arrival" in data
        assert data["amount_usd"] == 50.0
        assert data["provider"] == "paypal"

    def test_queue_stripe_provider(self, client):
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 25.0, "provider": "stripe"},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json()["provider"] == "stripe"

    def test_queue_missing_amount(self, client):
        resp = client.post(
            "/api/v1/payouts/queue",
            json={},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 400

    def test_queue_negative_amount(self, client):
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": -10},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 400

    def test_queue_zero_amount(self, client):
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 0},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 400

    def test_queue_invalid_provider(self, client):
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 10, "provider": "bitcoin"},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 400

    def test_queue_no_auth(self, client):
        resp = client.post("/api/v1/payouts/queue", json={"amount_usd": 10})
        assert resp.status_code == 401

    def test_queue_invalid_json(self, client):
        resp = client.post(
            "/api/v1/payouts/queue",
            data="not json",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/v1/payouts/{id}
# ---------------------------------------------------------------------------


class TestGetPayout:
    def test_get_queued_payout(self, client):
        # First queue one
        q_resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 100},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        payout_id = q_resp.json()["payout_id"]

        resp = client.get(
            f"/api/v1/payouts/{payout_id}",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == payout_id
        assert data["status"] == "queued"
        assert data["amount_usd"] == 100

    def test_get_not_found(self, client):
        resp = client.get(
            "/api/v1/payouts/po-nonexistent",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 404

    def test_get_no_auth(self, client):
        resp = client.get("/api/v1/payouts/po-something")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/payouts/{id}/simulate
# ---------------------------------------------------------------------------


class TestSimulatePayout:
    def test_simulate_paid(self, client):
        q_resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 75},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        payout_id = q_resp.json()["payout_id"]

        resp = client.post(
            f"/api/v1/payouts/{payout_id}/simulate",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "paid"
        assert "txn_id" in data
        assert data["txn_id"].startswith("mock-txn-")
        assert "paid_at" in data

    def test_simulate_not_found(self, client):
        resp = client.post(
            "/api/v1/payouts/po-nonexistent/simulate",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert resp.status_code == 404

    def test_simulate_no_admin(self, client):
        q_resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 10},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        payout_id = q_resp.json()["payout_id"]

        resp = client.post(
            f"/api/v1/payouts/{payout_id}/simulate",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 403

    def test_simulate_fail(self, client):
        q_resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 30},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        payout_id = q_resp.json()["payout_id"]

        resp = client.post(
            f"/api/v1/payouts/{payout_id}/simulate-fail",
            json={"reason": "insufficient_funds"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["failure_reason"] == "insufficient_funds"


# ---------------------------------------------------------------------------
# Daily limit → 429
# ---------------------------------------------------------------------------


class TestDailyLimit:
    def test_daily_limit_exceeded(self, client):
        # Queue exactly $1000
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": DAILY_LIMIT_USD},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 200

        # Next one should be 429
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 1},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 429
        data = resp.json()
        assert data["error"] == "daily_limit_exceeded"
        assert "retry_after" in data
        assert "Retry-After" in resp.headers

    def test_daily_limit_per_user(self, client):
        # user-001 hits limit
        client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": DAILY_LIMIT_USD},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        resp1 = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 1},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp1.status_code == 429

        # user-002 should still be fine
        resp2 = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 50},
            headers={"Authorization": "Bearer user-token-2"},
        )
        assert resp2.status_code == 200

    def test_daily_limit_partial(self, client):
        # Queue $600
        client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 600},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        # Queue $300 more (total $900)
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 300},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 200

        # $150 more would exceed $1000
        resp = client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 150},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Worker thread auto-advancing states
# ---------------------------------------------------------------------------


class TestWorkerThread:
    def test_worker_advances_queued_to_processing(self, store):
        """With accelerate=3600, 1h threshold = 1 real second."""
        worker = PayoutWorker(store, accelerate=3600.0, interval=0.2)
        worker.start()
        try:
            # Create a payout
            rec = store.create("user-001", 50.0)
            assert rec.status == "queued"

            # Wait for worker to advance it to processing (1h simulated = 1s real)
            # Need >1s to account for worker startup delay and tick interval
            time.sleep(1.3)

            rec = store.get(rec.id)
            assert rec is not None
            assert rec.status == "processing"
        finally:
            worker.stop()

    def test_worker_advances_processing_to_paid(self, store):
        """With accelerate=3600, 30min threshold = 0.5 real seconds."""
        worker = PayoutWorker(store, accelerate=3600.0, interval=0.2)
        worker.start()
        try:
            rec = store.create("user-001", 50.0)
            assert rec.status == "queued"

            # Wait for queued→processing→paid
            # queued→processing: 1s, processing→paid: 0.5s
            time.sleep(2.5)

            rec = store.get(rec.id)
            assert rec is not None
            assert rec.status == "paid"
            assert rec.txn_id is not None
            assert rec.txn_id.startswith("mock-txn-")
        finally:
            worker.stop()

    def test_worker_full_lifecycle_via_client(self, client_with_worker):
        """End-to-end: queue → wait → auto-paid."""
        resp = client_with_worker.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 200},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 200
        payout_id = resp.json()["payout_id"]

        # Wait for full lifecycle
        time.sleep(3)

        resp = client_with_worker.get(
            f"/api/v1/payouts/{payout_id}",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "paid"
        assert "txn_id" in data


# ---------------------------------------------------------------------------
# PayoutStore unit tests
# ---------------------------------------------------------------------------


class TestPayoutStore:
    def test_create_and_get(self, store):
        rec = store.create("user-001", 100.0)
        assert rec.id.startswith("po-")
        assert rec.status == "queued"
        assert rec.amount_usd == 100.0

        fetched = store.get(rec.id)
        assert fetched is not None
        assert fetched.id == rec.id

    def test_get_nonexistent(self, store):
        assert store.get("po-nope") is None

    def test_force_paid(self, store):
        rec = store.create("user-001", 50.0)
        result = store.force_paid(rec.id)
        assert result is not None
        assert result.status == "paid"
        assert result.txn_id is not None

    def test_force_paid_nonexistent(self, store):
        assert store.force_paid("po-nope") is None

    def test_force_failed(self, store):
        rec = store.create("user-001", 50.0)
        result = store.force_failed(rec.id, "bank_error")
        assert result is not None
        assert result.status == "failed"
        assert result.failure_reason == "bank_error"

    def test_daily_spent_tracking(self, store):
        assert store.daily_spent("user-001") == 0.0
        store.record_daily("user-001", 100.0)
        assert store.daily_spent("user-001") == 100.0
        store.record_daily("user-001", 200.0)
        assert store.daily_spent("user-001") == 300.0

    def test_can_payout(self, store):
        assert store.can_payout("user-001", 500.0) is True
        store.record_daily("user-001", 600.0)
        assert store.can_payout("user-001", 500.0) is False  # 600+500 > 1000
        assert store.can_payout("user-001", 400.0) is True  # 600+400 = 1000

    def test_to_dict(self, store):
        rec = store.create("user-001", 75.0)
        d = rec.to_dict()
        assert d["id"] == rec.id
        assert d["status"] == "queued"
        assert d["amount_usd"] == 75.0
        assert "queued_at" in d
        assert "est_arrival" in d

    def test_to_dict_after_paid(self, store):
        rec = store.create("user-001", 75.0)
        store.force_paid(rec.id)
        d = rec.to_dict()
        assert d["status"] == "paid"
        assert "txn_id" in d
        assert "paid_at" in d

    def test_to_dict_after_failed(self, store):
        rec = store.create("user-001", 75.0)
        store.force_failed(rec.id, "test_reason")
        d = rec.to_dict()
        assert d["status"] == "failed"
        assert d["failure_reason"] == "test_reason"
        assert "failed_at" in d

    def test_list_all(self, store):
        store.create("user-001", 10.0)
        store.create("user-002", 20.0)
        assert len(store.list_all()) == 2

    def test_advance_queued(self, store):
        rec = store.create("user-001", 10.0)
        # Manually backdate queued_at
        rec.queued_at = datetime.now(timezone.utc) - timedelta(hours=2)
        count = store.advance_queued(3600)  # 1h threshold
        assert count == 1
        assert store.get(rec.id).status == "processing"

    def test_advance_processing(self, store):
        rec = store.create("user-001", 10.0)
        rec.status = "processing"
        rec.processing_at = datetime.now(timezone.utc) - timedelta(minutes=45)
        count = store.advance_processing(1800)  # 30min threshold
        assert count == 1
        updated = store.get(rec.id)
        assert updated.status == "paid"
        assert updated.txn_id is not None


# ---------------------------------------------------------------------------
# PayoutWorker unit tests
# ---------------------------------------------------------------------------


class TestPayoutWorker:
    def test_effective_thresholds(self, store):
        worker = PayoutWorker(store, accelerate=24.0)
        # 1h / 24 = 150s
        assert worker.effective_queued_threshold == 150.0
        # 30min / 24 = 75s
        assert worker.effective_processing_threshold == 75.0

    def test_effective_thresholds_no_acceleration(self, store):
        worker = PayoutWorker(store, accelerate=1.0)
        assert worker.effective_queued_threshold == 3600.0
        assert worker.effective_processing_threshold == 1800.0

    def test_start_stop(self, store):
        worker = PayoutWorker(store, accelerate=1.0, interval=0.1)
        worker.start()
        assert worker._thread is not None
        assert worker._thread.is_alive()
        worker.stop()
        assert not worker._thread.is_alive()

    def test_double_start_is_safe(self, store):
        worker = PayoutWorker(store, accelerate=1.0, interval=0.1)
        worker.start()
        worker.start()  # should be a no-op
        worker.stop()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# List payouts (admin)
# ---------------------------------------------------------------------------


class TestListPayouts:
    def test_list_admin(self, client):
        client.post(
            "/api/v1/payouts/queue",
            json={"amount_usd": 10},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        resp = client.get(
            "/api/v1/payouts",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_non_admin(self, client):
        resp = client.get(
            "/api/v1/payouts",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_default_accelerate(self):
        from backend_stub.payout import parse_args

        args = parse_args([])
        assert args.accelerate == 1.0

    def test_custom_accelerate(self):
        from backend_stub.payout import parse_args

        args = parse_args(["--accelerate", "24"])
        assert args.accelerate == 24.0

    def test_custom_interval(self):
        from backend_stub.payout import parse_args

        args = parse_args(["--interval", "60"])
        assert args.interval == 60.0
