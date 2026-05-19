#!/usr/bin/env python3
"""
G203 · Buyer Signup Flow
========================
Buyer signup: company info + sales contact + JWT issuance + sample-clip
download grant; writes buyers row + audit log.

Usage:
    python3 bin/buyer_signup_flow.py --company-name "Acme" --company-address \
        "123 Main" --company-city "Springfield" --company-state "IL" \
        --company-zip "62701" --company-country "US" --contact-first "Jane" \
        --contact-last "Doe" --contact-email "jane@acme.com"
"""

import argparse
import datetime
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import uuid
from base64 import urlsafe_b64encode
from typing import Any, Dict, List, Optional

DB_PATH_ENV = "G203_BUYERS_DB"
DB_PATH_DEFAULT = "data/buyers.db"
AUDIT_PATH_ENV = "G203_AUDIT_LOG"
AUDIT_PATH_DEFAULT = "data/audit.log"
JWT_SECRET_ENV = "G203_JWT_SECRET"
JWT_SECRET_DEFAULT = "dev-secret-change-in-prod"
SAMPLE_CLIP_ID = "sample-clip-001"
TOKEN_EXPIRY_HOURS = 24

logger = logging.getLogger("g203.buyer_signup")


class CompanyInfo:
    """Company information for buyer signup."""

    def __init__(self, name: str, address: str, city: str, state: str,
                 zip_code: str, country: str, tax_id: Optional[str] = None) -> None:
        self.name, self.address, self.city = name, address, city
        self.state, self.zip_code, self.country = state, zip_code, country
        self.tax_id = tax_id

    def to_dict(self) -> Dict[str, Any]:
        """Convert company information to a dictionary representation.

        Returns:
            Dict containing name, address, city, state, zip_code, country,
            and optional tax_id fields.
        """
        return {"name": self.name, "address": self.address, "city": self.city,
                "state": self.state, "zip_code": self.zip_code,
                "country": self.country, "tax_id": self.tax_id}

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.name or len(self.name.strip()) < 2:
            errors.append("Company name must be at least 2 characters")
        for label, value in [("Address", self.address), ("City", self.city),
                             ("State", self.state), ("ZIP", self.zip_code),
                             ("Country", self.country)]:
            if not value or not value.strip():
                errors.append(f"{label} is required")
        return errors


class SalesContact:
    """Sales contact information for a buyer."""

    def __init__(self, first_name: str, last_name: str, email: str,
                 phone: Optional[str] = None, title: Optional[str] = None) -> None:
        self.first_name, self.last_name, self.email = first_name, last_name, email
        self.phone, self.title = phone, title

    def to_dict(self) -> Dict[str, Any]:
        return {"first_name": self.first_name, "last_name": self.last_name,
                "email": self.email, "phone": self.phone, "title": self.title}

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.first_name or not self.first_name.strip():
            errors.append("First name is required")
        if not self.last_name or not self.last_name.strip():
            errors.append("Last name is required")
        if not self.email or "@" not in self.email:
            errors.append("Valid email is required")
        return errors


