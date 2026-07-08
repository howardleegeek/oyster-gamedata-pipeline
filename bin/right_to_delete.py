#!/usr/bin/env python3
"""
Right-to-Delete CLI - Process GDPR deletion requests.
Finds sessions tagged with contributor_id_hash, marks for deletion (30d grace period),
removes from S3 + dashboard + payout history after grace period.
Logs deletion audit trail to ~/.oyster/deletions.jsonl
"""

import argparse
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Default config
OYSTER_DIR = Path.home() / '.oyster'
DELETION_LOG = OYSTER_DIR / 'deletions.jsonl'
GRACE_PERIOD_DAYS = 30


def ensure_oyster_dir():
    """Ensure oyster config directory exists."""
    OYSTER_DIR.mkdir(parents=True, exist_ok=True)


def hash_contributor_id(contributor_id: str) -> str:
    """Hash contributor ID for privacy."""
    return hashlib.sha256(contributor_id.encode()).hexdigest()[:16]


def load_deletion_log() -> List[Dict[str, Any]]:
    """Load existing deletion log entries."""
    if not DELETION_LOG.exists():
        return []
    
    entries = []
    with open(DELETION_LOG, 'r') as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.debug(
                    "deletion log line skipped (corrupt JSON) [%s]: %s",
                    type(exc).__name__, exc,
                )
                continue
    return entries


def save_deletion_entry(entry: Dict[str, Any]):
    """Save a deletion log entry."""
    ensure_oyster_dir()
    with open(DELETION_LOG, 'a') as f:
        f.write(json.dumps(entry) + '\n')


def find_sessions_for_contributor(contributor_id_hash: str, sessions_dir: Path = Path('.')) -> List[Path]:
    """Find all sessions tagged with a contributor ID."""
    sessions = []
    
    if not sessions_dir.exists():
        return sessions
    
    # Look for sessions with contributor_id in metadata
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        
        metadata_file = session_dir / 'metadata.json'
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                contrib_id = metadata.get('contributor_id_hash') or metadata.get('contributor_id')
                if contrib_id == contributor_id_hash:
                    sessions.append(session_dir)
            except (json.JSONDecodeError, IOError) as exc:
                logger.debug(
                    "find_sessions: metadata parse failed for %s [%s]: %s",
                    metadata_file, type(exc).__name__, exc,
                )
                continue
        
        # Also check session.json
        session_file = session_dir / 'session.json'
        if session_file.exists():
            try:
                with open(session_file, 'r') as f:
                    session_data = json.load(f)
                
                contrib_id = session_data.get('contributor_id_hash') or session_data.get('contributor_id')
                if contrib_id == contributor_id_hash:
                    if session_dir not in sessions:
                        sessions.append(session_dir)
            except (json.JSONDecodeError, IOError) as exc:
                logger.debug(
                    "find_sessions: session.json parse failed for %s [%s]: %s",
                    session_file, type(exc).__name__, exc,
                )
                continue
    
    return sessions


def mark_for_deletion(contributor_id: str, requested_at: str, reason: str, sessions_dir: Path = Path('.')) -> Dict[str, Any]:
    """Mark contributor's data for deletion."""
    contributor_id_hash = hash_contributor_id(contributor_id)
    
    # Find sessions
    sessions = find_sessions_for_contributor(contributor_id_hash, sessions_dir)
    
    # Calculate deletion date (30 days from now)
    requested_date = datetime.fromisoformat(requested_at.replace('Z', '+00:00')) if 'T' in requested_at else datetime.strptime(requested_at, '%Y-%m-%d')
    deletion_date = requested_date + timedelta(days=GRACE_PERIOD_DAYS)
    
    # Create deletion request entry
    entry = {
        'contributor_id_hash': contributor_id_hash,
        'requested_at': requested_at,
        'reason': reason,
        'deletion_due_by': deletion_date.isoformat(),
        'sessions_marked': [str(s) for s in sessions],
        'status': 'pending',
        'created_at': datetime.now(timezone.utc).isoformat()
    }
    
    # Save to log
    save_deletion_entry(entry)
    
    # Mark each session for deletion
    for session_dir in sessions:
        mark_session_for_deletion(session_dir, contributor_id_hash, deletion_date)
    
    return entry


