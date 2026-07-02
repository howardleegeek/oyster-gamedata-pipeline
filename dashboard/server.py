"""
FastAPI backend for Oyster Dashboard.
Provides REST API for session management, provenance verification, and payouts.
"""

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import provenance verification (mock for now, real import when available)
try:
    import oyster_provenance
    HAS_PROVENANCE = True
except ImportError:
    HAS_PROVENANCE = False

app = FastAPI(
    title="Oyster Dashboard API",
    description="Backend API for buyer/contributor dashboard",
    version="1.0.0"
)

# CORS for Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT Configuration (imported from oauth.py)
from oauth import JWT_ALGORITHM, JWT_SECRET

# Mock database
MOCK_SESSIONS = {}
MOCK_USERS = {
    "buyer1": {"role": "buyer", "id": "buyer1"},
    "contributor1": {"role": "contributor", "id": "contributor1"},
    "contributor2": {"role": "contributor", "id": "contributor2"},
}

# Initialize mock data
def init_mock_data():
    for i in range(1, 21):
        session_id = f"session_{i:03d}"
        MOCK_SESSIONS[session_id] = {
            "id": session_id,
            "game": ["minecraft", "roblox", "fortnite"][i % 3],
            "scene": f"scene_{(i % 5) + 1}",
            "route_type": ["exploration", "task", "combat"][i % 3],
            "audit_score": 0.7 + (i % 30) / 100,
            "contributor_id": f"contributor{(i % 2) + 1}",
            "created_at": datetime.utcnow().isoformat(),
            "duration_seconds": 60 + i * 10,
            "status": "pending",  # All sessions start as pending for testing
            "provenance_hash": hashlib.sha256(session_id.encode()).hexdigest(),
            "mp4_path": f"/data/sessions/{session_id}/video.mp4",
            "depth_path": f"/data/sessions/{session_id}/depth.npy",
            "actions_path": f"/data/sessions/{session_id}/actions.json",
            "payout_amount": 5.0 + (i % 10),
        }

init_mock_data()


# Pydantic models
class SessionMetadata(BaseModel):
    id: str
    game: str
    scene: str
    route_type: str
    audit_score: float
    contributor_id: str
    created_at: str
    duration_seconds: int
    status: str
    provenance_hash: str
    payout_amount: float


class SessionListResponse(BaseModel):
    sessions: List[SessionMetadata]
    total: int
    page: int
    page_size: int


class AuditResponse(BaseModel):
    session_id: str
    audit_score: float
    checks: Dict[str, Any]
    timestamp: str


class VerifyResponse(BaseModel):
    session_id: str
    valid: bool
    chain_intact: bool
    hash_matches: bool
    details: Dict[str, Any]


class ApprovalRequest(BaseModel):
    notes: Optional[str] = None


class RejectionRequest(BaseModel):
    reason: str
    notes: Optional[str] = None


class UserInfo(BaseModel):
    user_id: str
    role: str


