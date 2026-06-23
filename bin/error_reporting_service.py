#!/usr/bin/env python3
"""
Error Reporting Service

FastAPI service that receives error reports, performs rate-limiting per IP and user,
deduplicates by traceback hash, and persists to PostgreSQL.

Endpoints:
    POST /v1/errors - Submit an error report

Environment Variables:
    DATABASE_URL - PostgreSQL connection string
    RATE_LIMIT_PER_IP - Max requests per IP per window (default: 100)
    RATE_LIMIT_PER_USER - Max requests per user per window (default: 50)
    RATE_WINDOW_SECONDS - Time window for rate limiting (default: 60)
    HOST - Server host (default: 0.0.0.0)
    PORT - Server port (default: 8080)
"""

import argparse
import hashlib
import logging
import os
import sys
import time
from collections import defaultdict
from threading import Lock
from typing import Any, Optional

# Lazy imports for optional dependencies
FastAPI: Optional[Any] = None
HTTPException: Optional[Any] = None
Request: Optional[Any] = None
BaseModel: Optional[Any] = None
psycopg2: Optional[Any] = None

logger = logging.getLogger(__name__)


def _lazy_imports() -> None:
    """Lazily import optional dependencies to meet vendor constraints."""
    global FastAPI, HTTPException, Request, BaseModel, psycopg2
    if FastAPI is None:
        import psycopg2
        from fastapi import FastAPI, HTTPException, Request
        from pydantic import BaseModel
        # Store in module globals
        globals()['FastAPI'] = FastAPI
        globals()['HTTPException'] = HTTPException
        globals()['Request'] = Request
        globals()['BaseModel'] = BaseModel
        globals()['psycopg2'] = psycopg2


class ErrorReport(BaseModel):
    """Schema for incoming error reports."""
    severity: str
    source: str
    user_id: str
    error_class: str
    traceback: str
    context_json: Optional[str] = None


