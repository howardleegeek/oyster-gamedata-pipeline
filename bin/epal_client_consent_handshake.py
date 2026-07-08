#!/usr/bin/env python3
"""
EPal Client Consent Handshake Module.

Handles consent handshake at EPal session start: prompts paying client for
recording consent, AI training opt-out, and GDPR-compliant disclosure.
Persists signed consent log per G221 specification.
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONSENT_LOG_DIR = Path("logs/consent")

GDPR_DISCLOSURE = """
================================================================================
                          GDPR COMPLIANCE NOTICE
================================================================================
Data Controller: EPal Technologies Inc.

PURPOSE: Session recording for quality assurance, AI training, and security.

LEGAL BASIS: Explicit consent (GDPR Article 6(1)(a)) and legitimate interests.

DATA RETENTION: Up to 24 months for AI training, then securely deleted.

YOUR RIGHTS: Access, rectification, erasure, restriction, portability, and
withdrawal of consent at any time.

CONTACT: dpo@epal.example.com for data protection inquiries.
================================================================================
"""


class ConsentError(Exception):
    """Exception raised when consent handshake fails."""
    pass


class ConsentRecord:
    """Represents a consent record for a client session per G221."""
    
    def __init__(
        self,
        session_id: str,
        client_id: str,
        ai_training_consent: bool = False,
        recording_consent: bool = False,
        gdpr_acknowledged: bool = False,
        opt_out_flags: Optional[Dict[str, bool]] = None
    ) -> None:
        self.session_id = session_id
        self.client_id = client_id
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.ai_training_consent = ai_training_consent
        self.recording_consent = recording_consent
        self.gdpr_acknowledged = gdpr_acknowledged
        self.opt_out_flags = opt_out_flags or {}
        self.signature_hash = self._compute_signature()
    
    def _compute_signature(self) -> str:
        """Compute SHA-256 signature hash for verification."""
        data = (
            f"{self.session_id}:{self.client_id}:{self.timestamp}:"
            f"{self.ai_training_consent}:{self.recording_consent}:"
            f"{self.gdpr_acknowledged}:{json.dumps(self.opt_out_flags, sort_keys=True)}"
        )
        return hashlib.sha256(data.encode('utf-8')).hexdigest()[:32]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert consent record to dictionary."""
        return {
            "session_id": self.session_id,
            "client_id": self.client_id,
            "timestamp": self.timestamp,
            "ai_training_consent": self.ai_training_consent,
            "recording_consent": self.recording_consent,
            "gdpr_acknowledged": self.gdpr_acknowledged,
            "opt_out_flags": self.opt_out_flags,
            "signature_hash": self.signature_hash,
            "version": "1.0"
        }


def display_gdpr_disclosure() -> bool:
    """Display GDPR disclosure and get acknowledgment."""
    print(GDPR_DISCLOSURE)
    print("Press ENTER to acknowledge, or 'q' to quit...")
    try:
        return input().strip().lower() != 'q'
    except EOFError:
        return False


def prompt_consent() -> tuple[bool, bool, Dict[str, bool]]:
    """Prompt for AI training and recording consent with opt-out options."""
    print("\nSESSION RECORDING & AI TRAINING CONSENT")
    print("-" * 40)
    
    # AI training consent
    print("Consent to session recording for AI training? [y/N]: ", end="")
    try:
        ai_consent = input().strip().lower() in ('y', 'yes')
    except EOFError as exc:
        logger.debug("prompt_consent: AI training consent input EOF: %s", exc)
        ai_consent = False

    # Recording consent
    print("Consent to session recording for quality purposes? [y/N]: ", end="")
    try:
        rec_consent = input().strip().lower() in ('y', 'yes')
    except EOFError as exc:
        logger.debug("prompt_consent: recording consent input EOF: %s", exc)
        rec_consent = False

    # Opt-out options
    opt_out: Dict[str, bool] = {}
    if ai_consent:
        print("\nOpt-out options:")
        for opt in ["data_sharing", "third_party_access", "long_term_storage"]:
            print(f"  Opt-out of {opt.replace('_', ' ')}? [y/N]: ", end="")
            try:
                if input().strip().lower() in ('y', 'yes'):
                    opt_out[opt] = True
            except EOFError as exc:
                logger.debug("prompt_consent: opt-out %s input EOF: %s", opt, exc)
    
    return ai_consent, rec_consent, opt_out


