#!/usr/bin/env python3
"""
G221 · bin/consent_log_signed.py

Legally-binding signed consent log: HMAC-signed entry per recording session
capturing user_id + game + timestamp + opt-in version; serves GDPR/CCPA/COPPA.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


class ConsentLogError(Exception):
    """Base exception for consent log operations."""

    pass


class ConsentEntry:
    """Represents a single consent log entry with HMAC signature."""

    def __init__(
        self,
        user_id: str,
        game: str,
        timestamp: Optional[str] = None,
        opt_in_version: str = "1.0",
        signature: Optional[str] = None,
    ):
        self.user_id = user_id
        self.game = game
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.opt_in_version = opt_in_version
        self.signature = signature

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "game": self.game,
            "timestamp": self.timestamp,
            "opt_in_version": self.opt_in_version,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsentEntry":
        return cls(
            user_id=data["user_id"],
            game=data["game"],
            timestamp=data["timestamp"],
            opt_in_version=data["opt_in_version"],
            signature=data.get("signature"),
        )


class ConsentLogSigned:
    """
    HMAC-signed consent log for recording user consent sessions.
    Provides tamper-evident storage for GDPR/CCPA/COPPA compliance.
    """

    MIN_KEY_LENGTH = 16

    def __init__(
        self,
        log_file: Union[str, Path],
        secret_key: Optional[bytes] = None,
        key_file: Optional[Union[str, Path]] = None,
    ):
        self.log_file = Path(log_file)
        self.key_file = Path(key_file) if key_file else None

        if secret_key is not None:
            self.secret_key = secret_key
        elif self.key_file and self.key_file.exists():
            self.secret_key = self._load_key()
        else:
            self.secret_key = os.urandom(32)
            if self.key_file:
                self._save_key()

        self._validate_key()
        self._ensure_log_file()

    def _validate_key(self) -> None:
        if len(self.secret_key) < self.MIN_KEY_LENGTH:
            raise ConsentLogError(f"Secret key must be at least {self.MIN_KEY_LENGTH} bytes")

    def _load_key(self) -> bytes:
        try:
            return base64.b64decode(self.key_file.read_text().strip())
        except Exception as e:
            raise ConsentLogError(f"Failed to load key file: {e}")

    def _save_key(self) -> None:
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key_file.write_text(base64.b64encode(self.secret_key).decode())
        self.key_file.chmod(0o600)

    def _ensure_log_file(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_file.exists():
            self._write_entries([])

    def _compute_signature(self, entry: ConsentEntry) -> str:
        """Compute HMAC-SHA256 signature for a consent entry."""
        canonical = f"{entry.user_id}|{entry.game}|{entry.timestamp}|{entry.opt_in_version}"
        signature = hmac.new(self.secret_key, canonical.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(signature).decode()

    def _read_entries(self) -> List[ConsentEntry]:
        try:
            data = json.loads(self.log_file.read_text())
            return [ConsentEntry.from_dict(e) for e in data.get("entries", [])]
        except json.JSONDecodeError as e:
            raise ConsentLogError(f"Invalid log file format: {e}")

    def _write_entries(self, entries: List[ConsentEntry]) -> None:
        data = {
            "version": "1.0",
            "created": datetime.now(timezone.utc).isoformat(),
            "entries": [e.to_dict() for e in entries],
        }
        self.log_file.write_text(json.dumps(data, indent=2))

    def add_entry(
        self, user_id: str, game: str, opt_in_version: str = "1.0", timestamp: Optional[str] = None
    ) -> ConsentEntry:
        """Add a new signed consent entry to the log."""
        entry = ConsentEntry(
            user_id=user_id, game=game, timestamp=timestamp, opt_in_version=opt_in_version
        )
        entry.signature = self._compute_signature(entry)
        entries = self._read_entries()
        entries.append(entry)
        self._write_entries(entries)
        return entry

    def verify_entry(self, entry: ConsentEntry) -> bool:
        """Verify the signature of a consent entry."""
        if not entry.signature:
            return False
        expected = self._compute_signature(entry)
        return hmac.compare_digest(entry.signature, expected)

    def verify_all(self) -> Tuple[int, List[ConsentEntry]]:
        """Verify all entries. Returns (valid_count, list of invalid entries)."""
        entries = self._read_entries()
        invalid = [e for e in entries if not self.verify_entry(e)]
        return len(entries) - len(invalid), invalid

    def get_entries_by_user(self, user_id: str) -> List[ConsentEntry]:
        return [e for e in self._read_entries() if e.user_id == user_id]

    def get_entries_by_game(self, game: str) -> List[ConsentEntry]:
        return [e for e in self._read_entries() if e.game == game]


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the consent log CLI."""
    parser = argparse.ArgumentParser(
        description="Manage HMAC-signed consent logs for GDPR/CCPA/COPPA compliance"
    )
    parser.add_argument("-l", "--log-file", default="consent_log.json", help="Log file path")
    parser.add_argument("-k", "--key-file", help="Secret key file path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new consent entry")
    add_parser.add_argument("-u", "--user-id", required=True, help="User ID")
    add_parser.add_argument("-g", "--game", required=True, help="Game identifier")
    add_parser.add_argument("-v", "--version", default="1.0", help="Opt-in version")

    subparsers.add_parser("verify", help="Verify all log entries")

    list_parser = subparsers.add_parser("list", help="List log entries")
    list_parser.add_argument("-u", "--user-id", help="Filter by user ID")
    list_parser.add_argument("-g", "--game", help="Filter by game")

    args = parser.parse_args(argv)

    # Read-only commands require an existing log file; only `add` may create one.
    if args.command in ("list", "verify") and not Path(args.log_file).exists():
        print(
            f"Error: log file '{args.log_file}' does not exist; use 'add' to create it",
            file=sys.stderr,
        )
        return 1

    try:
        log = ConsentLogSigned(log_file=args.log_file, key_file=getattr(args, "key_file", None))

        if args.command == "add":
            entry = log.add_entry(user_id=args.user_id, game=args.game, opt_in_version=args.version)
            print("Added consent entry:")
            print(f"  User: {entry.user_id}")
            print(f"  Game: {entry.game}")
            print(f"  Timestamp: {entry.timestamp}")
            print(f"  Version: {entry.opt_in_version}")
            print(f"  Signature: {entry.signature[:16]}...")
            return 0

        elif args.command == "verify":
            valid_count, invalid = log.verify_all()
            print(f"Verified {valid_count} valid entries")
            if invalid:
                print(f"WARNING: {len(invalid)} invalid entries found!")
                for e in invalid:
                    print(f"  - {e.user_id}/{e.game} at {e.timestamp}")
                return 1
            return 0

        elif args.command == "list":
            entries = log._read_entries()
            if args.user_id:
                entries = [e for e in entries if e.user_id == args.user_id]
            if args.game:
                entries = [e for e in entries if e.game == args.game]
            for e in entries:
                sig_status = "✓" if log.verify_entry(e) else "✗"
                print(f"{sig_status} {e.timestamp} | {e.user_id} | {e.game} | v{e.opt_in_version}")
            return 0

    except ConsentLogError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
