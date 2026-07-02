"""
Weekly chain anchor for Oyster provenance.

Rolls up all session manifests of a week into a meta-Merkle, anchors root hash
on Bitcoin (via OP_RETURN) or Ethereum (via contract). Makes back-dating
provably impossible.

Cost target: ≤ $1/week (Bitcoin OP_RETURN is $0.50-2 typically)
"""

import json
import os
import time
from typing import List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum

from .merkle import hash_node, sha256


class AnchorChain(Enum):
    """Supported anchor chains."""
    BITCOIN = "bitcoin"
    ETHEREUM = "ethereum"


@dataclass
class AnchorTransaction:
    """Anchor transaction details."""
    chain: str
    tx_hash: str
    block_number: int
    block_hash: str
    confirmed_at_utc: str
    anchor_root: str
    week_start: str
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AnchorTransaction':
        return cls(**data)


@dataclass
class WeeklyAnchor:
    """Weekly anchor metadata."""
    week_start: str  # ISO date
    week_end: str
    session_count: int
    meta_merkle_root: str
    anchor_tx: Optional[AnchorTransaction] = None
    
    def to_dict(self) -> dict:
        data = asdict(self)
        if self.anchor_tx:
            data['anchor_tx'] = self.anchor_tx.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'WeeklyAnchor':
        anchor_tx = None
        if data.get('anchor_tx'):
            anchor_tx = AnchorTransaction.from_dict(data['anchor_tx'])
        return cls(
            week_start=data['week_start'],
            week_end=data['week_end'],
            session_count=data['session_count'],
            meta_merkle_root=data['meta_merkle_root'],
            anchor_tx=anchor_tx,
        )