def persist_consent_log(record: ConsentRecord, log_dir: Optional[Path] = None) -> Path:
    """Persist consent record to signed log file per G221."""
    log_path = log_dir or DEFAULT_CONSENT_LOG_DIR
    log_path.mkdir(parents=True, exist_ok=True)
    
    safe_id = "".join(c if c.isalnum() or c in '-_' else '_' for c in record.session_id)
    ts_safe = record.timestamp.replace(':', '-').replace('.', '-')
    filename = f"consent_{safe_id}_{ts_safe}.json"
    file_path = log_path / filename
    
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(record.to_dict(), f, indent=2, ensure_ascii=False)
    
    return file_path


def run_consent_handshake(
    session_id: str,
    client_id: str,
    log_dir: Optional[Path] = None,
    non_interactive: bool = False,
    default_consent: bool = False
) -> ConsentRecord:
    """Execute full consent handshake process."""
    if non_interactive:
        record = ConsentRecord(
            session_id=session_id,
            client_id=client_id,
            ai_training_consent=default_consent,
            recording_consent=default_consent,
            gdpr_acknowledged=True,
            opt_out_flags={}
        )
    else:
        if not display_gdpr_disclosure():
            raise ConsentError("Client declined GDPR disclosure acknowledgment")
        
        ai_consent, rec_consent, opt_out = prompt_consent()
        record = ConsentRecord(
            session_id=session_id,
            client_id=client_id,
            ai_training_consent=ai_consent,
            recording_consent=rec_consent,
            gdpr_acknowledged=True,
            opt_out_flags=opt_out
        )
    
    log_path = persist_consent_log(record, log_dir)
    print(f"\nConsent log saved to: {log_path}")
    return record


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for consent handshake CLI."""
    parser = argparse.ArgumentParser(
        prog="epal_client_consent_handshake",
        description="Handle client consent handshake for EPal sessions."
    )
    parser.add_argument("--session-id", "-s", required=True, help="Unique session ID")
    parser.add_argument("--client-id", "-c", required=True, help="Unique client ID")
    parser.add_argument("--log-dir", "-l", type=Path, help="Consent log directory")
    parser.add_argument("--non-interactive", "-n", action="store_true",
                        help="Run non-interactively with defaults")
    parser.add_argument("--default-consent", "-d", action="store_true",
                        help="Default to consent given (non-interactive)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without persisting")
    
    args = parser.parse_args(argv)
    
    print("=" * 60)
    print("EPAL CLIENT CONSENT HANDSHAKE")
    print("=" * 60)
    print(f"Session ID: {args.session_id}")
    print(f"Client ID: {args.client_id}\n")
    
    if args.dry_run:
        record = ConsentRecord(
            session_id=args.session_id,
            client_id=args.client_id,
            ai_training_consent=args.default_consent,
            recording_consent=args.default_consent,
            gdpr_acknowledged=True
        )
        print("[DRY RUN] Consent record:")
        print(json.dumps(record.to_dict(), indent=2))
        return 0
    
    try:
        record = run_consent_handshake(
            session_id=args.session_id,
            client_id=args.client_id,
            log_dir=args.log_dir,
            non_interactive=args.non_interactive,
            default_consent=args.default_consent
        )
        print("\n" + "=" * 60)
        print("CONSENT HANDSHAKE COMPLETE")
        print("=" * 60)
        print(f"AI Training Consent: {'YES' if record.ai_training_consent else 'NO'}")
        print(f"Recording Consent: {'YES' if record.recording_consent else 'NO'}")
        print(f"Signature: {record.signature_hash}")
        return 0
    except ConsentError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n\nCancelled by user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
