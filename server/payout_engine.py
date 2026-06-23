"""
Contributor Payout Engine - FastAPI Service
Handles automatic payouts via Stripe Connect (default) or PayPal (fallback).
"""

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

import stripe
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment configuration
STRIPE_LIVE = os.getenv("STRIPE_LIVE", "false").lower() == "true"
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
stripe.api_key = STRIPE_SECRET_KEY

# Constants
DAILY_CAP = 200.0  # $200 per contributor per day (anti-fraud)
OYSTER_MARGIN = 0.20  # 20% take rate
BASE_PAYOUT = 5.0  # Base $5 per qualified session

# In-memory payout queue (in production, use Redis/DB)
payout_queue: Dict[str, Dict] = {}
completed_payouts: Dict[str, Dict] = {}
contributor_balances: Dict[str, Dict] = {}

app = FastAPI(title="Payout Engine", version="1.0.0")


class RouteType(int, Enum):
    """Route types with payout multipliers."""
    TYPE_1 = 1
    TYPE_2 = 2
    TYPE_3 = 3
    TYPE_4 = 4


class PayoutStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"


class PayoutMethod(str, Enum):
    STRIPE = "stripe"
    PAYPAL = "paypal"


# --- Data Models ---

class SessionApproval(BaseModel):
    """Buyer approves session - triggers payout."""
    session_id: str
    buyer_id: str
    contributor_id: str
    route_type: int
    is_multi_player: bool = False
    is_novel_scene: bool = False
    depth_source: str = "monocular_da_v2"  # or "engine_zbuffer"
    audit_score: int = Field(ge=0, le=200)
    buyer_amount_charged: float  # What buyer paid


class PayoutRequest(BaseModel):
    """Contributor requests withdrawal."""
    contributor_id: str
    amount: float
    payout_method: PayoutMethod = PayoutMethod.STRIPE


class PayoutRecord(BaseModel):
    payout_id: str
    contributor_id: str
    session_id: str
    amount: float
    method: PayoutMethod
    status: PayoutStatus
    created_at: datetime
    processed_at: Optional[datetime] = None
    stripe_transfer_id: Optional[str] = None
    idempotency_key: str


@dataclass
class PayoutQueueItem:
    """Internal payout queue item."""
    payout_id: str
    contributor_id: str
    session_id: str
    amount: float
    method: PayoutMethod
    idempotency_key: str
    retry_count: int = 0
    status: PayoutStatus = PayoutStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)


# --- Helper Functions ---