# Auth dependency
async def get_current_user(authorization: str = Header(None)) -> UserInfo:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    try:
        token = authorization.replace("Bearer ", "")
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        role = payload.get("role")
        
        if not user_id or not role:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        
        return UserInfo(user_id=user_id, role=role)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_buyer(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if user.role != "buyer":
        raise HTTPException(status_code=403, detail="Buyer access required")
    return user


async def require_contributor(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if user.role != "contributor":
        raise HTTPException(status_code=403, detail="Contributor access required")
    return user


# Helper to generate JWT tokens (for testing)
@app.get("/api/auth/token")
async def get_test_token(user_id: str, role: str):
    """Generate a test JWT token. Remove in production."""
    token = jwt.encode(
        {"sub": user_id, "role": role, "exp": datetime.utcnow().timestamp() + 86400},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )
    return {"access_token": token, "token_type": "bearer"}


# Session endpoints
@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    game: Optional[str] = Query(None),
    scene: Optional[str] = Query(None),
    route_type: Optional[str] = Query(None),
    min_audit_score: Optional[float] = Query(None),
    max_audit_score: Optional[float] = Query(None),
    status: Optional[str] = Query(None),
    contributor_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: UserInfo = Depends(get_current_user)
):
    """List sessions with filters. Contributors see only their own sessions."""
    sessions = list(MOCK_SESSIONS.values())
    
    # Contributors can only see their own sessions
    if user.role == "contributor":
        sessions = [s for s in sessions if s["contributor_id"] == user.user_id]
    
    # Apply filters
    if game:
        sessions = [s for s in sessions if s["game"] == game]
    if scene:
        sessions = [s for s in sessions if s["scene"] == scene]
    if route_type:
        sessions = [s for s in sessions if s["route_type"] == route_type]
    if min_audit_score is not None:
        sessions = [s for s in sessions if s["audit_score"] >= min_audit_score]
    if max_audit_score is not None:
        sessions = [s for s in sessions if s["audit_score"] <= max_audit_score]
    if status:
        sessions = [s for s in sessions if s["status"] == status]
    if contributor_id and user.role == "buyer":
        sessions = [s for s in sessions if s["contributor_id"] == contributor_id]
    
    # Pagination
    total = len(sessions)
    start = (page - 1) * page_size
    end = start + page_size
    sessions_page = sessions[start:end]
    
    return SessionListResponse(
        sessions=[SessionMetadata(**s) for s in sessions_page],
        total=total,
        page=page,
        page_size=page_size
    )


@app.get("/api/sessions/{session_id}", response_model=SessionMetadata)
async def get_session(
    session_id: str,
    user: UserInfo = Depends(get_current_user)
):
    """Get single session metadata."""
    session = MOCK_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Contributors can only see their own sessions
    if user.role == "contributor" and session["contributor_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return SessionMetadata(**session)


@app.get("/api/sessions/{session_id}/preview")
async def get_session_preview(
    session_id: str,
    user: UserInfo = Depends(get_current_user),
    range: Optional[str] = Header(None)
):
    """Stream mp4 video with byte range support (HTTP 206)."""
    session = MOCK_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Contributors can only preview their own sessions
    if user.role == "contributor" and session["contributor_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Generate mock video data
    video_data = f"MOCK_MP4_DATA_FOR_{session_id}".encode()
    total_size = len(video_data)
    
    # Handle byte range request
    if range:
        start, end = range.replace("bytes=", "").split("-")
        start = int(start)
        end = int(end) if end else total_size - 1
        
        content = video_data[start:end + 1]
        headers = {
            "Content-Range": f"bytes {start}-{end}/{total_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(content)),
            "Content-Type": "video/mp4",
        }
        return Response(content=content, status_code=206, headers=headers)
    
    return Response(
        content=video_data,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Content-Length": str(total_size)}
    )


@app.get("/api/sessions/{session_id}/audit", response_model=AuditResponse)
async def get_session_audit(
    session_id: str,
    user: UserInfo = Depends(get_current_user)
):
    """Get audit details for a session."""
    session = MOCK_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Contributors can only see their own sessions
    if user.role == "contributor" and session["contributor_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return AuditResponse(
        session_id=session_id,
        audit_score=session["audit_score"],
        checks={
            "frame_consistency": session["audit_score"] > 0.8,
            "action_validity": session["audit_score"] > 0.75,
            "depth_quality": session["audit_score"] > 0.85,
            "temporal_smoothness": session["audit_score"] > 0.7,
        },
        timestamp=datetime.utcnow().isoformat()
    )


@app.get("/api/sessions/{session_id}/verify", response_model=VerifyResponse)
async def verify_session_provenance(
    session_id: str,
    user: UserInfo = Depends(get_current_user)
):
    """Verify provenance hash chain for a session."""
    session = MOCK_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Contributors can only verify their own sessions
    if user.role == "contributor" and session["contributor_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Use real provenance verification if available
    if HAS_PROVENANCE:
        try:
            result = oyster_provenance.verify(session_id)
            return VerifyResponse(
                session_id=session_id,
                valid=result.get("valid", False),
                chain_intact=result.get("chain_intact", False),
                hash_matches=result.get("hash_matches", False),
                details=result
            )
        except Exception as e:
            pass
    
    # Mock verification - hash should match the session_id
    expected_hash = hashlib.sha256(session_id.encode()).hexdigest()
    hash_matches = session["provenance_hash"] == expected_hash
    
    return VerifyResponse(
        session_id=session_id,
        valid=hash_matches,
        chain_intact=hash_matches,
        hash_matches=hash_matches,
        details={
            "computed_hash": expected_hash,
            "stored_hash": session["provenance_hash"],
            "verification_time": datetime.utcnow().isoformat()
        }
    )


@app.post("/api/sessions/{session_id}/approve")
async def approve_session(
    session_id: str,
    request: ApprovalRequest,
    user: UserInfo = Depends(require_buyer)
):
    """Buyer approves a session (triggers payout)."""
    session = MOCK_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session["status"] == "approved":
        raise HTTPException(status_code=400, detail="Session already approved")
    
    # Update status
    session["status"] = "approved"
    session["approved_at"] = datetime.utcnow().isoformat()
    session["approved_by"] = user.user_id
    if request.notes:
        session["approval_notes"] = request.notes
    
    return {
        "status": "approved",
        "session_id": session_id,
        "payout_triggered": True,
        "payout_amount": session["payout_amount"]
    }


@app.post("/api/sessions/{session_id}/reject")
async def reject_session(
    session_id: str,
    request: RejectionRequest,
    user: UserInfo = Depends(require_buyer)
):
    """Buyer rejects a session with reason."""
    session = MOCK_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session["status"] == "rejected":
        raise HTTPException(status_code=400, detail="Session already rejected")
    
    # Update status
    session["status"] = "rejected"
    session["rejected_at"] = datetime.utcnow().isoformat()
    session["rejected_by"] = user.user_id
    session["rejection_reason"] = request.reason
    if request.notes:
        session["rejection_notes"] = request.notes
    
    return {
        "status": "rejected",
        "session_id": session_id,
        "reason": request.reason
    }


# Contributor-specific endpoints
@app.get("/api/my/sessions", response_model=SessionListResponse)
async def get_my_sessions(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: UserInfo = Depends(require_contributor)
):
    """Get contributor's own sessions."""
    sessions = [s for s in MOCK_SESSIONS.values() if s["contributor_id"] == user.user_id]
    
    if status:
        sessions = [s for s in sessions if s["status"] == status]
    
    total = len(sessions)
    start = (page - 1) * page_size
    end = start + page_size
    sessions_page = sessions[start:end]
    
    return SessionListResponse(
        sessions=[SessionMetadata(**s) for s in sessions_page],
        total=total,
        page=page,
        page_size=page_size
    )


@app.get("/api/my/payouts")
async def get_my_payouts(
    user: UserInfo = Depends(require_contributor)
):
    """Get contributor's payout summary."""
    sessions = [s for s in MOCK_SESSIONS.values() if s["contributor_id"] == user.user_id]
    
    approved = [s for s in sessions if s["status"] == "approved"]
    pending = [s for s in sessions if s["status"] == "pending"]
    rejected = [s for s in sessions if s["status"] == "rejected"]
    
    total_payout = sum(s["payout_amount"] for s in approved)
    pending_payout = sum(5.0 for s in pending)  # Estimated
    
    return {
        "contributor_id": user.user_id,
        "total_sessions": len(sessions),
        "approved_sessions": len(approved),
        "pending_sessions": len(pending),
        "rejected_sessions": len(rejected),
        "total_payout_usd": total_payout,
        "pending_payout_usd": pending_payout,
        "payout_history": [
            {
                "session_id": s["id"],
                "amount": s["payout_amount"],
                "approved_at": s.get("approved_at", s["created_at"])
            }
            for s in approved[:10]
        ]
    }


# Bug-fix 2026-05-17: Pydantic model must be defined BEFORE the function that
# uses it as a type hint. server.py doesn't `from __future__ import annotations`,
# so type hints are evaluated eagerly. Originally defined at line 500, hoisted
# here so request_rerecord's RerecordRequest annotation resolves at module
# import time. Test collection (test_dashboard_api.py) was breaking with
# NameError before this fix.
class RerecordRequest(BaseModel):
    reason: str


@app.post("/api/sessions/{session_id}/rerecord")
async def request_rerecord(
    session_id: str,
    request: RerecordRequest,
    user: UserInfo = Depends(require_contributor)
):
    """Contributor requests to re-record a rejected session."""
    session = MOCK_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["contributor_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if session["status"] != "rejected":
        raise HTTPException(status_code=400, detail="Can only request re-record for rejected sessions")

    session["rerecord_requested"] = True
    session["rerecord_reason"] = request.reason
    session["rerecord_requested_at"] = datetime.utcnow().isoformat()

    return {
        "status": "rerecord_requested",
        "session_id": session_id,
        "reason": request.reason
    }


# Bulk download endpoint
@app.post("/api/sessions/bulk-download")
async def bulk_download(
    session_ids: List[str],
    user: UserInfo = Depends(require_buyer)
):
    """Generate bulk download bundle for selected sessions."""
    sessions = []
    for sid in session_ids:
        if sid in MOCK_SESSIONS:
            sessions.append(MOCK_SESSIONS[sid])
    
    if not sessions:
        raise HTTPException(status_code=400, detail="No valid sessions found")
    
    # Return bundle info (actual file generation would happen here)
    bundle_id = hashlib.sha256(",".join(session_ids).encode()).hexdigest()[:16]
    
    return {
        "bundle_id": bundle_id,
        "session_count": len(sessions),
        "sessions": [{"id": s["id"], "game": s["game"]} for s in sessions],
        "download_url": f"/api/bundles/{bundle_id}/download",
        "expires_at": datetime.utcnow().isoformat()
    }


# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
