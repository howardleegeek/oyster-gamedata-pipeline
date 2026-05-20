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
    from fastapi import Depends, FastAPI, HTTPException, Query, Request
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    import uvicorn
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
        raise JWTAuthError(f"Invalid payload: {e}")
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


class ClipStore:
    """SQLite-backed clip storage with buyer access control."""
    
    def __init__(self, db_path: str = ":memory:"):
        """Initialize the clip store with a SQLite database.
        
        Args:
            db_path: Path to SQLite database file. Defaults to in-memory database.
        """
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()
    
    def _init_schema(self) -> None:
        """Initialize database schema for clips and buyer access."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                clip_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                content_type TEXT DEFAULT 'video/mp4',
                metadata TEXT DEFAULT '{}'
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS buyer_access (
                buyer_id TEXT NOT NULL,
                clip_id TEXT NOT NULL,
                PRIMARY KEY (buyer_id, clip_id),
                FOREIGN KEY (clip_id) REFERENCES clips(clip_id)
            )
        """)
        self.conn.commit()
    
    def add_clip(self, clip: Clip) -> None:
        """Add a clip to the store.
        
        Args:
            clip: The Clip object to add.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO clips 
               (clip_id, title, duration_seconds, file_size_bytes, created_at, storage_path, content_type, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (clip.clip_id, clip.title, clip.duration_seconds, clip.file_size_bytes,
             clip.created_at, clip.storage_path, clip.content_type, json.dumps(clip.metadata))
        )
        self.conn.commit()
    
    def grant_access(self, buyer_id: str, clip_id: str) -> None:
        """Grant a buyer access to a clip.
        
        Args:
            buyer_id: The buyer identifier.
            clip_id: The clip identifier.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO buyer_access (buyer_id, clip_id) VALUES (?, ?)",
            (buyer_id, clip_id)
        )
        self.conn.commit()
    
    def get_clips_for_buyer(self, buyer_id: str, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> Tuple[List[Clip], int]:
        """Get clips accessible to a buyer with pagination.
        
        Args:
            buyer_id: The buyer identifier.
            page: Page number (1-indexed).
            page_size: Number of items per page.
        
        Returns:
            A tuple of (list of Clip objects, total count).
        """
        cursor = self.conn.cursor()
        offset = (page - 1) * page_size
        
        # Get total count
        cursor.execute(
            "SELECT COUNT(*) FROM buyer_access WHERE buyer_id = ?",
            (buyer_id,)
        )
        total = cursor.fetchone()[0]
        
        # Get clips for page
        cursor.execute(
            """SELECT c.clip_id, c.title, c.duration_seconds, c.file_size_bytes,
                      c.created_at, c.storage_path, c.content_type, c.metadata
               FROM clips c
               JOIN buyer_access ba ON c.clip_id = ba.clip_id
               WHERE ba.buyer_id = ?
               ORDER BY c.created_at DESC
               LIMIT ? OFFSET ?""",
            (buyer_id, page_size, offset)
        )
        
        clips = []
        for row in cursor.fetchall():
            clips.append(Clip(
                clip_id=row[0], title=row[1], duration_seconds=row[2],
                file_size_bytes=row[3], created_at=row[4], storage_path=row[5],
                content_type=row[6], metadata=json.loads(row[7])
            ))
        
        return clips, total


class RateLimiter:
    """Simple in-memory rate limiter using sliding window."""
    
    def __init__(self, requests_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE):
        """Initialize the rate limiter.
        
        Args:
            requests_per_minute: Maximum requests allowed per minute per buyer.
        """
        self.requests_per_minute = requests_per_minute
        self.requests: Dict[str, List[float]] = defaultdict(list)
    
    def is_allowed(self, buyer_id: str) -> Tuple[bool, Optional[str]]:
        """Check if a request is allowed for the buyer.
        
        Args:
            buyer_id: The buyer identifier.
        
        Returns:
            A tuple of (is_allowed, error_message).
        """
        now = time.time()
        window_start = now - 60
        
        # Clean old requests
        self.requests[buyer_id] = [t for t in self.requests[buyer_id] if t > window_start]
        
        if len(self.requests[buyer_id]) >= self.requests_per_minute:
            return False, f"Rate limit exceeded: {self.requests_per_minute} requests per minute"
        
        self.requests[buyer_id].append(now)
        return True, None


class AuditLogger:
    """Simple audit logger for API actions."""
    
    def __init__(self, log_path: Optional[str] = None):
        """Initialize the audit logger.
        
        Args:
            log_path: Path to audit log file. If None, logs to stdout.
        """
        self.log_path = log_path
        if log_path:
            self.log_file = open(log_path, "a")
        else:
            self.log_file = None
    
    def log(self, buyer_id: str, action: str, ip_address: Optional[str] = None,
            user_agent: Optional[str] = None, status_code: int = 200,
            details: Optional[Dict[str, Any]] = None) -> None:
        """Log an audit event.
        
        Args:
            buyer_id: The buyer identifier.
            action: The action being performed.
            ip_address: Client IP address.
            user_agent: Client user agent.
            status_code: HTTP status code.
            details: Additional details about the action.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "buyer_id": buyer_id,
            "action": action,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "status_code": status_code,
            "details": details or {}
        }
        line = json.dumps(entry)
        
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()
        else:
            logger.info(f"AUDIT: {line}")
    
    def close(self) -> None:
        """Close the log file if open."""
        if self.log_file:
            self.log_file.close()


def create_app(jwt_secret: str, download_base_url: str, clip_store: ClipStore,
               rate_limiter: RateLimiter, audit_logger: AuditLogger) -> FastAPI:
    """Create and configure the FastAPI application.
    
    Args:
        jwt_secret: Secret key for JWT validation.
        download_base_url: Base URL for generating presigned download URLs.
        clip_store: ClipStore instance for clip data.
        rate_limiter: RateLimiter instance for rate limiting.
        audit_logger: AuditLogger instance for audit logging.
    
    Returns:
        Configured FastAPI application.
    """
    if not HAS_FASTAPI:
        raise RuntimeError("FastAPI is required. Install with: pip install fastapi uvicorn")
    
    app = FastAPI(title="Buyer Download API", version="1.0.0")
    
    def get_current_buyer(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
        """Extract and validate buyer from JWT token."""
        try:
            return decode_jwt(credentials.credentials, jwt_secret)
        except JWTAuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
    
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
        """Return health status for the API.
        
        Returns:
            A dictionary with a "status" key indicating the API is healthy.
        """
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
        store.add_clip(clip)
        store.grant_access("demo_buyer", clip.clip_id)


def main(argv: Optional[List[str]] = None) -> int:
    """Run the buyer download API server.
    
    Args:
        argv: Command-line arguments. Defaults to sys.argv if None.
    
    Returns:
        Exit code (0 for success).
    """
    parser = argparse.ArgumentParser(description="Buyer Download API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--jwt-secret", required=True, help="JWT secret key")
    parser.add_argument("--download-base-url", required=True, help="Base URL for downloads")
    parser.add_argument("--db-path", default=":memory:", help="SQLite database path")
    parser.add_argument("--rate-limit", type=int, default=DEFAULT_RATE_LIMIT_PER_MINUTE,
                       help="Requests per minute per buyer")
    parser.add_argument("--audit-log", help="Path to audit log file")
    parser.add_argument("--demo-data", action="store_true", help="Load demo data")
    
    args = parser.parse_args(argv)
    
    clip_store = ClipStore(args.db_path)
    rate_limiter = RateLimiter(args.rate_limit)
    audit_logger = AuditLogger(args.audit_log)
    
    if args.demo_data:
        populate_demo_data(clip_store)
    
    app = create_app(args.jwt_secret, args.download_base_url, clip_store, rate_limiter, audit_logger)
    
    logger.info(f"Starting Buyer Download API on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
    
    audit_logger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())