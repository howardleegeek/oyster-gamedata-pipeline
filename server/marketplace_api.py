"""
Marketplace REST API for AI labs (buyers).

Provides programmatic access to browse, filter, download, and approve sessions.
"""

import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

app = FastAPI(
    title="Oyster Marketplace API",
    description="REST API for AI labs to browse, filter, download, and approve sessions",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()

# Rate limiting storage (in production, use Redis)
rate_limit_store: Dict[str, List[float]] = {}
RATE_LIMIT_PER_HOUR = 1000

# Webhook storage
webhooks_store: Dict[str, Dict] = {}

# Bulk download jobs storage
bulk_jobs_store: Dict[str, Dict] = {}

# Sessions storage (mock data)
sessions_store: Dict[str, Dict] = {}


# JWT verification (integrates with #24 OAuth flow)
async def verify_jwt(token: str) -> Dict:
    """Verify JWT token and return buyer info."""
    # In production, verify against OAuth server
    # For now, accept any token with 'buyer' role
    if not token or len(token) < 10:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"buyer_id": "buyer_123", "role": "buyer"}


async def get_current_buyer(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict:
    """Extract and verify buyer from JWT token."""
    token = credentials.credentials
    return await verify_jwt(token)


def check_rate_limit(buyer_id: str) -> None:
    """Check and enforce rate limit per buyer."""
    now = time.time()
    hour_ago = now - 3600

    if buyer_id not in rate_limit_store:
        rate_limit_store[buyer_id] = []

    # Clean old requests
    rate_limit_store[buyer_id] = [t for t in rate_limit_store[buyer_id] if t > hour_ago]

    if len(rate_limit_store[buyer_id]) >= RATE_LIMIT_PER_HOUR:
        retry_after = int(rate_limit_store[buyer_id][0] + 3600 - now) + 1
        raise HTTPException(
            status_code=429, detail="Rate limit exceeded", headers={"Retry-After": str(retry_after)}
        )

    rate_limit_store[buyer_id].append(now)


# Models
class Session(BaseModel):
    id: str
    game: str
    scene: str
    route_type: str
    audit_score: int
    quality_score: int
    has_depth: bool
    has_audio: bool
    has_voice: bool
    has_zbuffer: bool
    created_at: datetime
    status: str = "available"
    download_urls: Optional[Dict[str, str]] = None


class SessionList(BaseModel):
    sessions: List[Session]
    total: int
    page: int
    page_size: int
    has_more: bool


class AuditResult(BaseModel):
    session_id: str
    audit_score: int
    checks: Dict[str, Any]
    passed: bool
    timestamp: datetime


class VerifyResult(BaseModel):
    session_id: str
    verified: bool
    provenance_chain: List[Dict]
    timestamp: datetime


class WebhookRegistration(BaseModel):
    url: str
    events: List[str] = Field(
        default=["session.created", "session.audit_passed", "session.approved", "payout.completed"]
    )
    secret: str


class WebhookInfo(BaseModel):
    id: str
    url: str
    events: List[str]
    created_at: datetime


class BulkDownloadRequest(BaseModel):
    filters: Dict[str, Any]
    since: Optional[str] = None


class BulkDownloadJob(BaseModel):
    job_id: str
    status: str
    created_at: datetime
    download_url: Optional[str] = None
    total_sessions: Optional[int] = None
    expires_at: Optional[datetime] = None


class ApprovalRequest(BaseModel):
    notes: Optional[str] = None


class RejectionRequest(BaseModel):
    reason: str
    notes: Optional[str] = None


# Helper functions
def generate_signed_url(session_id: str, file_type: str) -> str:
    """Generate a signed download URL for a file."""
    expires = int(time.time()) + 3600
    signature = hmac.new(
        b"download_secret_key", f"{session_id}:{file_type}:{expires}".encode(), hashlib.sha256
    ).hexdigest()[:16]
    return f"https://storage.oyster.ai/sessions/{session_id}/{file_type}?expires={expires}&sig={signature}"


def compute_job_id(filters: Dict, since: Optional[str]) -> str:
    """Compute deterministic job ID for idempotent bulk downloads."""
    filter_str = str(sorted(filters.items())) + str(since or "")
    hash_input = filter_str.encode()
    return hashlib.sha256(hash_input).hexdigest()[:16]


def filter_sessions(filters: Dict[str, Any]) -> List[Dict]:
    """Filter sessions based on criteria."""
    # Mock implementation - in production, query database
    mock_sessions = [
        {
            "id": "sess_001",
            "game": "cyberpunk_2077",
            "scene": "night_city_downtown",
            "route_type": "driving",
            "audit_score": 105,
            "quality_score": 85,
            "has_depth": True,
            "has_audio": True,
            "has_voice": False,
            "has_zbuffer": True,
            "created_at": datetime(2026, 5, 17, 10, 30, 0),
            "status": "available",
        },
        {
            "id": "sess_002",
            "game": "gta_v",
            "scene": "los_santos_airport",
            "route_type": "walking",
            "audit_score": 102,
            "quality_score": 90,
            "has_depth": True,
            "has_audio": True,
            "has_voice": True,
            "has_zbuffer": True,
            "created_at": datetime(2026, 5, 18, 14, 20, 0),
            "status": "available",
        },
    ]

    result = []
    for s in mock_sessions:
        match = True
        for key, value in filters.items():
            if (
                (key == "audit_score_min" and s["audit_score"] < value)
                or (key == "quality_score_min" and s["quality_score"] < value)
                or (key == "has_depth" and s["has_depth"] != value)
                or (key == "has_audio" and s["has_audio"] != value)
                or (key == "has_voice" and s["has_voice"] != value)
                or (key == "has_zbuffer" and s["has_zbuffer"] != value)
                or (key == "game" and s["game"] != value)
                or (key == "scene" and s["scene"] != value)
                or (key == "route_type" and s["route_type"] != value)
            ):
                match = False
                break
        if match:
            result.append(s)

    return result


# Routes
@app.get("/api/v1/sessions", response_model=SessionList)
async def list_sessions(
    request: Request,
    buyer: Dict = Depends(get_current_buyer),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    game: Optional[str] = None,
    scene: Optional[str] = None,
    route_type: Optional[str] = None,
    audit_score_min: Optional[int] = None,
    quality_score_min: Optional[int] = None,
    has_depth: Optional[bool] = None,
    has_audio: Optional[bool] = None,
    has_voice: Optional[bool] = None,
    has_zbuffer: Optional[bool] = None,
):
    """List sessions with pagination and filters."""
    check_rate_limit(buyer["buyer_id"])

    filters = {}
    if game:
        filters["game"] = game
    if scene:
        filters["scene"] = scene
    if route_type:
        filters["route_type"] = route_type
    if audit_score_min is not None:
        filters["audit_score_min"] = audit_score_min
    if quality_score_min is not None:
        filters["quality_score_min"] = quality_score_min
    if has_depth is not None:
        filters["has_depth"] = has_depth
    if has_audio is not None:
        filters["has_audio"] = has_audio
    if has_voice is not None:
        filters["has_voice"] = has_voice
    if has_zbuffer is not None:
        filters["has_zbuffer"] = has_zbuffer

    all_sessions = filter_sessions(filters)
    total = len(all_sessions)

    start = (page - 1) * page_size
    end = start + page_size
    page_sessions = all_sessions[start:end]

    return SessionList(
        sessions=[Session(**s) for s in page_sessions],
        total=total,
        page=page,
        page_size=page_size,
        has_more=end < total,
    )


@app.get("/api/v1/sessions/{session_id}", response_model=Session)
async def get_session(
    session_id: str,
    buyer: Dict = Depends(get_current_buyer),
):
    """Get single session metadata with signed download URLs."""
    check_rate_limit(buyer["buyer_id"])

    # Mock session retrieval
    session_data = {
        "id": session_id,
        "game": "cyberpunk_2077",
        "scene": "night_city_downtown",
        "route_type": "driving",
        "audit_score": 105,
        "quality_score": 85,
        "has_depth": True,
        "has_audio": True,
        "has_voice": False,
        "has_zbuffer": True,
        "created_at": datetime(2026, 5, 17, 10, 30, 0),
        "status": "available",
        "download_urls": {
            "rgb": generate_signed_url(session_id, "rgb"),
            "depth": generate_signed_url(session_id, "depth"),
            "audio": generate_signed_url(session_id, "audio"),
            "metadata": generate_signed_url(session_id, "metadata"),
        },
    }

    return Session(**session_data)


@app.get("/api/v1/sessions/{session_id}/audit", response_model=AuditResult)
async def get_session_audit(
    session_id: str,
    buyer: Dict = Depends(get_current_buyer),
):
    """Get full audit JSON for a session."""
    check_rate_limit(buyer["buyer_id"])

    return AuditResult(
        session_id=session_id,
        audit_score=105,
        checks={
            "frame_consistency": {"passed": True, "score": 98},
            "depth_quality": {"passed": True, "score": 95},
            "audio_sync": {"passed": True, "score": 100},
            "motion_blur": {"passed": True, "score": 92},
        },
        passed=True,
        timestamp=datetime.utcnow(),
    )


@app.get("/api/v1/sessions/{session_id}/verify", response_model=VerifyResult)
async def verify_session(
    session_id: str,
    buyer: Dict = Depends(get_current_buyer),
):
    """Verify session provenance."""
    check_rate_limit(buyer["buyer_id"])

    return VerifyResult(
        session_id=session_id,
        verified=True,
        provenance_chain=[
            {"step": "capture", "timestamp": "2026-05-17T10:30:00Z", "node": "node_abc123"},
            {"step": "upload", "timestamp": "2026-05-17T10:45:00Z", "node": "gateway_eu_west"},
            {"step": "audit", "timestamp": "2026-05-17T11:00:00Z", "node": "audit_worker_01"},
            {"step": "store", "timestamp": "2026-05-17T11:05:00Z", "node": "storage_cluster_01"},
        ],
        timestamp=datetime.utcnow(),
    )


@app.post("/api/v1/sessions/bulk-download", response_model=BulkDownloadJob)
async def create_bulk_download(
    request: BulkDownloadRequest,
    buyer: Dict = Depends(get_current_buyer),
):
    """Initiate bulk download job. Idempotent within 24h window."""
    check_rate_limit(buyer["buyer_id"])

    # Compute deterministic job ID for idempotency
    job_id = compute_job_id(request.filters, request.since)

    # Check if job already exists and is less than 24h old
    if job_id in bulk_jobs_store:
        existing = bulk_jobs_store[job_id]
        age = datetime.utcnow() - existing["created_at"]
        if age < timedelta(hours=24):
            return BulkDownloadJob(**existing)

    # Create new job
    job_data = {
        "job_id": job_id,
        "status": "pending",
        "created_at": datetime.utcnow(),
        "download_url": None,
        "total_sessions": len(filter_sessions(request.filters)),
        "expires_at": datetime.utcnow() + timedelta(hours=24),
        "filters": request.filters,
        "since": request.since,
        "buyer_id": buyer["buyer_id"],
    }

    bulk_jobs_store[job_id] = job_data

    # In production, enqueue job for background processing
    # For now, simulate completion
    job_data["status"] = "completed"
    job_data["download_url"] = (
        f"https://storage.oyster.ai/bulk/{job_id}/download.tar.gz?expires={int(time.time()) + 86400}"
    )

    return BulkDownloadJob(**job_data)


@app.get("/api/v1/bulk-download/{job_id}", response_model=BulkDownloadJob)
async def get_bulk_download_status(
    job_id: str,
    buyer: Dict = Depends(get_current_buyer),
):
    """Poll bulk download job status."""
    check_rate_limit(buyer["buyer_id"])

    if job_id not in bulk_jobs_store:
        raise HTTPException(status_code=404, detail="Job not found")

    return BulkDownloadJob(**bulk_jobs_store[job_id])


@app.post("/api/v1/webhooks", response_model=WebhookInfo)
async def register_webhook(
    webhook: WebhookRegistration,
    buyer: Dict = Depends(get_current_buyer),
):
    """Register a webhook URL for events."""
    check_rate_limit(buyer["buyer_id"])

    webhook_id = f"wh_{uuid.uuid4().hex[:12]}"
    webhook_data = {
        "id": webhook_id,
        "url": webhook.url,
        "events": webhook.events,
        "secret": webhook.secret,
        "buyer_id": buyer["buyer_id"],
        "created_at": datetime.utcnow(),
    }

    webhooks_store[webhook_id] = webhook_data

    return WebhookInfo(
        id=webhook_id, url=webhook.url, events=webhook.events, created_at=webhook_data["created_at"]
    )


@app.get("/api/v1/webhooks", response_model=List[WebhookInfo])
async def list_webhooks(
    buyer: Dict = Depends(get_current_buyer),
):
    """List registered webhooks for the buyer."""
    check_rate_limit(buyer["buyer_id"])

    buyer_webhooks = [wh for wh in webhooks_store.values() if wh["buyer_id"] == buyer["buyer_id"]]

    return [
        WebhookInfo(id=wh["id"], url=wh["url"], events=wh["events"], created_at=wh["created_at"])
        for wh in buyer_webhooks
    ]


@app.delete("/api/v1/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    buyer: Dict = Depends(get_current_buyer),
):
    """Unregister a webhook."""
    check_rate_limit(buyer["buyer_id"])

    if webhook_id not in webhooks_store:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if webhooks_store[webhook_id]["buyer_id"] != buyer["buyer_id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    del webhooks_store[webhook_id]

    return {"status": "deleted"}


@app.post("/api/v1/sessions/{session_id}/approve")
async def approve_session(
    session_id: str,
    approval: ApprovalRequest,
    buyer: Dict = Depends(get_current_buyer),
):
    """Approve a session, triggering payout."""
    check_rate_limit(buyer["buyer_id"])

    # In production, trigger payout workflow
    # Emit webhook event
    from server.webhook_dispatcher import dispatch_event

    await dispatch_event(
        "session.approved",
        {
            "session_id": session_id,
            "buyer_id": buyer["buyer_id"],
            "approved_at": datetime.utcnow().isoformat(),
            "notes": approval.notes,
        },
    )

    return {"status": "approved", "session_id": session_id}


@app.post("/api/v1/sessions/{session_id}/reject")
async def reject_session(
    session_id: str,
    rejection: RejectionRequest,
    buyer: Dict = Depends(get_current_buyer),
):
    """Reject a session with reason."""
    check_rate_limit(buyer["buyer_id"])

    # In production, update session status
    # Emit webhook event if needed

    return {"status": "rejected", "session_id": session_id, "reason": rejection.reason}


# Health check endpoint
@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}
