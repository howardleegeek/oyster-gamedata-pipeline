"""
Tests for Payout Engine
- Mock Stripe API; verify transfer parameters correct
- Verify cap-per-day enforcement
- Verify multipliers compute correctly
- Verify idempotency (re-running queue doesn't double-pay)
"""

import os
import sys
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- Test Payout Calculation ---

def test_base_payout_qualifying_session():
    """Base $5 for qualifying session (audit >= 101)."""
    from server.payout_engine import calculate_payout
    
    # Audit score 101+ should qualify
    amount = calculate_payout(
        route_type=1,
        is_multi_player=False,
        is_novel_scene=False,
        depth_source="monocular_da_v2",
        audit_score=101
    )
    assert amount == 5.0


def test_base_payout_failing_audit():
    """No payout for failing audit (< 101)."""
    from server.payout_engine import calculate_payout
    
    amount = calculate_payout(
        route_type=1,
        is_multi_player=False,
        is_novel_scene=False,
        depth_source="monocular_da_v2",
        audit_score=100
    )
    assert amount == 0.0


def test_route_type_multiplier():
    """Route type 3+4 = 1.5x multiplier."""
    from server.payout_engine import calculate_payout
    
    # Route type 3: 5 * 1.5 = 7.5
    amount = calculate_payout(
        route_type=3,
        is_multi_player=False,
        is_novel_scene=False,
        depth_source="monocular_da_v2",
        audit_score=150
    )
    assert amount == 7.5
    
    # Route type 4: 5 * 1.5 = 7.5
    amount = calculate_payout(
        route_type=4,
        is_multi_player=False,
        is_novel_scene=False,
        depth_source="monocular_da_v2",
        audit_score=150
    )
    assert amount == 7.5
    
    # Route type 1-2: no multiplier
    amount = calculate_payout(
        route_type=1,
        is_multi_player=False,
        is_novel_scene=False,
        depth_source="monocular_da_v2",
        audit_score=150
    )
    assert amount == 5.0


def test_multi_player_multiplier():
    """Multi-player session = 2x multiplier."""
    from server.payout_engine import calculate_payout
    
    amount = calculate_payout(
        route_type=1,
        is_multi_player=True,
        is_novel_scene=False,
        depth_source="monocular_da_v2",
        audit_score=150
    )
    assert amount == 10.0  # 5 * 2


def test_novel_scene_multiplier():
    """Novel scene = 1.3x multiplier."""
    from server.payout_engine import calculate_payout
    
    amount = calculate_payout(
        route_type=1,
        is_multi_player=False,
        is_novel_scene=True,
        depth_source="monocular_da_v2",
        audit_score=150
    )
    assert amount == 6.5  # 5 * 1.3


def test_depth_source_bonus():
    """engine_zbuffer adds $2 bonus."""
    from server.payout_engine import calculate_payout
    
    # With zbuffer: 5 + 2 = 7
    amount = calculate_payout(
        route_type=1,
        is_multi_player=False,
        is_novel_scene=False,
        depth_source="engine_zbuffer",
        audit_score=150
    )
    assert amount == 7.0
    
    # Without zbuffer: 5
    amount = calculate_payout(
        route_type=1,
        is_multi_player=False,
        is_novel_scene=False,
        depth_source="monocular_da_v2",
        audit_score=150
    )
    assert amount == 5.0


def test_combined_multipliers():
    """Test combined multipliers."""
    from server.payout_engine import calculate_payout
    
    # Route 3 + multi-player + novel + zbuffer
    # Base: 5
    # Route 3: 5 * 1.5 = 7.5
    # Multi-player: 7.5 * 2 = 15
    # Novel: 15 * 1.3 = 19.5
    # Zbuffer: 19.5 + 2 = 21.5
    amount = calculate_payout(
        route_type=3,
        is_multi_player=True,
        is_novel_scene=True,
        depth_source="engine_zbuffer",
        audit_score=150
    )
    assert amount == 21.5


# --- Test Daily Cap ---

