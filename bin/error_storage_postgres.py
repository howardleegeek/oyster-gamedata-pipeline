#!/usr/bin/env python3
"""
error_storage_postgres.py — Alembic migration + SQLAlchemy models for error storage.

Schema: id, timestamp, severity, source(recorder/cli/test/cluster), user_id,
        error_class, traceback_text, context_jsonb, sha256_dedup_key (indexed).
Retention: 90 days (configurable).

Usage:
    python3 bin/error_storage_postgres.py --db-url postgresql://... init
    python3 bin/error_storage_postgres.py --db-url ... insert --severity error --source cli --error-class ValueError
    python3 bin/error_storage_postgres.py --db-url ... query --severity error --limit 50
    python3 bin/error_storage_postgres.py --db-url ... purge --retention-days 90
    python3 bin/error_storage_postgres.py --db-url ... stats
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS: int = 90
VALID_SOURCES: List[str] = ["recorder", "cli", "test", "cluster"]
VALID_SEVERITIES: List[str] = ["debug", "info", "warning", "error", "critical"]


def _sa():
    """Lazy-import sqlalchemy."""
    import sqlalchemy as sa
    return sa


def _orm():
    """Lazy-import sqlalchemy.orm."""
    from sqlalchemy import orm
    return orm


def _sessionmaker():
    """Lazy-import sessionmaker."""
    from sqlalchemy.orm import sessionmaker
    return sessionmaker


def _build_model():
    """Construct declarative base + Error model."""
    sa, orm = _sa(), _orm()
    Base = orm.declarative_base()

    class Error(Base):  # type: ignore[valid-type]
        """ORM model for the ``errors`` table."""
        __tablename__ = "errors"
        id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
        timestamp = sa.Column(sa.DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(timezone.utc))
        severity = sa.Column(sa.String(16), nullable=False)
        source = sa.Column(sa.String(32), nullable=False)
        user_id = sa.Column(sa.String(128), nullable=True)
        error_class = sa.Column(sa.String(256), nullable=False)
        traceback_text = sa.Column(sa.Text, nullable=True)
        context_jsonb = sa.Column(sa.JSON, nullable=True, default=dict)
        sha256_dedup_key = sa.Column(sa.String(64), nullable=False, index=True)
        __table_args__ = (
            sa.Index("ix_errors_timestamp", "timestamp"),
            sa.Index("ix_errors_severity_source", "severity", "source"),
            sa.Index("ix_errors_user_id", "user_id"),
        )

        def __repr__(self) -> str:
            return (f"<Error(id={self.id}, severity={self.severity!r}, "
                    f"source={self.source!r}, error_class={self.error_class!r})>")

        def to_dict(self) -> Dict[str, Any]:
            """Serialise record to plain dict."""
            return {
                "id": self.id,
                "timestamp": self.timestamp.isoformat() if self.timestamp else None,
                "severity": self.severity, "source": self.source,
                "user_id": self.user_id, "error_class": self.error_class,
                "traceback_text": self.traceback_text,
                "context_jsonb": self.context_jsonb,
                "sha256_dedup_key": self.sha256_dedup_key,
            }

    return Base, Error


def compute_dedup_key(error_class: str, traceback_text: str,
                      context: Optional[Dict] = None) -> str:
    """SHA-256 hex digest for deduplication."""
    payload = json.dumps(
        {"error_class": error_class, "traceback_text": traceback_text,
         "context": context or {}}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_engine(db_url: str):
    """Create SQLAlchemy engine with pool_pre_ping."""
    return _sa().create_engine(db_url, pool_pre_ping=True)


def get_session(db_url: str):
    """Return a new bound session."""
    return _sessionmaker()(bind=create_engine(db_url))


# --- Alembic migration helpers ---

def alembic_upgrade(db_url: str) -> None:
    """Create all tables (equivalent to ``alembic upgrade head``)."""
    _, Error = _build_model()
    Error.metadata.create_all(create_engine(db_url))


def alembic_downgrade(db_url: str) -> None:
    """Drop the errors table (equivalent to ``alembic downgrade base``)."""
    _, Error = _build_model()
    Error.metadata.drop_all(create_engine(db_url))


# --- CRUD ---

def insert_error(db_url: str, severity: str, source: str, error_class: str,
                 traceback_text: str = "", user_id: Optional[str] = None,
                 context: Optional[Dict[str, Any]] = None,
                 timestamp: Optional[datetime] = None) -> int:
    """Insert a single error record; returns new row id."""
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"severity must be one of {VALID_SEVERITIES}")
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}")
    _, Error = _build_model()
    sess = get_session(db_url)
    try:
        rec = Error(timestamp=timestamp or datetime.now(timezone.utc),
                    severity=severity, source=source, user_id=user_id,
                    error_class=error_class, traceback_text=traceback_text,
                    context_jsonb=context or {},
                    sha256_dedup_key=compute_dedup_key(error_class, traceback_text, context))
        sess.add(rec)
        sess.commit()
        return rec.id  # type: ignore[return-value]
    except Exception as e:
        sess.rollback()
        logger.debug("insert_error: db_url=%s, error_class=%s, exception=%r", db_url, error_class, e)
        raise
    finally:
        sess.close()


def query_errors(db_url: str, severity: Optional[str] = None,
                 source: Optional[str] = None, user_id: Optional[str] = None,
                 dedup_key: Optional[str] = None,
                 limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """Query error records with optional filters; returns list of dicts."""
    _, Error = _build_model()
    sess = get_session(db_url)
    try:
        stmt = sess.query(Error)
        if severity:
            stmt = stmt.filter(Error.severity == severity)
        if source:
            stmt = stmt.filter(Error.source == source)
        if user_id:
            stmt = stmt.filter(Error.user_id == user_id)
        if dedup_key:
            stmt = stmt.filter(Error.sha256_dedup_key == dedup_key)
        stmt = stmt.order_by(Error.timestamp.desc()).limit(limit).offset(offset)
        return [r.to_dict() for r in stmt.all()]
    finally:
        sess.close()


def purge_old_errors(db_url: str,
                     retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete records older than *retention_days*; returns count deleted."""
    _, Error = _build_model()
    sess = get_session(db_url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    try:
        deleted = sess.query(Error).filter(Error.timestamp < cutoff).delete()
        sess.commit()
        return deleted  # type: ignore[return-value]
    except Exception as e:
        sess.rollback()
        logger.debug("purge_old_errors: db_url=%s, retention_days=%s, exception=%r", db_url, retention_days, e)
        raise
    finally:
        sess.close()


def get_stats(db_url: str) -> Dict[str, Any]:
    """Return aggregate stats: total_count, oldest, newest, by_severity, by_source."""
    sa = _sa()
    _, Error = _build_model()
    sess = get_session(db_url)
    try:
        total = sess.query(Error).count()
        oldest = sess.query(Error.timestamp).order_by(Error.timestamp.asc()).first()
        newest = sess.query(Error.timestamp).order_by(Error.timestamp.desc()).first()
        sev = {s: c for s, c in sess.query(Error.severity, sa.func.count(Error.id))
               .group_by(Error.severity).all()}
        src = {s: c for s, c in sess.query(Error.source, sa.func.count(Error.id))
               .group_by(Error.source).all()}
        return {"total_count": total,
                "oldest": oldest[0].isoformat() if oldest else None,
                "newest": newest[0].isoformat() if newest else None,
                "by_severity": sev, "by_source": src}
    finally:
        sess.close()


# --- CLI ---

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="error_storage_postgres",
                                description="PostgreSQL error storage — Alembic + CRUD CLI.")
    p.add_argument("--db-url", required=True, help="SQLAlchemy database URL.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create errors table.").set_defaults(_handler=_cmd_init)
    sub.add_parser("downgrade", help="Drop errors table.").set_defaults(_handler=_cmd_down)

    pi = sub.add_parser("insert", help="Insert error record.")
    pi.add_argument("--severity", required=True, choices=VALID_SEVERITIES)
    pi.add_argument("--source", required=True, choices=VALID_SOURCES)
    pi.add_argument("--error-class", required=True)
    pi.add_argument("--traceback", default="")
    pi.add_argument("--user-id", default=None)
    pi.add_argument("--context", default="{}")
    pi.set_defaults(_handler=_cmd_insert)

    pq = sub.add_parser("query", help="Query errors.")
    pq.add_argument("--severity", default=None, choices=VALID_SEVERITIES)
    pq.add_argument("--source", default=None, choices=VALID_SOURCES)
    pq.add_argument("--user-id", default=None)
    pq.add_argument("--dedup-key", default=None)
    pq.add_argument("--limit", type=int, default=100)
    pq.add_argument("--offset", type=int, default=0)
    pq.set_defaults(_handler=_cmd_query)

    pp = sub.add_parser("purge", help="Purge old errors.")
    pp.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    pp.set_defaults(_handler=_cmd_purge)

    sub.add_parser("stats", help="Show stats.").set_defaults(_handler=_cmd_stats)
    return p


def _cmd_init(a: argparse.Namespace) -> int:
    alembic_upgrade(a.db_url)
    print("errors table created.")
    return 0


def _cmd_down(a: argparse.Namespace) -> int:
    alembic_downgrade(a.db_url)
    print("errors table dropped.")
    return 0


def _cmd_insert(a: argparse.Namespace) -> int:
    rid = insert_error(a.db_url, a.severity, a.source, a.error_class,
                       a.traceback, a.user_id, json.loads(a.context))
    print(f"inserted error id={rid}")
    return 0


def _cmd_query(a: argparse.Namespace) -> int:
    rows = query_errors(a.db_url, a.severity, a.source, a.user_id,
                        a.dedup_key, a.limit, a.offset)
    print(json.dumps(rows, indent=2, default=str))
    return 0


def _cmd_purge(a: argparse.Namespace) -> int:
    n = purge_old_errors(a.db_url, a.retention_days)
    print(f"deleted {n} records older than {a.retention_days} days.")
    return 0


def _cmd_stats(a: argparse.Namespace) -> int:
    print(json.dumps(get_stats(a.db_url), indent=2, default=str))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; returns exit code."""
    args = _build_parser().parse_args(argv)
    try:
        return args._handler(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