def get_week_range(date: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """
    Get week range (Monday to Sunday) for a date.
    
    Args:
        date: Date to get week for (default: now)
        
    Returns:
        Tuple of (week_start, week_end)
    """
    if date is None:
        date = datetime.utcnow()
    
    # Monday start
    week_start = date - timedelta(days=date.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Sunday end
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    
    return week_start, week_end


def format_week_id(week_start: datetime) -> str:
    """Format week ID as YYYY-MM-DD."""
    return week_start.strftime("%Y-%m-%d")


def compute_meta_merkle_root(session_manifests: List[dict]) -> str:
    """
    Compute meta-Merkle root from session manifest hashes.
    
    Args:
        session_manifests: List of session manifest dicts
        
    Returns:
        Meta-Merkle root hash
    """
    if not session_manifests:
        return sha256(b"empty_week")
    
    # Hash each manifest
    manifest_hashes = []
    for manifest in session_manifests:
        # Canonical JSON
        manifest_json = json.dumps(manifest, sort_keys=True, separators=(',', ':'))
        manifest_hash = sha256(manifest_json.encode())
        manifest_hashes.append(manifest_hash)
    
    # Build Merkle tree
    leaves = manifest_hashes[:]
    
    while len(leaves) > 1:
        next_level = []
        for i in range(0, len(leaves), 2):
            left = leaves[i]
            right = leaves[i + 1] if i + 1 < len(leaves) else left
            parent = hash_node(left, right)
            next_level.append(parent)
        leaves = next_level
    
    return leaves[0]


def collect_week_manifests(
    sessions_dir: str,
    week_start: datetime,
    week_end: datetime,
) -> List[dict]:
    """
    Collect all session manifests for a week.
    
    Args:
        sessions_dir: Root directory containing session folders
        week_start: Start of week
        week_end: End of week
        
    Returns:
        List of manifest dicts
    """
    manifests = []
    
    if not os.path.exists(sessions_dir):
        return manifests
    
    for session_folder in os.listdir(sessions_dir):
        session_path = os.path.join(sessions_dir, session_folder)
        
        if not os.path.isdir(session_path):
            continue
        
        manifest_path = os.path.join(session_path, "provenance.json")
        
        if not os.path.exists(manifest_path):
            continue
        
        # Check if manifest is from this week
        # Use file modification time as proxy for session time
        mtime = os.path.getmtime(manifest_path)
        manifest_time = datetime.fromtimestamp(mtime)
        
        if week_start <= manifest_time <= week_end:
            with open(manifest_path, 'r') as f:
                try:
                    manifest = json.load(f)
                    manifests.append(manifest)
                except json.JSONDecodeError:
                    pass
    
    return manifests


def create_weekly_anchor(
    sessions_dir: str,
    week_start: Optional[datetime] = None,
    anchor_chain: AnchorChain = AnchorChain.BITCOIN,
) -> WeeklyAnchor:
    """
    Create weekly anchor from all sessions in a week.
    
    Args:
        sessions_dir: Root directory containing session folders
        week_start: Start of week (default: current week)
        anchor_chain: Chain to anchor to
        
    Returns:
        WeeklyAnchor with meta-Merkle root
    """
    if week_start is None:
        week_start, week_end = get_week_range()
    else:
        _, week_end = get_week_range(week_start)
    
    week_start_str = format_week_id(week_start)
    week_end_str = week_end.strftime("%Y-%m-%d")
    
    # Collect manifests
    manifests = collect_week_manifests(sessions_dir, week_start, week_end)
    
    # Compute meta-Merkle root
    meta_merkle_root = compute_meta_merkle_root(manifests)
    
    anchor = WeeklyAnchor(
        week_start=week_start_str,
        week_end=week_end_str,
        session_count=len(manifests),
        meta_merkle_root=meta_merkle_root,
    )
    
    return anchor


def simulate_anchor_tx(
    anchor: WeeklyAnchor,
    chain: AnchorChain = AnchorChain.BITCOIN,
    block_number: int = 870234,
) -> AnchorTransaction:
    """
    Simulate an anchor transaction (for testing/demo).
    
    In production, this would call Bitcoin/Ethereum APIs.
    
    Args:
        anchor: WeeklyAnchor to anchor
        chain: Chain to use
        block_number: Simulated block number
        
    Returns:
        AnchorTransaction
    """
    # Simulate tx hash (in production, this would be real)
    tx_data = f"{anchor.meta_merkle_root}:{anchor.week_start}:{chain.value}"
    tx_hash = sha256(tx_data.encode())
    
    # Simulate block hash
    block_data = f"{block_number}:{tx_hash}"
    block_hash = sha256(block_data.encode())
    
    return AnchorTransaction(
        chain=chain.value,
        tx_hash=tx_hash,
        block_number=block_number,
        block_hash=block_hash,
        confirmed_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        anchor_root=anchor.meta_merkle_root,
        week_start=anchor.week_start,
    )


def save_weekly_anchor(anchor: WeeklyAnchor, anchors_dir: str):
    """Save weekly anchor to file."""
    os.makedirs(anchors_dir, exist_ok=True)
    
    anchor_path = os.path.join(anchors_dir, f"anchor_{anchor.week_start}.json")
    with open(anchor_path, 'w') as f:
        json.dump(anchor.to_dict(), f, indent=2)


def load_weekly_anchor(anchors_dir: str, week_start: str) -> Optional[WeeklyAnchor]:
    """Load weekly anchor from file."""
    anchor_path = os.path.join(anchors_dir, f"anchor_{week_start}.json")
    
    if not os.path.exists(anchor_path):
        return None
    
    with open(anchor_path, 'r') as f:
        return WeeklyAnchor.from_dict(json.load(f))


def get_anchor_for_session(
    session_dir: str,
    anchors_dir: str,
) -> Optional[AnchorTransaction]:
    """
    Get anchor transaction for a session's week.
    
    Args:
        session_dir: Path to session directory
        anchors_dir: Directory containing anchor files
        
    Returns:
        AnchorTransaction if found
    """
    manifest_path = os.path.join(session_dir, "provenance.json")
    
    if not os.path.exists(manifest_path):
        return None
    
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)
    
    # Get session timestamp from consent_signed_at_utc
    consent_time = manifest.get("consent_signed_at_utc")
    if not consent_time:
        return None
    
    # Parse and get week
    try:
        session_date = datetime.fromisoformat(consent_time.replace('Z', '+00:00'))
        week_start, _ = get_week_range(session_date)
        week_start_str = format_week_id(week_start)
    except Exception:
        return None
    
    # Load anchor
    anchor = load_weekly_anchor(anchors_dir, week_start_str)
    
    if anchor and anchor.anchor_tx:
        return anchor.anchor_tx
    
    return None


# Mock blockchain API for testing
class MockBlockchainAPI:
    """Mock blockchain API for testing."""
    
    def __init__(self, chain: AnchorChain = AnchorChain.BITCOIN):
        self.chain = chain
        self.tx_count = 0
    
    def broadcast_op_return(self, data: str) -> str:
        """Broadcast OP_RETURN (simulated)."""
        self.tx_count += 1
        tx_hash = sha256(f"{data}:{self.tx_count}".encode())
        return tx_hash
    
    def get_tx_confirmations(self, tx_hash: str) -> int:
        """Get confirmation count (simulated)."""
        return 6  # Always confirmed
    
    def get_block_info(self, block_number: int) -> dict:
        """Get block info (simulated)."""
        return {
            "hash": sha256(f"block{block_number}".encode()),
            "number": block_number,
            "timestamp": int(time.time()),
        }


if __name__ == "__main__":
    import tempfile
    
    # Demo
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some test sessions with manifests
        for i in range(3):
            session_dir = os.path.join(tmpdir, f"session_{i}")
            os.makedirs(session_dir)
            
            manifest = {
                "session_id": f"session-{i}",
                "player_id_hash": "abc123",
                "consent_doc_sha256": "def456",
                "consent_signed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "frame_hash_merkle_root": f"root{i}",
            }
            
            with open(os.path.join(session_dir, "provenance.json"), 'w') as f:
                json.dump(manifest, f)
        
        # Create weekly anchor
        anchor = create_weekly_anchor(tmpdir)
        print(f"Week: {anchor.week_start} to {anchor.week_end}")
        print(f"Session count: {anchor.session_count}")
        print(f"Meta-Merkle root: {anchor.meta_merkle_root}")
        
        # Simulate anchor transaction
        tx = simulate_anchor_tx(anchor)
        anchor.anchor_tx = tx
        print(f"\nAnchor tx: {tx.tx_hash}")
        print(f"Block: {tx.block_number}")
        
        # Save
        save_weekly_anchor(anchor, tmpdir)
        
        # Load and verify
        loaded = load_weekly_anchor(tmpdir, anchor.week_start)
        print(f"\nLoaded anchor: {loaded.week_start}")
        print(f"Anchor tx matches: {loaded.anchor_tx.tx_hash == tx.tx_hash}")
