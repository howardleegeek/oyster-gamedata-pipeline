"""
backend_stub.tester_invite – tester application & approval workflow.

Endpoints:
  POST /api/v1/testers/apply          → apply for beta access
  GET  /api/v1/testers                → list all applicants (admin)
  POST /api/v1/testers/{id}/approve   → approve + return signed download URL
  POST /api/v1/testers/{id}/reject    → reject application

Data lives in memory by default.  The backend can install a persistence hook
to save tester state after each write.  No SMTP – the CLI prints a
ready-to-send email.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DOWNLOAD_BASE_URL: str = "https://dl.example.com/beta"
EMAIL_REGEX: str = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

VALID_STATUSES = {"pending", "approved", "rejected"}


@dataclass
class TesterRecord:
    """In-memory representation of a single tester applicant."""

    tester_id: str
    email: str
    discord_user: str
    why_interested: str
    status: str = "pending"
    applied_at: Optional[str] = None
    approved_at: Optional[str] = None
    download_url: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TesterRecord":
        status = str(data.get("status", "pending"))
        if status not in VALID_STATUSES:
            status = "pending"
        return cls(
            tester_id=str(data["tester_id"]),
            email=str(data["email"]),
            discord_user=str(data["discord_user"]),
            why_interested=str(data.get("why_interested", "")),
            status=status,
            applied_at=data.get("applied_at"),
            approved_at=data.get("approved_at"),
            download_url=data.get("download_url"),
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "tester_id": self.tester_id,
            "email": self.email,
            "discord_user": self.discord_user,
            "status": self.status,
            "applied_at": self.applied_at,
            "why_interested": self.why_interested,
        }
        if self.approved_at:
            d["approved_at"] = self.approved_at
        if self.download_url:
            d["download_url"] = self.download_url
        return d


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class TesterStore:
    """Thread-safe in-memory dict store for tester applicants."""

    def __init__(self) -> None:
        self._store: Dict[str, TesterRecord] = {}
        self._lock = RLock()
        self._on_change: Optional[Callable[[], None]] = None

    def set_change_hook(self, hook: Optional[Callable[[], None]]) -> None:
        self._on_change = hook

    def mark_changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def add(self, record: TesterRecord) -> None:
        with self._lock:
            self._store[record.tester_id] = record
        self.mark_changed()

    def get(self, tester_id: str) -> Optional[TesterRecord]:
        with self._lock:
            return self._store.get(tester_id)

    def list_all(self) -> List[TesterRecord]:
        with self._lock:
            return list(self._store.values())

    def to_dicts(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.list_all()]

    def replace_all(self, records: list[dict[str, Any]]) -> None:
        restored: Dict[str, TesterRecord] = {}
        for item in records:
            record = TesterRecord.from_dict(item)
            restored[record.tester_id] = record
        with self._lock:
            self._store = restored

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
        self.mark_changed()


# Global store instance (replaced in tests)
_tester_store = TesterStore()


def get_store() -> TesterStore:
    return _tester_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_admin_token() -> str:
    """Read admin token from env at call time (so monkeypatch works in tests)."""
    return os.environ.get("TESTER_ADMIN_TOKEN", "dev-admin-token")


def _validate_email(email: str) -> bool:
    """Return True if *email* matches a basic RFC-5322-ish pattern."""
    return bool(re.match(EMAIL_REGEX, email))


def _require_admin(authorization: str | None) -> None:
    """Raise 401/403 if the caller is not an admin."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")
    token = authorization[7:]
    if token != _get_admin_token():
        raise HTTPException(status_code=403, detail="Admin token required")


def _generate_signed_url(tester_id: str) -> str:
    """Return a mock signed download URL for an approved tester."""
    sig = uuid.uuid4().hex[:16]
    return f"{DOWNLOAD_BASE_URL}/installer?tester={tester_id}&sig={sig}"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/testers", tags=["testers"])


@router.post("/apply")
async def apply_tester(request: Request) -> Dict[str, Any]:
    """Submit a tester application.

    Body: { "email": str, "discord_user": str, "why_interested": str }
    """
    body = await request.json()

    email = body.get("email", "").strip()
    discord_user = body.get("discord_user", "").strip()
    why_interested = body.get("why_interested", "").strip()

    # Validation
    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    if not _validate_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    if not discord_user:
        raise HTTPException(status_code=400, detail="discord_user is required")
    if not why_interested:
        raise HTTPException(status_code=400, detail="why_interested is required")

    tester_id = f"tst-{uuid.uuid4().hex[:12]}"
    record = TesterRecord(
        tester_id=tester_id,
        email=email,
        discord_user=discord_user,
        why_interested=why_interested,
        applied_at=datetime.now(timezone.utc).isoformat(),
    )
    get_store().add(record)

    return {"tester_id": tester_id, "status": "pending"}


@router.get("")
async def list_testers(
    authorization: str | None = Header(default=None),
) -> List[Dict[str, Any]]:
    """List all tester applicants (admin only)."""
    _require_admin(authorization)
    return [r.to_dict() for r in get_store().list_all()]


@router.post("/{tester_id}/approve")
async def approve_tester(
    tester_id: str,
    authorization: str | None = Header(default=None),
) -> Dict[str, Any]:
    """Approve a tester application and return a signed download URL."""
    _require_admin(authorization)

    record = get_store().get(tester_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Tester not found")
    if record.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Tester is already {record.status}",
        )

    record.status = "approved"
    record.approved_at = datetime.now(timezone.utc).isoformat()
    record.download_url = _generate_signed_url(tester_id)
    get_store().mark_changed()

    return {
        "tester_id": tester_id,
        "status": "approved",
        "download_url": record.download_url,
    }


@router.post("/{tester_id}/reject")
async def reject_tester(
    tester_id: str,
    authorization: str | None = Header(default=None),
) -> Dict[str, Any]:
    """Reject a tester application."""
    _require_admin(authorization)

    record = get_store().get(tester_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Tester not found")
    if record.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Tester is already {record.status}",
        )

    record.status = "rejected"
    get_store().mark_changed()
    return {"tester_id": tester_id, "status": "rejected"}
