"""
Per-session manifest builder for Oyster provenance.

Produces provenance.json for each session with all required fields:
- session_id
- player_id_hash (pseudonymous)
- consent_doc_sha256
- consent_doc_url
- consent_signed_at_utc
- capture_eula_version
- frame_count
- frame_hash_merkle_root
- file_hashes
- device_attestation
- biometric_flags
- oyster_signature
- anchor_tx_hash
"""

import json
import os
import uuid
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Union
from dataclasses import dataclass, asdict

from .merkle import MerkleTree, compute_file_hashes, build_merkle_root_from_files
from .sign import SigningKey, load_or_create_keypair, verify_json_signature


# Default paths
DEFAULT_CONSENT_URL = "https://oyster.io/consent/v3.pdf"
DEFAULT_EULA_VERSION = "v3.2"


def sha256(data: bytes) -> str:
    """Compute SHA256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def hash_player_id(player_pubkey: str, salt: str = "") -> str:
    """
    Hash player public key with salt for pseudonymous identification.
    
    Args:
        player_pubkey: Player's public key (hex or base64)
        salt: Optional salt for additional privacy
        
    Returns:
        SHA256 hash of pubkey + salt
    """
    return sha256(f"{player_pubkey}:{salt}".encode())


@dataclass
class BiometricFlags:
    """Biometric consent flags."""
    voice_chat_captured: bool = False
    webcam_captured: bool = False
    facial_data: bool = False
    minor_consent_obtained: bool = False
    age_verified_18plus: bool = False
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'BiometricFlags':
        return cls(**data)


@dataclass
class DeviceAttestation:
    """Device hardware attestation."""
    hardware_id_hash: str = ""
    device_model: str = ""
    os_version: str = ""
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'DeviceAttestation':
        return cls(**data)


@dataclass
class SessionManifest:
    """Complete session provenance manifest."""
    session_id: str
    player_id_hash: str
    consent_doc_sha256: str
    consent_doc_url: str
    consent_signed_at_utc: str
    capture_eula_version: str
    frame_count: int
    frame_hash_merkle_root: str
    file_hashes: Dict[str, str]
    device_attestation: Dict[str, str]
    biometric_flags: Dict[str, bool]
    oyster_signature: str = ""
    anchor_tx_hash: str = ""
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Remove empty signature/anchor for now
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'SessionManifest':
        """Create from dictionary."""
        return cls(**data)
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'SessionManifest':
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))
    
    def sign(self, signing_key: SigningKey):
        """Sign the manifest."""
        # Create canonical representation (without signature)
        data = self.to_dict()
        data_copy = dict(data)
        data_copy.pop('oyster_signature', None)
        data_copy.pop('anchor_tx_hash', None)
        
        self.oyster_signature = signing_key.sign_json(data_copy)
    
    def verify_signature(self, public_key_path: Optional[Union[str, Path]] = None) -> bool:
        """Verify the manifest signature."""
        if not self.oyster_signature:
            return False
        
        # Convert to Path if string
        if public_key_path is not None:
            public_key_path = Path(public_key_path)
        
        data = self.to_dict()
        data_copy = dict(data)
        data_copy.pop('oyster_signature', None)
        data_copy.pop('anchor_tx_hash', None)
        
        return verify_json_signature(data_copy, self.oyster_signature, public_key_path)


def build_manifest(
    session_dir: str,
    session_id: Optional[str] = None,
    player_pubkey: str = "",
    player_salt: str = "",
    consent_doc_path: Optional[str] = None,
    consent_doc_url: str = DEFAULT_CONSENT_URL,
    consent_signed_at_utc: Optional[str] = None,
    capture_eula_version: str = DEFAULT_EULA_VERSION,
    biometric_flags: Optional[BiometricFlags] = None,
    device_attestation: Optional[DeviceAttestation] = None,
    frame_hashes: Optional[Dict[int, str]] = None,
    signing_key: Optional[SigningKey] = None,
    key_dir: Optional[Union[str, Path]] = None,
) -> SessionManifest:
    """
    Build a complete session manifest.
    
    Args:
        session_dir: Path to session directory
        session_id: Optional session ID (generated if not provided)
        player_pubkey: Player's public key for pseudonymous ID
        player_salt: Optional salt for player ID
        consent_doc_path: Path to signed consent document
        consent_doc_url: URL of consent document
        consent_signed_at_utc: ISO timestamp when consent was signed
        capture_eula_version: Version of EULA in effect
        biometric_flags: Biometric consent flags
        device_attestation: Device attestation data
        frame_hashes: Dict of {frame_idx: frame_hash} for Merkle tree
        signing_key: Signing key (loaded from default if not provided)
        key_dir: Directory for keys (used if signing_key not provided)
        
    Returns:
        Signed SessionManifest
    """
    # Generate session ID if not provided
    if session_id is None:
        session_id = str(uuid.uuid4())
    
    # Compute player ID hash
    player_id_hash = hash_player_id(player_pubkey, player_salt)
    
    # Compute consent doc hash
    if consent_doc_path and os.path.exists(consent_doc_path):
        with open(consent_doc_path, 'rb') as f:
            consent_doc_sha256 = sha256(f.read())
    else:
        # Use placeholder if no consent doc
        consent_doc_sha256 = sha256(b"consent_not_provided")
    
    # Consent timestamp
    if consent_signed_at_utc is None:
        consent_signed_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    # Compute file hashes
    file_hashes = compute_file_hashes(session_dir)
    
    # Build frame Merkle tree
    if frame_hashes:
        merkle_tree = MerkleTree.from_frame_hashes(frame_hashes)
        frame_count = merkle_tree.frame_count
        frame_hash_merkle_root = merkle_tree.root
    else:
        # Build from file hashes if no frame hashes provided
        frame_hash_merkle_root = build_merkle_root_from_files(session_dir)
        frame_count = len(file_hashes)
    
    # Default biometric flags
    if biometric_flags is None:
        biometric_flags = BiometricFlags()
    
    # Default device attestation
    if device_attestation is None:
        device_attestation = DeviceAttestation()
    
    # Create manifest
    manifest = SessionManifest(
        session_id=session_id,
        player_id_hash=player_id_hash,
        consent_doc_sha256=consent_doc_sha256,
        consent_doc_url=consent_doc_url,
        consent_signed_at_utc=consent_signed_at_utc,
        capture_eula_version=capture_eula_version,
        frame_count=frame_count,
        frame_hash_merkle_root=frame_hash_merkle_root,
        file_hashes=file_hashes,
        device_attestation=device_attestation.to_dict(),
        biometric_flags=biometric_flags.to_dict(),
    )
    
    # Sign manifest
    if signing_key is None:
        signing_key = load_or_create_keypair(key_dir) if key_dir else load_or_create_keypair()
    manifest.sign(signing_key)
    
    return manifest


def save_manifest(manifest: SessionManifest, session_dir: str):
    """Save manifest to session directory."""
    manifest_path = os.path.join(session_dir, "provenance.json")
    with open(manifest_path, 'w') as f:
        f.write(manifest.to_json())


def load_manifest(session_dir: str) -> SessionManifest:
    """Load manifest from session directory."""
    manifest_path = os.path.join(session_dir, "provenance.json")
    with open(manifest_path, 'r') as f:
        return SessionManifest.from_json(f.read())


def manifest_exists(session_dir: str) -> bool:
    """Check if manifest exists in session directory."""
    return os.path.exists(os.path.join(session_dir, "provenance.json"))


def backfill_manifest(
    session_dir: str,
    player_pubkey: str = "",
    player_salt: str = "",
    consent_doc_path: Optional[str] = None,
    biometric_flags: Optional[BiometricFlags] = None,
    device_attestation: Optional[DeviceAttestation] = None,
    frame_hashes: Optional[Dict[int, str]] = None,
    key_dir: Optional[Union[str, Path]] = None,
) -> SessionManifest:
    """
    Backfill provenance manifest for existing session.
    
    If manifest already exists, loads and returns it.
    Otherwise, creates new manifest.
    
    Args:
        session_dir: Path to session directory
        player_pubkey: Player's public key
        player_salt: Salt for player ID
        consent_doc_path: Path to consent document
        biometric_flags: Biometric flags
        device_attestation: Device attestation
        frame_hashes: Frame hashes for Merkle tree
        key_dir: Directory for keys
        
    Returns:
        SessionManifest (signed)
    """
    if manifest_exists(session_dir):
        return load_manifest(session_dir)
    
    return build_manifest(
        session_dir=session_dir,
        player_pubkey=player_pubkey,
        player_salt=player_salt,
        consent_doc_path=consent_doc_path,
        biometric_flags=biometric_flags,
        device_attestation=device_attestation,
        frame_hashes=frame_hashes,
        key_dir=key_dir,
    )


if __name__ == "__main__":
    import tempfile
    
    # Demo - create a test session
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some test files
        for i in range(5):
            with open(os.path.join(tmpdir, f"frame_{i:05d}.jpg"), 'wb') as f:
                f.write(b"fake frame data " * 100)
        
        with open(os.path.join(tmpdir, "game_state.jsonl"), 'wb') as f:
            f.write(b'{"tick": 1, "player_pos": [0, 0]}\n')
        
        # Build manifest
        manifest = build_manifest(
            session_dir=tmpdir,
            session_id="test-session-001",
            player_pubkey="test_pubkey_12345",
            player_salt="random_salt",
            biometric_flags=BiometricFlags(
                voice_chat_captured=False,
                webcam_captured=False,
                facial_data=False,
                minor_consent_obtained=False,
                age_verified_18plus=True,
            ),
        )
        
        print("Manifest created:")
        print(manifest.to_json())
        
        # Verify signature
        print(f"\nSignature valid: {manifest.verify_signature()}")