def _b64url(data: bytes) -> str:
    """URL-safe base64 encode without padding."""
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_jwt(buyer_id: str, company_name: str, email: str,
                 secret: str = JWT_SECRET_DEFAULT) -> str:
    """Generate a minimal HS256 JWT token for the buyer."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = datetime.datetime.utcnow()
    expiry = now + datetime.timedelta(hours=TOKEN_EXPIRY_HOURS)
    payload = {
        "sub": buyer_id, "company": company_name, "email": email,
        "iat": int(now.timestamp()), "exp": int(expiry.timestamp()),
        "jti": str(uuid.uuid4()), "scope": ["buyer", "sample-clip:download"],
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def _ensure_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite buyers database and ensure schema."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS buyers (
            buyer_id TEXT PRIMARY KEY, company_name TEXT NOT NULL,
            company_addr TEXT, company_city TEXT, company_state TEXT,
            company_zip TEXT, company_country TEXT, company_tax_id TEXT,
            contact_first TEXT NOT NULL, contact_last TEXT NOT NULL,
            contact_email TEXT NOT NULL, contact_phone TEXT,
            contact_title TEXT, jwt_token TEXT,
            sample_clip_granted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.commit()
    return conn


def insert_buyer(conn: sqlite3.Connection, buyer_id: str,
                 company: CompanyInfo, contact: SalesContact,
                 jwt_token: str) -> None:
    """Insert a new buyer row into the database."""
    now = datetime.datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO buyers (buyer_id, company_name, company_addr, company_city,
            company_state, company_zip, company_country, company_tax_id,
            contact_first, contact_last, contact_email, contact_phone,
            contact_title, jwt_token, sample_clip_granted, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
        (buyer_id, company.name, company.address, company.city,
         company.state, company.zip_code, company.country, company.tax_id,
         contact.first_name, contact.last_name, contact.email,
         contact.phone, contact.title, jwt_token, now, now))
    conn.commit()


def write_audit(audit_path: str, buyer_id: str, action: str,
                details: Optional[Dict[str, Any]] = None) -> None:
    """Append a JSON-lines audit record."""
    os.makedirs(os.path.dirname(audit_path) or ".", exist_ok=True)
    record = {"timestamp": datetime.datetime.utcnow().isoformat() + "Z",
              "buyer_id": buyer_id, "action": action, "details": details or {}}
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def grant_sample_clip(conn: sqlite3.Connection, buyer_id: str,
                      clip_id: str = SAMPLE_CLIP_ID) -> None:
    """Mark the sample-clip download grant for the buyer."""
    conn.execute("UPDATE buyers SET sample_clip_granted=1, updated_at=? "
                 "WHERE buyer_id=?",
                 (datetime.datetime.utcnow().isoformat() + "Z", buyer_id))
    conn.commit()
    logger.info("Granted sample-clip '%s' to buyer '%s'", clip_id, buyer_id)


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI parser."""
    p = argparse.ArgumentParser(
        prog="buyer_signup_flow",
        description="G203 Buyer Signup: register company + contact, issue JWT, grant sample clip.")
    p.add_argument("--company-name", required=True, help="Company legal name")
    p.add_argument("--company-address", required=True, help="Street address")
    p.add_argument("--company-city", required=True, help="City")
    p.add_argument("--company-state", required=True, help="State / province")
    p.add_argument("--company-zip", required=True, help="ZIP / postal code")
    p.add_argument("--company-country", required=True, help="ISO country code")
    p.add_argument("--company-tax-id", default=None, help="Tax ID (optional)")
    p.add_argument("--contact-first", required=True, help="Contact first name")
    p.add_argument("--contact-last", required=True, help="Contact last name")
    p.add_argument("--contact-email", required=True, help="Contact email")
    p.add_argument("--contact-phone", default=None, help="Contact phone (optional)")
    p.add_argument("--contact-title", default=None, help="Contact title (optional)")
    p.add_argument("--db-path", default=None, help="SQLite DB path (env: G203_BUYERS_DB)")
    p.add_argument("--audit-path", default=None, help="Audit log path (env: G203_AUDIT_LOG)")
    p.add_argument("--jwt-secret", default=None, help="JWT signing secret (env: G203_JWT_SECRET)")
    p.add_argument("--dry-run", action="store_true", help="Validate only, do not persist")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """
    Entry point for the buyer signup flow.
    Returns 0 on success, 1 on validation error, 2 on runtime error.
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    db_path = args.db_path or os.environ.get(DB_PATH_ENV, DB_PATH_DEFAULT)
    audit_path = args.audit_path or os.environ.get(AUDIT_PATH_ENV, AUDIT_PATH_DEFAULT)
    jwt_secret = args.jwt_secret or os.environ.get(JWT_SECRET_ENV, JWT_SECRET_DEFAULT)

    company = CompanyInfo(args.company_name, args.company_address,
                          args.company_city, args.company_state,
                          args.company_zip, args.company_country,
                          args.company_tax_id)
    contact = SalesContact(args.contact_first, args.contact_last,
                           args.contact_email, args.contact_phone,
                           args.contact_title)

    errors = company.validate() + contact.validate()
    if errors:
        for err in errors:
            logger.error("Validation: %s", err)
        return 1

    buyer_id = str(uuid.uuid4())
    logger.info("Buyer signup initiated: id=%s company='%s'", buyer_id, company.name)

    if args.dry_run:
        jwt_token = generate_jwt(buyer_id, company.name, contact.email, jwt_secret)
        logger.info("Dry-run JWT: %s", jwt_token)
        return 0

    try:
        conn = _ensure_db(db_path)
        jwt_token = generate_jwt(buyer_id, company.name, contact.email, jwt_secret)
        insert_buyer(conn, buyer_id, company, contact, jwt_token)
        grant_sample_clip(conn, buyer_id)
        conn.close()

        write_audit(audit_path, buyer_id, "buyer_signup", {
            "company": company.to_dict(), "contact": contact.to_dict(),
            "sample_clip_id": SAMPLE_CLIP_ID})

        logger.info("Buyer signup complete: id=%s", buyer_id)
        print(json.dumps({"buyer_id": buyer_id, "jwt_token": jwt_token,
                          "sample_clip_id": SAMPLE_CLIP_ID}))
        return 0
    except Exception as exc:
        logger.error("Signup failed: %s", exc, exc_info=True)
        write_audit(audit_path, buyer_id, "signup_error", {"error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
