"""
Merkle tree implementation for frame-level provenance verification.

Builds Merkle tree over (frame_idx, frame_sha256). Buyer can verify any single
frame against the root without downloading all frames.
"""

import hashlib
from typing import List, Tuple, Optional
from dataclasses import dataclass


def sha256(data: bytes) -> str:
    """Compute SHA256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def double_sha256(data: bytes) -> str:
    """Double SHA256 (like Bitcoin) for internal Merkle nodes."""
    return sha256(sha256(data).encode())


def hash_leaf(frame_idx: int, frame_hash: str) -> str:
    """Hash a leaf node (frame index + frame hash)."""
    return sha256(f"{frame_idx}:{frame_hash}".encode())


def hash_node(left: str, right: str) -> str:
    """Hash an internal Merkle node (concatenation of children)."""
    return double_sha256((left + right).encode())


@dataclass
class MerkleProof:
    """Merkle proof for a single frame."""
    frame_idx: int
    frame_hash: str
    root: str
    proof: List[Tuple[str, str]]  # List of (hash, position) pairs


class MerkleTree:
    """Merkle tree for frame hashing."""
    
    def __init__(self):
        self.leaves: List[str] = []
        self.nodes: List[str] = []  # All nodes level by level
        self.root: Optional[str] = None
        self.frame_count: int = 0
    
    @classmethod
    def from_frame_hashes(cls, frame_hashes: dict) -> 'MerkleTree':
        """
        Build Merkle tree from dict of {frame_idx: frame_sha256}.
        
        Args:
            frame_hashes: Dict mapping frame index to SHA256 hash
            
        Returns:
            MerkleTree with computed root
        """
        tree = cls()
        
        # Sort by frame index
        sorted_items = sorted(frame_hashes.items(), key=lambda x: x[0])
        
        # Create leaf nodes
        for frame_idx, frame_hash in sorted_items:
            leaf = hash_leaf(frame_idx, frame_hash)
            tree.leaves.append(leaf)
        
        tree.frame_count = len(tree.leaves)
        
        if tree.frame_count == 0:
            # Empty tree - use empty hash
            tree.root = sha256(b"")
            return tree
        
        if tree.frame_count == 1:
            tree.root = tree.leaves[0]
            return tree
        
        # Build tree bottom-up
        current_level = tree.leaves[:]
        
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                parent = hash_node(left, right)
                next_level.append(parent)
            current_level = next_level
        
        tree.root = current_level[0]
        return tree
    
    def get_proof(self, frame_idx: int, frame_hash: str) -> MerkleProof:
        """
        Generate Merkle proof for a specific frame.
        
        Args:
            frame_idx: Index of the frame
            frame_hash: SHA256 hash of the frame
            
        Returns:
            MerkleProof that can be verified against the root
        """
        if frame_idx >= self.frame_count:
            raise ValueError(f"Frame index {frame_idx} out of range")
        
        # Rebuild path to root
        proof = []
        
        # Start from leaf
        current_idx = frame_idx
        
        # Build tree level by level
        level = self.leaves[:]
        
        while len(level) > 1:
            # Determine position in current level
            pos = current_idx
            
            # Get sibling
            if pos % 2 == 0:
                # Left child - sibling is right
                sibling_idx = pos + 1
                sibling_pos = 'right'
            else:
                # Right child - sibling is left
                sibling_idx = pos - 1
                sibling_pos = 'left'
            
            if sibling_idx < len(level):
                sibling_hash = level[sibling_idx]
                proof.append((sibling_hash, sibling_pos))
            
            # Move to parent level
            current_idx = pos // 2
            next_level = []
            
            for i in range(0, len(level), 2):
                left = level[i]
                right = level[i + 1] if i + 1 < len(level) else left
                parent = hash_node(left, right)
                next_level.append(parent)
            
            level = next_level
        
        return MerkleProof(
            frame_idx=frame_idx,
            frame_hash=frame_hash,
            root=self.root,
            proof=proof
        )
    
    def verify_proof(proof: MerkleProof) -> bool:
        """
        Verify a Merkle proof.
        
        Args:
            proof: MerkleProof to verify
            
        Returns:
            True if proof is valid
        """
        # Start with the leaf
        current_hash = hash_leaf(proof.frame_idx, proof.frame_hash)
        
        # Traverse proof
        for sibling_hash, position in proof.proof:
            if position == 'left':
                current_hash = hash_node(sibling_hash, current_hash)
            else:
                current_hash = hash_node(current_hash, sibling_hash)
        
        return current_hash == proof.root
    
    def to_dict(self) -> dict:
        """Serialize tree info for manifest."""
        return {
            "frame_count": self.frame_count,
            "frame_hash_merkle_root": self.root,
        }


def compute_file_hashes(directory: str) -> dict:
    """
    Compute SHA256 hashes for all files in a directory.
    
    Args:
        directory: Path to session directory
        
    Returns:
        Dict mapping filename to SHA256 hash
    """
    import os
    
    file_hashes = {}
    
    for root, _, files in os.walk(directory):
        for filename in sorted(files):
            filepath = os.path.join(root, filename)
            relative_path = os.path.relpath(filepath, directory)
            
            with open(filepath, 'rb') as f:
                file_hashes[relative_path] = sha256(f.read())
    
    return file_hashes


def build_merkle_root_from_files(directory: str) -> str:
    """
    Build Merkle root from all files in a directory.
    
    This is used for the file_hashes in the manifest.
    
    Args:
        directory: Path to session directory
        
    Returns:
        Merkle root hash
    """
    file_hashes = compute_file_hashes(directory)
    
    if not file_hashes:
        return sha256(b"")
    
    # Sort by filename
    sorted_hashes = sorted(file_hashes.items(), key=lambda x: x[0])
    
    # Build simple Merkle tree (not frame-level)
    leaves = [sha256(f"{k}:{v}".encode()) for k, v in sorted_hashes]
    
    while len(leaves) > 1:
        next_level = []
        for i in range(0, len(leaves), 2):
            left = leaves[i]
            right = leaves[i + 1] if i + 1 < len(leaves) else left
            next_level.append(hash_node(left, right))
        leaves = next_level
    
    return leaves[0]


if __name__ == "__main__":
    # Demo
    frame_hashes = {
        0: "a" * 64,
        1: "b" * 64,
        2: "c" * 64,
        3: "d" * 64,
    }
    
    tree = MerkleTree.from_frame_hashes(frame_hashes)
    print(f"Root: {tree.root}")
    print(f"Frame count: {tree.frame_count}")
    
    # Generate and verify proof
    proof = tree.get_proof(1, "b" * 64)
    print(f"Proof: {proof}")
    print(f"Verified: {MerkleTree.verify_proof(proof)}")