class ErrorStore:
    """PostgreSQL-backed error storage with deduplication."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._conn = None

    def _get_connection(self):
        """Get or create database connection."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.database_url)
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        """Initialize database schema if not exists."""
        conn = self._conn
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS error_reports (
                id SERIAL PRIMARY KEY,
                severity VARCHAR(50) NOT NULL,
                source VARCHAR(255) NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                error_class VARCHAR(255) NOT NULL,
                traceback_hash VARCHAR(64) NOT NULL,
                traceback TEXT NOT NULL,
                context_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_error_reports_traceback_hash
            ON error_reports(traceback_hash)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_error_reports_user_id
            ON error_reports(user_id)
        """)
        conn.commit()
        cur.close()

    def insert(self, report: ErrorReport, traceback_hash: str) -> bool:
        """
        Insert error report if not duplicate.

        Args:
            report: The error report to insert
            traceback_hash: SHA256 hash of the traceback

        Returns:
            True if inserted, False if duplicate
        """
        conn = self._get_connection()
        cur = conn.cursor()

        # Check for duplicate
        cur.execute(
            "SELECT 1 FROM error_reports WHERE traceback_hash = %s LIMIT 1",
            (traceback_hash,)
        )
        if cur.fetchone():
            cur.close()
            return False

        # Insert new report
        cur.execute(
            """
            INSERT INTO error_reports
            (severity, source, user_id, error_class, traceback_hash, traceback, context_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                report.severity,
                report.source,
                report.user_id,
                report.error_class,
                traceback_hash,
                report.traceback,
                report.context_json,
            )
        )
        conn.commit()
        cur.close()
        return True

    def close(self) -> None:
        """Close database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()


class RateLimiter:
    """In-memory rate limiter for IP and user-based limiting."""

    def __init__(self, per_ip: int, per_user: int, window_seconds: int):
        self.per_ip = per_ip
        self.per_user = per_user
        self.window_seconds = window_seconds
        self._ip_requests: dict[str, list[float]] = defaultdict(list)
        self._user_requests: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def _clean_old_requests(
        self, requests: list[float], now: float
    ) -> list[float]:
        """Remove requests outside the time window."""
        cutoff = now - self.window_seconds
        return [ts for ts in requests if ts > cutoff]

    def check(self, ip: str, user_id: str) -> tuple[bool, str]:
        """
        Check if request is allowed.

        Args:
            ip: Client IP address
            user_id: User identifier

        Returns:
            Tuple of (allowed, message)
        """
        now = time.time()

        with self._lock:
            # Check IP rate limit
            ip_requests = self._clean_old_requests(
                self._ip_requests[ip].copy(), now
            )
            if len(ip_requests) >= self.per_ip:
                return False, f"Rate limit exceeded for IP: {self.per_ip} requests per {self.window_seconds}s"
            self._ip_requests[ip] = ip_requests + [now]

            # Check user rate limit
            user_requests = self._clean_old_requests(
                self._user_requests[user_id].copy(), now
            )
            if len(user_requests) >= self.per_user:
                return False, f"Rate limit exceeded for user: {self.per_user} requests per {self.window_seconds}s"
            self._user_requests[user_id] = user_requests + [now]

        return True, ""


def compute_traceback_hash(traceback: str) -> str:
    """
    Compute SHA256 hash of traceback for deduplication.

    Args:
        traceback: The traceback string to hash

    Returns:
        Hex-encoded SHA256 hash
    """
    return hashlib.sha256(traceback.encode('utf-8')).hexdigest()


def get_client_ip(request: Request) -> str:
    """
    Extract client IP from request, handling proxies.

    Args:
        request: FastAPI request object

    Returns:
        Client IP address string
    """
    # Check for forwarded header (reverse proxy)
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'


def create_app(
    database_url: str,
    rate_limit_per_ip: int = 100,
    rate_limit_per_user: int = 50,
    rate_window_seconds: int = 60,
) -> Any:
    """
    Create and configure FastAPI application.

    Args:
        database_url: PostgreSQL connection string
        rate_limit_per_ip: Max requests per IP per window
        rate_limit_per_user: Max requests per user per window
        rate_window_seconds: Time window for rate limiting

    Returns:
        Configured FastAPI application
    """
    _lazy_imports()

    app = FastAPI(
        title="Error Reporting Service",
        description="Receive and store error reports with rate limiting and deduplication",
        version="1.0.0",
    )

    store = ErrorStore(database_url)
    limiter = RateLimiter(
        per_ip=rate_limit_per_ip,
        per_user=rate_limit_per_user,
        window_seconds=rate_window_seconds,
    )

    @app.on_event("shutdown")
    def shutdown_event() -> None:
        """Clean up resources on shutdown."""
        store.close()

    @app.post("/v1/errors", status_code=201)
    def submit_error(request: Request, report: ErrorReport) -> dict[str, Any]:
        """
        Submit an error report.

        Performs rate limiting per IP and user, deduplicates by traceback hash,
        and persists to PostgreSQL.

        Args:
            request: FastAPI request object
            report: Error report data

        Returns:
            Success response with report ID

        Raises:
            HTTPException: If rate limit exceeded or validation fails
        """
        client_ip = get_client_ip(request)

        # Rate limiting
        allowed, message = limiter.check(client_ip, report.user_id)
        if not allowed:
            raise HTTPException(status_code=429, detail=message)

        # Compute dedup hash
        traceback_hash = compute_traceback_hash(report.traceback)

        # Insert with dedup
        inserted = store.insert(report, traceback_hash)

        return {
            "status": "accepted" if inserted else "duplicate",
            "traceback_hash": traceback_hash,
            "message": "Error report recorded" if inserted else "Duplicate error ignored",
        }

    @app.get("/health")
    def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    return app


def parse_args(argv: list[str]) -> argparse.Namespace:
    """
    Parse command line arguments.

    Args:
        argv: Command line arguments

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="Error Reporting Service - FastAPI server for error collection"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Server host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="Server port (default: 8080)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL connection string",
    )
    parser.add_argument(
        "--rate-limit-per-ip",
        type=int,
        default=int(os.environ.get("RATE_LIMIT_PER_IP", "100")),
        help="Max requests per IP per window (default: 100)",
    )
    parser.add_argument(
        "--rate-limit-per-user",
        type=int,
        default=int(os.environ.get("RATE_LIMIT_PER_USER", "50")),
        help="Max requests per user per window (default: 50)",
    )
    parser.add_argument(
        "--rate-window-seconds",
        type=int,
        default=int(os.environ.get("RATE_WINDOW_SECONDS", "60")),
        help="Time window for rate limiting in seconds (default: 60)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """
    Main entry point for the error reporting service.

    Args:
        argv: Command line arguments

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    args = parse_args(argv)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Validate required arguments
    if not args.database_url:
        logger.error("DATABASE_URL is required")
        print("Error: --database-url or DATABASE_URL environment variable is required",
              file=sys.stderr)
        return 1

    logger.info(
        f"Starting Error Reporting Service on {args.host}:{args.port}"
    )
    logger.info(
        f"Rate limits - IP: {args.rate_limit_per_ip}, User: {args.rate_limit_per_user}, "
        f"Window: {args.rate_window_seconds}s"
    )

    try:
        app = create_app(
            database_url=args.database_url,
            rate_limit_per_ip=args.rate_limit_per_ip,
            rate_limit_per_user=args.rate_limit_per_user,
            rate_window_seconds=args.rate_window_seconds,
        )

        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port)
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        return 0
    except Exception as e:
        logger.error(f"Server error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