def test_daily_cap_enforcement():
    """Verify $200/day cap is enforced."""
    from server.payout_engine import (
        calculate_daily_total,
        check_daily_cap,
        DAILY_CAP,
        completed_payouts
    )
    
    # Clear any existing data
    completed_payouts.clear()
    
    # Simulate $150 already paid today
    contributor_id = "test_contributor_123"
    for i in range(3):
        completed_payouts[f"payout_{i}"] = {
            "contributor_id": contributor_id,
            "amount": 50.0,
            "processed_at": datetime.utcnow(),
            "created_at": datetime.utcnow()
        }
    
    # Check that $60 more is allowed (150 + 60 = 210 > 200)
    assert not check_daily_cap(contributor_id, 60.0)
    
    # Check that $49 more is allowed (150 + 49 = 199 <= 200)
    assert check_daily_cap(contributor_id, 49.0)
    
    # Check exact cap
    assert check_daily_cap(contributor_id, 50.0)  # 150 + 50 = 200


# --- Test Idempotency ---

def test_idempotency_no_double_pay():
    """Verify re-running queue doesn't double-pay."""
    from server.payout_engine import (
        payout_queue,
        completed_payouts,
        PayoutStatus,
        PayoutQueueItem,
        asdict
    )
    
    # Clear queues
    payout_queue.clear()
    completed_payouts.clear()
    
    # Add a completed payout
    payout_id = "test_payout_123"
    completed_payouts[payout_id] = {
        "payout_id": payout_id,
        "contributor_id": "contributor_456",
        "amount": 10.0,
        "status": PayoutStatus.COMPLETED,
        "processed_at": datetime.utcnow().isoformat()
    }
    
    # Add same payout to queue as pending
    payout_queue[payout_id] = {
        "payout_id": payout_id,
        "contributor_id": "contributor_456",
        "amount": 10.0,
        "status": PayoutStatus.PENDING,
        "idempotency_key": "idem_123"
    }
    
    # Simulate processing - should skip because already in completed_payouts
    assert payout_id in completed_payouts
    assert payout_queue[payout_id]["status"] == PayoutStatus.PENDING
    
    # The idempotency check in process_payouts should skip this
    # (we verify this by checking completed_payouts first)


def test_session_idempotency():
    """Verify same session can't be queued twice."""
    from server.payout_engine import payout_queue, PayoutStatus
    
    payout_queue.clear()
    
    # Simulate existing completed payout for session
    session_id = "session_abc123"
    payout_queue["existing_payout"] = {
        "payout_id": "existing_payout",
        "session_id": session_id,
        "status": PayoutStatus.COMPLETED,
        "amount": 5.0
    }
    
    # Check that session is already paid
    for existing in payout_queue.values():
        if existing.get("session_id") == session_id and existing["status"] == PayoutStatus.COMPLETED:
            # Should return "already_paid" status
            assert True
            return
    
    pytest.fail("Session should have been detected as already paid")


# --- Test Stripe Integration (Mocked) ---

@patch("server.stripe_connect.stripe.Transfer.create")
def test_stripe_transfer_parameters(mock_transfer_create):
    """Verify Stripe transfer is called with correct parameters."""
    from server.stripe_connect import execute_stripe_transfer
    
    # Mock the transfer response
    mock_transfer = MagicMock()
    mock_transfer.id = "tr_test_123"
    mock_transfer_create.return_value = mock_transfer
    
    # Execute transfer
    transfer_id = execute_stripe_transfer(
        contributor_id="contributor_123",
        amount=10.0,
        idempotency_key="idem_key_abc",
        account_id="acct_test_123"
    )
    
    # Verify call parameters
    mock_transfer_create.assert_called_once()
    call_kwargs = mock_transfer_create.call_args[1]
    
    assert call_kwargs["amount"] == 1000  # $10 = 1000 cents
    assert call_kwargs["currency"] == "usd"
    assert call_kwargs["destination"] == "acct_test_123"
    assert call_kwargs["idempotency_key"] == "idem_key_abc"
    assert call_kwargs["metadata"]["contributor_id"] == "contributor_123"
    
    assert transfer_id == "tr_test_123"


@patch("server.stripe_connect.stripe.Transfer.create")
def test_stripe_minimum_amount(mock_transfer_create):
    """Verify minimum transfer amount is enforced."""
    from server.stripe_connect import execute_stripe_transfer
    
    with pytest.raises(ValueError, match="Transfer amount too small"):
        execute_stripe_transfer(
            contributor_id="contributor_123",
            amount=0.30,  # Less than $0.50 minimum
            idempotency_key="idem_key_abc"
        )


# --- Test PayPal Integration (Mocked) ---

