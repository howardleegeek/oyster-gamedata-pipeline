"""
Oyster provenance package.

Cryptographic provenance hash chain for Oyster GameData.
"""

from .manifest import (
    SessionManifest,
    BiometricFlags,
    DeviceAttestation,
    build_manifest,
    save_manifest,
    load_manifest,
    backfill_manifest,
)
from .merkle import MerkleTree, MerkleProof
from .sign import SigningKey, PublicKeyInfo, generate_keypair, load_or_create_keypair
from .anchor import (
    WeeklyAnchor,
    AnchorTransaction,
    AnchorChain,
    create_weekly_anchor,
    save_weekly_anchor,
    load_weekly_anchor,
)

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
