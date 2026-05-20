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
        signature: Optional[str] = None
    ):
        self.user_id = user_id
        self.game = game
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.opt_in_version = opt_in_version
        self.signature = signature
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert consent entry to a dictionary representation.

        Returns:
            Dict containing user_id, game, timestamp, opt_in_version,
            and signature fields.
        """
        return {
            "user_id": self.user_id,
            "game": self.game,
            "timestamp": self.timestamp,
            "opt_in_version": self.opt_in_version,
            "signature": self.signature
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsentEntry":
        """Create a ConsentEntry from a dictionary representation.

        Args:
            data: Dictionary containing user_id, game, timestamp,
                  opt_in_version, and optional signature.

        Returns:
            A new ConsentEntry instance.
        """
        return cls(
            user_id=data["user_id"],
            game=data["game"],
            timestamp=data["timestamp"],
            opt_in_version=data["opt_in_version"],
            signature=data.get("signature")
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
        key_file: Optional[Union[str, Path]] = None
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
            raise ConsentLogError(f"Failed to load key: {e}") from e
    
    def _save_key(self) -> None:
        try:
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            self.key_file.write_text(base64.b64encode(self.secret_key).decode())
        except Exception as e:
            raise ConsentLogError(f"Failed to save key: {e}") from e
    
    def _ensure_log_file(self) -> None:
        if not self.log_file.exists():
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self.log_file.touch()
    
    def _compute_hmac(self, entry: "ConsentEntry") -> str:
        """Compute HMAC-SHA256 signature for a consent entry."""
        message = json.dumps(entry.to_dict(), sort_keys=True)
        signature = hmac.new(
            self.secret_key, message.encode(), hashlib.sha256
        ).hexdigest()
        return signature
    
    def add_entry(self, user_id: str, game: str, opt_in_version: str = "1.0") -> ConsentEntry:
        """Add a new consent entry to the log with HMAC signature."""
        entry = ConsentEntry(
            user_id=user_id,
            game=game,
            opt_in_version=opt_in_version
        )
        entry.signature = self._compute_hmac(entry)
        
        # Append to log file
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")
        
        return entry
    
    def verify_entry(self, entry: ConsentEntry) -> bool:
        """Verify HMAC signature of a consent entry."""
        if not entry.signature:
            return False
        expected_signature = self._compute_hmac(entry)
        return hmac.compare_digest(entry.signature, expected_signature)
    
    def load_entries(self) -> List[ConsentEntry]:
        """Load and verify all consent entries from the log file."""
        entries = []
        if not self.log_file.exists():
            return entries
        
        with open(self.log_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entry = ConsentEntry.from_dict(data)
                    if self.verify_entry(entry):
                        entries.append(entry)
                except (json.JSONDecodeError, KeyError):
                    continue
        return entries
    
    def get_entries_for_user(self, user_id: str) -> List[ConsentEntry]:
        """Get all consent entries for a specific user."""
        return [e for e in self.load_entries() if e.user_id == user_id]


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command-line arguments for consent log operations."""
    parser = argparse.ArgumentParser(
        description="G221 Consent Log Signed — HMAC-signed consent entries"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # add command
    add_parser = subparsers.add_parser("add", help="Add a consent entry")
    add_parser.add_argument("--user-id", required=True, help="User ID")
    add_parser.add_argument("--game", required=True, help="Game name")
    add_parser.add_argument(
        "--opt-in-version", default="1.0", help="Opt-in version"
    )
    add_parser.add_argument(
        "--log-file", default="consent.log", help="Log file path"
    )
    add_parser.add_argument(
        "--key-file", default=None, help="Key file path"
    )
    
    # verify command
    verify_parser = subparsers.add_parser("verify", help="Verify a consent entry")
    verify_parser.add_argument("--user-id", required=True, help="User ID")
    verify_parser.add_argument("--game", required=True, help="Game name")
    verify_parser.add_argument("--timestamp", required=True, help="Timestamp")
    verify_parser.add_argument(
        "--opt-in-version", default="1.0", help="Opt-in version"
    )
    verify_parser.add_argument("--signature", required=True, help="Signature")
    verify_parser.add_argument(
        "--log-file", default="consent.log", help="Log file path"
    )
    verify_parser.add_argument(
        "--key-file", default=None, help="Key file path"
    )
    
    # list command
    list_parser = subparsers.add_parser("list", help="List all entries")
    list_parser.add_argument(
        "--log-file", default="consent.log", help="Log file path"
    )
    list_parser.add_argument(
        "--key-file", default=None, help="Key file path"
    )
    list_parser.add_argument(
        "--user-id", default=None, help="Filter by user ID"
    )
    
    return parser.parse_args(argv)


def cmd_add(args: argparse.Namespace) -> int:
    """Handle the 'add' command."""
    log = ConsentLogSigned(args.log_file, key_file=args.key_file)
    entry = log.add_entry(args.user_id, args.game, args.opt_in_version)
    print(f"Added entry: {entry.to_dict()}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Handle the 'verify' command."""
    entry = ConsentEntry(
        user_id=args.user_id,
        game=args.game,
        timestamp=args.timestamp,
        opt_in_version=args.opt_in_version,
        signature=args.signature
    )
    log = ConsentLogSigned(args.log_file, key_file=args.key_file)
    if log.verify_entry(entry):
        print("Signature is VALID")
        return 0
    else:
        print("Signature is INVALID")
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """Handle the 'list' command."""
    log = ConsentLogSigned(args.log_file, key_file=args.key_file)
    if args.user_id:
        entries = log.get_entries_for_user(args.user_id)
    else:
        entries = log.load_entries()
    
    for entry in entries:
        print(json.dumps(entry.to_dict()))
    return 0


def main() -> int:
    """Main entry point for consent log CLI."""
    args = parse_args(sys.argv[1:])
    
    if args.command == "add":
        return cmd_add(args)
    elif args.command == "verify":
        return cmd_verify(args)
    elif args.command == "list":
        return cmd_list(args)
    else:
        print("Error: must specify command (add, verify, list)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