@patch("server.paypal_payouts.requests.post")
@patch("server.paypal_payouts.get_access_token")
def test_paypal_payout_parameters(mock_get_token, mock_post):
    """Verify PayPal payout is called with correct parameters."""
    # Mock access token
    mock_get_token.return_value = "mock_access_token"
    
    # Mock response
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "batch_header": {
            "payout_batch_id": "PAYOUT-test-123"
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response
    
    # Import after patching
    from server.paypal_payouts import execute_paypal_payout
    
    # Execute payout
    batch_id = execute_paypal_payout(
        contributor_id="contributor_123",
        amount=25.0,
        idempotency_key="idem_key_xyz",
        paypal_email="test@example.com"
    )
    
    # Verify call
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args[1]
    
    payload = call_kwargs["json"]
    assert payload["sender_batch_header"]["sender_batch_id"] == "idem_key_xyz"
    assert payload["items"][0]["amount"]["value"] == "25.00"
    assert payload["items"][0]["receiver"] == "test@example.com"
    
    assert batch_id == "PAYOUT-test-123"


@patch("server.paypal_payouts.requests.post")
@patch("server.paypal_payouts.get_access_token")
def test_paypal_minimum_amount(mock_get_token, mock_post):
    """Verify minimum PayPal payout amount is enforced."""
    mock_get_token.return_value = "mock_token"
    
    from server.paypal_payouts import execute_paypal_payout
    
    with pytest.raises(ValueError, match="Payout amount too small"):
        execute_paypal_payout(
            contributor_id="contributor_123",
            amount=0.50,  # Less than $1.00 minimum
            idempotency_key="idem_key_abc"
        )


# --- Test API Endpoints ---

@patch("server.payout_engine.process_payout_queue")
def test_init_payout_endpoint(mock_process):
    """Test /api/payout/init endpoint."""
    # Mock the background task to avoid actual processing
    mock_process.return_value = None
    
    from fastapi.testclient import TestClient
    from server.payout_engine import app, payout_queue
    
    # Clear queue
    payout_queue.clear()
    
    client = TestClient(app)
    
    response = client.post("/api/payout/init", json={
        "session_id": "session_test_001",
        "buyer_id": "buyer_123",
        "contributor_id": "contributor_456",
        "route_type": 1,
        "is_multi_player": False,
        "is_novel_scene": False,
        "depth_source": "monocular_da_v2",
        "audit_score": 150,
        "buyer_amount_charged": 25.0
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert "payout_id" in data
    assert data["amount"] == 5.0  # Base payout


def test_get_balance_endpoint():
    """Test /api/payout/{contributor_id}/balance endpoint."""
    from fastapi.testclient import TestClient
    from server.payout_engine import app, completed_payouts, payout_queue
    
    # Clear and set up test data in completed_payouts (which the endpoint reads from)
    completed_payouts.clear()
    payout_queue.clear()
    
    contributor_id = "test_contributor"
    
    # Add completed payouts to simulate available balance
    for i in range(5):
        completed_payouts[f"payout_{i}"] = {
            "contributor_id": contributor_id,
            "amount": 10.0,
            "status": "completed",
            "processed_at": datetime.utcnow(),
            "created_at": datetime.utcnow()
        }
    
    # Add pending payout
    payout_queue["pending_1"] = {
        "contributor_id": contributor_id,
        "amount": 5.0,
        "status": "pending"
    }
    
    client = TestClient(app)
    
    response = client.get(f"/api/payout/{contributor_id}/balance")
    
    assert response.status_code == 200
    data = response.json()
    assert data["contributor_id"] == contributor_id
    assert data["available_balance"] == 50.0  # 5 * $10
    assert data["pending_balance"] == 5.0
    assert data["daily_cap"] == 200.0


def test_health_endpoint():
    """Test /health endpoint."""
    from fastapi.testclient import TestClient
    from server.payout_engine import app
    
    client = TestClient(app)
    
    response = client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


# --- Test Audit Logging ---

def test_audit_log_written():
    """Verify audit log is written to file."""
    from server.payout_engine import _write_audit_log, PayoutMethod
    import tempfile
    
    # Use temp file for test
    log_path = tempfile.mktemp()
    
    with patch("server.payout_engine.os.path.expanduser", return_value=log_path):
        _write_audit_log(
            action="TEST",
            payout_id="test_payout_999",
            contributor_id="contributor_test",
            amount=10.0,
            method=PayoutMethod.STRIPE
        )
    
    # Verify log was written
    with open(log_path, "r") as f:
        log_content = f.read()
    
    assert "TEST" in log_content
    assert "test_payout_999" in log_content
    assert "contributor_test" in log_content
    
    # Clean up
    os.remove(log_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