def parse_datetime(dt_value: Any) -> Optional[datetime]:
    """Parse datetime from string or datetime object."""
    if dt_value is None:
        return None
    if isinstance(dt_value, datetime):
        return dt_value
    if isinstance(dt_value, str):
        try:
            return datetime.fromisoformat(dt_value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


# --- Payout Calculation ---

def calculate_payout(
    route_type: int,
    is_multi_player: bool,
    is_novel_scene: bool,
    depth_source: str,
    audit_score: int
) -> float:
    """
    Calculate payout per session based on pricing model.
    
    Base: $5 per qualified session (passes audit ≥ 101)
    Multipliers: route_type 3+4 = 1.5x, multi-player = 2x, novel scene = 1.3x
    Quality: depth source engine_zbuffer adds $2
    """
    # Must pass audit threshold
    if audit_score < 101:
        logger.warning(f"Session failed audit: score {audit_score} < 101")
        return 0.0
    
    amount = BASE_PAYOUT
    
    # Route type multiplier (3+4 = 1.5x)
    if route_type in (3, 4):
        amount *= 1.5
    
    # Multi-player multiplier (2x)
    if is_multi_player:
        amount *= 2.0
    
    # Novel scene multiplier (1.3x)
    if is_novel_scene:
        amount *= 1.3
    
    # Depth source bonus
    if depth_source == "engine_zbuffer":
        amount += 2.0
    
    return round(amount, 2)


def calculate_daily_total(contributor_id: str, check_date: Optional[datetime] = None) -> float:
    """Calculate total payouts for a contributor on a given date."""
    if check_date is None:
        check_date = datetime.utcnow()
    
    date_str = check_date.strftime("%Y-%m-%d")
    total = 0.0
    
    for payout in completed_payouts.values():
        if payout["contributor_id"] == contributor_id:
            payout_date = parse_datetime(payout.get("processed_at") or payout.get("created_at"))
            if payout_date:
                payout_date_str = payout_date.strftime("%Y-%m-%d")
                if payout_date_str == date_str:
                    total += payout["amount"]
    
    return total


def check_daily_cap(contributor_id: str, amount: float) -> bool:
    """Check if payout would exceed daily cap."""
    daily_total = calculate_daily_total(contributor_id)
    return (daily_total + amount) <= DAILY_CAP


# --- API Endpoints ---

@app.post("/api/payout/init")
async def init_payout(approval: SessionApproval, background_tasks: BackgroundTasks):
    """
    Buyer approves session - queues payout for contributor.
    Idempotent: uses session_id to prevent double-queueing.
    """
    # Check if already processed (idempotency)
    for existing in payout_queue.values():
        if existing.get("session_id") == approval.session_id and existing["status"] == PayoutStatus.COMPLETED:
            logger.info(f"Session {approval.session_id} already paid, skipping")
            return {"status": "already_paid", "payout_id": existing["payout_id"]}
    
    # Calculate payout amount
    amount = calculate_payout(
        route_type=approval.route_type,
        is_multi_player=approval.is_multi_player,
        is_novel_scene=approval.is_novel_scene,
        depth_source=approval.depth_source,
        audit_score=approval.audit_score
    )
    
    if amount <= 0:
        return {"status": "no_payout", "reason": "audit_failed"}
    
    # Check daily cap
    if not check_daily_cap(contributor_id=approval.contributor_id, amount=amount):
        logger.warning(f"Daily cap reached for contributor {approval.contributor_id}")
        return {"status": "cap_reached", "amount": amount, "daily_total": calculate_daily_total(approval.contributor_id)}
    
    # Determine payout method (default Stripe, fallback to PayPal)
    # In production, check contributor's country for PayPal eligibility
    payout_method = PayoutMethod.STRIPE
    
    # Create payout record
    payout_id = str(uuid.uuid4())
    idempotency_key = f"payout_{approval.session_id}_{approval.contributor_id}"
    
    queue_item = PayoutQueueItem(
        payout_id=payout_id,
        contributor_id=approval.contributor_id,
        session_id=approval.session_id,
        amount=amount,
        method=payout_method,
        idempotency_key=idempotency_key,
        status=PayoutStatus.PENDING
    )
    
    payout_queue[payout_id] = asdict(queue_item)
    
    # Log to audit trail
    _write_audit_log("PENDING", payout_id, approval.contributor_id, amount, payout_method)
    
    # Trigger background processing
    background_tasks.add_task(process_payout_queue)
    
    return {
        "status": "queued",
        "payout_id": payout_id,
        "amount": amount,
        "method": payout_method,
        "idempotency_key": idempotency_key
    }


@app.post("/api/payout/process")
async def process_payouts():
    """
    Cron-triggered: process payout queue, hit Stripe Transfer API.
    Idempotent: re-running never double-pays.
    """
    processed = []
    failed = []
    
    for payout_id, item in list(payout_queue.items()):
        if item["status"] not in (PayoutStatus.PENDING, PayoutStatus.RETRY):
            continue
        
        # Idempotency check: skip if already completed
        if payout_id in completed_payouts:
            continue
        
        # Update status
        item["status"] = PayoutStatus.PROCESSING
        payout_queue[payout_id] = item
        
        try:
            if item["method"] == PayoutMethod.STRIPE:
                from server.stripe_connect import execute_stripe_transfer
                transfer_id = execute_stripe_transfer(
                    contributor_id=item["contributor_id"],
                    amount=item["amount"],
                    idempotency_key=item["idempotency_key"]
                )
            else:
                from server.paypal_payouts import execute_paypal_payout
                transfer_id = execute_paypal_payout(
                    contributor_id=item["contributor_id"],
                    amount=item["amount"],
                    idempotency_key=item["idempotency_key"]
                )
            
            # Mark completed
            item["status"] = PayoutStatus.COMPLETED
            item["processed_at"] = datetime.utcnow().isoformat()
            item["stripe_transfer_id"] = transfer_id
            payout_queue[payout_id] = item
            
            # Add to completed (for idempotency and balance tracking)
            completed_payouts[payout_id] = item
            
            # Update contributor balance
            cid = item["contributor_id"]
            if cid not in contributor_balances:
                contributor_balances[cid] = {"available": 0.0, "pending": 0.0, "paid_today": 0.0}
            contributor_balances[cid]["paid_today"] += item["amount"]
            
            # Log to audit trail
            _write_audit_log("COMPLETED", payout_id, item["contributor_id"], item["amount"], item["method"])
            
            processed.append(payout_id)
            
        except Exception as e:
            logger.error(f"Payout {payout_id} failed: {e}")
            item["status"] = PayoutStatus.RETRY
            item["retry_count"] = item.get("retry_count", 0) + 1
            
            if item["retry_count"] >= 3:
                item["status"] = PayoutStatus.FAILED
                _write_audit_log("FAILED", payout_id, item["contributor_id"], item["amount"], item["method"], str(e))
            
            payout_queue[payout_id] = item
            failed.append(payout_id)
    
    return {
        "processed": len(processed),
        "failed": len(failed),
        "payout_ids": processed
    }


async def process_payout_queue():
    """Background task wrapper for processing queue."""
    await process_payouts()


@app.get("/api/payout/{contributor_id}/balance")
async def get_balance(contributor_id: str):
    """View available + pending balance for a contributor."""
    # Calculate from completed and pending payouts
    available = 0.0
    pending = 0.0
    
    for payout in completed_payouts.values():
        if payout["contributor_id"] == contributor_id:
            available += payout["amount"]
    
    for payout in payout_queue.values():
        if payout["contributor_id"] == contributor_id and payout["status"] == PayoutStatus.PENDING:
            pending += payout["amount"]
    
    daily_total = calculate_daily_total(contributor_id)
    
    return {
        "contributor_id": contributor_id,
        "available_balance": round(available, 2),
        "pending_balance": round(pending, 2),
        "paid_today": round(daily_total, 2),
        "daily_cap": DAILY_CAP,
        "remaining_today": round(max(0, DAILY_CAP - daily_total), 2)
    }


@app.post("/api/payout/withdraw")
async def request_withdrawal(request: PayoutRequest):
    """Contributor requests withdrawal."""
    # Get current balance
    balance = await get_balance(request.contributor_id)
    
    if balance["available_balance"] < request.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    
    # Process withdrawal via appropriate method
    if request.payout_method == PayoutMethod.STRIPE:
        from server.stripe_connect import execute_stripe_transfer
        transfer_id = execute_stripe_transfer(
            contributor_id=request.contributor_id,
            amount=request.amount,
            idempotency_key=f"withdraw_{request.contributor_id}_{datetime.utcnow().isoformat()}"
        )
    else:
        from server.paypal_payouts import execute_paypal_payout
        transfer_id = execute_paypal_payout(
            contributor_id=request.contributor_id,
            amount=request.amount,
            idempotency_key=f"withdraw_{request.contributor_id}_{datetime.utcnow().isoformat()}"
        )
    
    return {
        "status": "withdrawal_initiated",
        "amount": request.amount,
        "method": request.payout_method,
        "transfer_id": transfer_id
    }


# --- Audit Logging ---

def _write_audit_log(
    action: str,
    payout_id: str,
    contributor_id: str,
    amount: float,
    method: PayoutMethod,
    error: Optional[str] = None
):
    """Write to audit log and oyster_provenance manifest."""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "payout_id": payout_id,
        "contributor_id": contributor_id,
        "amount": amount,
        "method": method.value,
        "error": error
    }
    
    # Write to payouts.log
    log_path = os.path.expanduser("~/.oyster/payouts.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
    
    # Write to oyster_provenance manifest
    provenance_path = os.path.expanduser("~/.oyster/oyster_provenance")
    with open(provenance_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
    
    logger.info(f"Audit: {action} - {payout_id} - ${amount} via {method.value}")


# --- Health Check ---

@app.get("/health")
async def health_check():
    return {"status": "healthy", "queue_size": len(payout_queue)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
