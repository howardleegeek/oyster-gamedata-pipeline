"""
Oyster provenance package.

Cryptographic provenance hash chain for Oyster GameData.
"""

from .anchor import (
    AnchorChain,
    AnchorTransaction,
    WeeklyAnchor,
    create_weekly_anchor,
    load_weekly_anchor,
    save_weekly_anchor,
)
from .manifest import (
    BiometricFlags,
    DeviceAttestation,
    SessionManifest,
    backfill_manifest,
    build_manifest,
    load_manifest,
    save_manifest,
)
from .merkle import MerkleProof, MerkleTree
from .sign import PublicKeyInfo, SigningKey, generate_keypair, load_or_create_keypair

__all__ = [
    # Manifest
    "SessionManifest",
    "BiometricFlags",
    "DeviceAttestation",
    "build_manifest",
    "save_manifest",
    "load_manifest",
    "backfill_manifest",
    # Merkle
    "MerkleTree",
    "MerkleProof",
    # Sign
    "SigningKey",
    "PublicKeyInfo",
    "generate_keypair",
    "load_or_create_keypair",
    # Anchor
    "WeeklyAnchor",
    "AnchorTransaction",
    "AnchorChain",
    "create_weekly_anchor",
    "save_weekly_anchor",
    "load_weekly_anchor",
]

__version__ = "1.0.0"