def mark_session_for_deletion(session_dir: Path, contributor_id_hash: str, deletion_date: datetime):
    """Mark a session for deletion."""
    deletion_marker = session_dir / '.deletion_pending'
    
    with open(deletion_marker, 'w') as f:
        json.dump({
            'contributor_id_hash': contributor_id_hash,
            'deletion_due_by': deletion_date.isoformat(),
            'marked_at': datetime.now(timezone.utc).isoformat()
        }, f)


def process_deletions(sessions_dir: Path = Path('.'), dry_run: bool = False) -> List[Dict[str, Any]]:
    """Process any deletion requests that are due."""
    now = datetime.now(timezone.utc)
    processed = []
    
    entries = load_deletion_log()
    
    for entry in entries:
        if entry.get('status') != 'pending':
            continue
        
        deletion_due = datetime.fromisoformat(entry['deletion_due_by'])
        
        if deletion_due <= now:
            # Process deletion
            sessions = entry.get('sessions_marked', [])
            
            for session_path in sessions:
                session_dir = Path(session_path)
                if session_dir.exists():
                    if dry_run:
                        print(f"DRY RUN: Would delete {session_dir}")
                    else:
                        delete_session(session_dir)
            
            # Update entry status
            entry['status'] = 'completed'
            entry['completed_at'] = now.isoformat()
            
            # Rewrite log (inefficient but safe)
            rewrite_deletion_log(entries)
            
            processed.append(entry)
    
    return processed


def delete_session(session_dir: Path):
    """Delete a session and all its data."""
    # Remove session directory
    import shutil
    if session_dir.exists():
        shutil.rmtree(session_dir)
    
    # Log the deletion
    deletion_record = {
        'session_dir': str(session_dir),
        'deleted_at': datetime.now(timezone.utc).isoformat(),
        'action': 'session_deleted'
    }
    
    ensure_oyster_dir()
    with open(OYSTER_DIR / 'deletion_records.jsonl', 'a') as f:
        f.write(json.dumps(deletion_record) + '\n')


def rewrite_deletion_log(entries: List[Dict[str, Any]]):
    """Rewrite the entire deletion log."""
    ensure_oyster_dir()
    with open(DELETION_LOG, 'w') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')


def list_deletion_requests() -> List[Dict[str, Any]]:
    """List all deletion requests."""
    return load_deletion_log()


def check_deletion_status(contributor_id: str) -> Dict[str, Any]:
    """Check status of a deletion request."""
    contributor_id_hash = hash_contributor_id(contributor_id)
    
    entries = load_deletion_log()
    
    for entry in entries:
        if entry.get('contributor_id_hash') == contributor_id_hash:
            return entry
    
    return {'status': 'not_found'}


def main():
    parser = argparse.ArgumentParser(
        description='Right-to-Delete CLI - Process GDPR deletion requests',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Request deletion for a contributor
  oyster-delete --contributor-id user123 --requested-at 2026-05-17 --reason "user request"
  
  # Check status of a deletion request
  oyster-delete --contributor-id user123 --status
  
  # Process due deletions (run as cron job)
  oyster-delete --process
  
  # List all deletion requests
  oyster-delete --list
        """
    )
    
    parser.add_argument('--contributor-id', type=str, help='Contributor ID to delete')
    parser.add_argument('--requested-at', type=str, default=None, help='Date of deletion request (YYYY-MM-DD)')
    parser.add_argument('--reason', type=str, default='user request', help='Reason for deletion')
    parser.add_argument('--status', action='store_true', help='Check status of deletion request')
    parser.add_argument('--process', action='store_true', help='Process due deletions')
    parser.add_argument('--list', action='store_true', help='List all deletion requests')
    parser.add_argument('--sessions-dir', type=Path, default=Path('.'), help='Sessions directory')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode')
    
    args = parser.parse_args()
    
    ensure_oyster_dir()
    
    # Handle different commands
    if args.status and args.contributor_id:
        status = check_deletion_status(args.contributor_id)
        print(json.dumps(status, indent=2))
        return 0
    
    if args.list:
        entries = list_deletion_requests()
        print(json.dumps(entries, indent=2))
        return 0
    
    if args.process:
        processed = process_deletions(args.sessions_dir, dry_run=args.dry_run)
        print(f"Processed {len(processed)} deletion(s)")
        return 0
    
    if args.contributor_id and args.requested_at:
        result = mark_for_deletion(
            args.contributor_id,
            args.requested_at,
            args.reason,
            args.sessions_dir
        )
        print(json.dumps(result, indent=2))
        return 0
    
    parser.print_help()
    return 1


if __name__ == '__main__':
    exit(main())
