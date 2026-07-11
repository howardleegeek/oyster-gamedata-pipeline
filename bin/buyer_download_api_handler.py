#!/usr/bin/env python3
"""
G195 · bin/buyer_download_api_handler.py

FastAPI /v1/buyer/clips endpoint: paginated clip list + presigned download URL per clip.
Features: JWT authentication, rate limiting, audit logging.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

# Lazy imports for optional dependencies
try:
    import uvicorn
    from fastapi import Depends, FastAPI, HTTPException, Query, Request
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    FastAPI = object
    HTTPException = Exception

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("buyer_api")

# Constants
DEFAULT_RATE_LIMIT_PER_MINUTE = 60
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
PRESIGNED_URL_EXPIRY_SECONDS = 3600


@dataclass
class Clip:
    """Represents a video clip in the system."""
    clip_id: str
    title: str
    duration_seconds: float
    file_size_bytes: int
    created_at: str
    storage_path: str
    content_type: str = "video/mp4"
    metadata: Dict[str, Any] = field(default_factory=dict)


class JWTAuthError(Exception):
    """Raised when JWT authentication fails."""
    pass


def decode_jwt(token: str, secret: str) -> Dict[str, Any]:
    """Decode and validate a JWT token using HMAC-SHA256."""
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTAuthError("Invalid token format")
    try:
        payload_json = base64.urlsafe_b64decode(parts[1] + "=" * (4 - len(parts[1]) % 4))
        payload = json.loads(payload_json)
    except Exception as e:
        raise JWTAuthError(f"Invalid payload: {e}") from None
    if "exp" in payload and datetime.now(timezone.utc).timestamp() > payload["exp"]:
        raise JWTAuthError("Token expired")
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    ).decode().rstrip("=")
    if not hmac.compare_digest(parts[2], expected_sig):
        raise JWTAuthError("Invalid signature")
    return payload


def generate_presigned_url(storage_path: str, base_url: str, secret: str,
                          expires_in: int = PRESIGNED_URL_EXPIRY_SECONDS) -> str:
    """Generate a presigned download URL for a clip."""
    expires = int(time.time()) + expires_in
    to_sign = f"{storage_path}\n{expires}"
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), to_sign.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    params = {"path": storage_path, "expires": expires, "signature": signature}
    return f"{base_url}?{urlencode(params)}"


class RateLimiter:
    """Simple in-memory rate limiter using sliding window."""

    def __init__(self, per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
                 per_hour: int = 1000):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> Tuple[bool, Optional[str]]:
        """Check if client is within rate limits."""
        now = time.time()
        requests = self._requests[client_id]
        requests[:] = [t for t in requests if now - t < 3600]
        minute_count = sum(1 for t in requests if now - t < 60)
        hour_count = len(requests)
        if minute_count >= self.per_minute:
            return False, f"Rate limit exceeded: {self.per_minute} requests per minute"
        if hour_count >= self.per_hour:
            return False, f"Rate limit exceeded: {self.per_hour} requests per hour"
        requests.append(now)
        return True, None


class AuditLogger:
    """Audit logger for tracking API access."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self._temp_dir = tempfile.mkdtemp(prefix="audit_")
            db_path = f"{self._temp_dir}/audit.db"
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL, buyer_id TEXT NOT NULL,
                    action TEXT NOT NULL, resource TEXT, ip_address TEXT,
                    user_agent TEXT, status_code INTEGER, details TEXT
                )
            """)
            conn.commit()

    def log(self, buyer_id: str, action: str, resource: Optional[str] = None,
            ip_address: Optional[str] = None, user_agent: Optional[str] = None,
            status_code: int = 200, details: Optional[Dict] = None) -> None:
        """Log an audit event."""
        timestamp = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(details) if details else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, buyer_id, action, resource, "
                "ip_address, user_agent, status_code, details) VALUES (?,?,?,?,?,?,?,?)",
                (timestamp, buyer_id, action, resource, ip_address, user_agent, status_code, details_json)
            )
            conn.commit()
        logger.info(f"AUDIT: buyer={buyer_id} action={action} resource={resource} status={status_code}")


class ClipStore:
    """In-memory store for clips (demo implementation)."""

    def __init__(self) -> None:
        self._clips: Dict[str, Clip] = {}
        self._buyer_clips: Dict[str, List[str]] = defaultdict(list)

    def add_clip(self, buyer_id: str, clip: Clip) -> None:
        self._clips[clip.clip_id] = clip
        if clip.clip_id not in self._buyer_clips[buyer_id]:
            self._buyer_clips[buyer_id].append(clip.clip_id)

    def get_clips_for_buyer(self, buyer_id: str, page: int = 1,
                            page_size: int = DEFAULT_PAGE_SIZE) -> Tuple[List[Clip], int]:
        """Get paginated clips for a buyer."""
        clip_ids = self._buyer_clips.get(buyer_id, [])
        total = len(clip_ids)
        start = (page - 1) * page_size
        end = start + page_size
        clips = [self._clips[cid] for cid in clip_ids[start:end] if cid in self._clips]
        return clips, total


# Global instances
rate_limiter = RateLimiter()
audit_logger = AuditLogger()
clip_store = ClipStore()
security = HTTPBearer() if HAS_FASTAPI else None


def create_app(jwt_secret: str, download_base_url: str) -> "FastAPI":
    """Create and configure the FastAPI application."""
    if not HAS_FASTAPI:
        raise RuntimeError("FastAPI is required. Install with: pip install fastapi uvicorn")

    app = FastAPI(title="Buyer Download API", version="1.0.0")

    def get_current_buyer(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
        """Extract and validate buyer from JWT token."""
        try:
            return decode_jwt(credentials.credentials, jwt_secret)
        except JWTAuthError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

    @app.get("/v1/buyer/clips")
    async def list_clips(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        buyer: Dict[str, Any] = Depends(get_current_buyer)
    ) -> Dict[str, Any]:
        """List clips available to the authenticated buyer with presigned download URLs."""
        buyer_id = buyer.get("buyer_id", "unknown")
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        allowed, error_msg = rate_limiter.is_allowed(buyer_id)
        if not allowed:
            audit_logger.log(buyer_id, "list_clips", ip_address=client_ip,
                           user_agent=user_agent, status_code=429, details={"error": error_msg})
            raise HTTPException(status_code=429, detail=error_msg)

        clips, total = clip_store.get_clips_for_buyer(buyer_id, page, page_size)
        clip_responses = []
        for clip in clips:
            download_url = generate_presigned_url(clip.storage_path, download_base_url, jwt_secret)
            clip_responses.append({
                "clip_id": clip.clip_id, "title": clip.title,
                "duration_seconds": clip.duration_seconds, "file_size_bytes": clip.file_size_bytes,
                "created_at": clip.created_at, "download_url": download_url,
                "content_type": clip.content_type, "metadata": clip.metadata
            })

        total_pages = (total + page_size - 1) // page_size
        audit_logger.log(buyer_id, "list_clips", ip_address=client_ip,
                        user_agent=user_agent, status_code=200, details={"page": page, "count": len(clips)})
        return {"clips": clip_responses, "page": page, "page_size": page_size,
                "total_count": total, "total_pages": total_pages}

    @app.get("/health")
    async def health_check() -> Dict[str, str]:
        return {"status": "healthy"}

    return app


def populate_demo_data(store: ClipStore) -> None:
    """Populate store with demo clips for testing."""
    demo_clips = [
        Clip("clip001", "Demo Video 1", 120.5, 52428800, "2024-01-15T10:30:00Z", "/storage/clips/clip001.mp4"),
        Clip("clip002", "Demo Video 2", 45.0, 20971520, "2024-01-16T14:20:00Z", "/storage/clips/clip002.mp4"),
        Clip("clip003", "Demo Video 3", 300.0, 131072000, "2024-01-17T09:00:00Z", "/storage/clips/clip003.mp4"),
    ]
    for clip in demo_clips:
        store.add_clip("demo_buyer", clip)


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the buyer download API handler."""
    parser = argparse.ArgumentParser(description="Buyer Download API Handler")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--jwt-secret", required=True, help="Secret key for JWT validation")
    parser.add_argument("--download-base-url", default="https://cdn.example.com/download",
                       help="Base URL for presigned download URLs")
    parser.add_argument("--rate-limit-per-minute", type=int, default=DEFAULT_RATE_LIMIT_PER_MINUTE)
    parser.add_argument("--demo", action="store_true", help="Populate with demo data")

    args = parser.parse_args(argv)

    if not HAS_FASTAPI:
        print("Error: FastAPI is required. Install with: pip install fastapi uvicorn", file=sys.stderr)
        return 1

    global rate_limiter
    rate_limiter = RateLimiter(per_minute=args.rate_limit_per_minute, per_hour=args.rate_limit_per_minute * 20)

    if args.demo:
        populate_demo_data(clip_store)
        logger.info("Populated demo data for testing")

    app = create_app(args.jwt_secret, args.download_base_url)
    logger.info(f"Starting Buyer Download API on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
