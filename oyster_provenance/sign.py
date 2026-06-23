"""
Ed25519 cryptographic signer for Oyster provenance.

Uses ed25519 (NOT RSA — smaller, modern) for signing session manifests.
"""

import json
import time
from pathlib import Path
from typing import Optional, Tuple, Union
from dataclasses import dataclass

# Use cryptography library for ed25519
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend


# Default paths
DEFAULT_KEY_DIR = Path.home() / ".oyster" / "keys"
DEFAULT_PRIVATE_KEY_PATH = DEFAULT_KEY_DIR / "signing_key.pem"
DEFAULT_PUBLIC_KEY_PATH = DEFAULT_KEY_DIR / "signing_key.pub"


@dataclass
class SigningKey:
    """Ed25519 signing key pair."""
    private_key: ed25519.Ed25519PrivateKey
    public_key: ed25519.Ed25519PublicKey
    
    @classmethod
    def generate(cls) -> 'SigningKey':
        """Generate a new ed25519 key pair."""
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        return cls(private_key=private_key, public_key=public_key)
    
    @classmethod
    def load(cls, private_key_path: Path = DEFAULT_PRIVATE_KEY_PATH) -> 'SigningKey':
        """Load signing key from file."""
        with open(private_key_path, 'rb') as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=None,
                backend=default_backend()
            )
        public_key = private_key.public_key()
        return cls(private_key=private_key, public_key=public_key)
    
    def save(self, private_key_path: Path = DEFAULT_PRIVATE_KEY_PATH,
             public_key_path: Path = DEFAULT_PUBLIC_KEY_PATH):
        """Save keys to files."""
        # Ensure directory exists
        private_key_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save private key
        private_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        with open(private_key_path, 'wb') as f:
            f.write(private_pem)
        
        # Save public key
        public_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        with open(public_key_path, 'wb') as f:
            f.write(public_pem)
    
    def public_key_hex(self) -> str:
        """Get public key as hex string."""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        ).hex()
    
    def sign(self, data: bytes) -> str:
        """Sign data, return signature as hex."""
        signature = self.private_key.sign(data)
        return signature.hex()
    
    def sign_json(self, data: dict) -> str:
        """Sign a JSON-serializable dict."""
        # Canonical JSON (sorted keys)
        json_bytes = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
        return self.sign(json_bytes)


@dataclass
class PublicKeyInfo:
    """Public key metadata for publishing."""
    key_id: str
    public_key_hex: str
    issued_at_utc: str
    expires_at_utc: Optional[str] = None
    version: str = "1.0"
    
    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "public_key": self.public_key_hex,
            "issued_at_utc": self.issued_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "version": self.version,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PublicKeyInfo':
        return cls(
            key_id=data["key_id"],
            public_key_hex=data["public_key"],
            issued_at_utc=data["issued_at_utc"],
            expires_at_utc=data.get("expires_at_utc"),
            version=data.get("version", "1.0"),
        )


def load_public_key(public_key_path: Path = DEFAULT_PUBLIC_KEY_PATH) -> ed25519.Ed25519PublicKey:
    """Load public key from file."""
    with open(public_key_path, 'rb') as f:
        public_key = serialization.load_pem_public_key(
            f.read(),
            backend=default_backend()
        )
    return public_key


def verify_signature(data: bytes, signature_hex: str, public_key_path: Path = DEFAULT_PUBLIC_KEY_PATH) -> bool:
    """
    Verify an ed25519 signature.
    
    Args:
        data: Original data that was signed
        signature_hex: Signature as hex string
        public_key_path: Path to public key file
        
    Returns:
        True if signature is valid
    """
    public_key = load_public_key(public_key_path)
    signature = bytes.fromhex(signature_hex)
    
    try:
        public_key.verify(signature, data)
        return True
    except Exception:
        return False


def verify_json_signature(data: dict, signature_hex: str, public_key_path: Path = DEFAULT_PUBLIC_KEY_PATH) -> bool:
    """Verify signature on JSON data."""
    json_bytes = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
    return verify_signature(json_bytes, signature_hex, public_key_path)


def generate_keypair(key_dir: Union[str, Path] = DEFAULT_KEY_DIR) -> Tuple[SigningKey, PublicKeyInfo]:
    """
    Generate a new keypair and save to key directory.
    
    Args:
        key_dir: Directory to save keys (can be str or Path)
        
    Returns:
        Tuple of (SigningKey, PublicKeyInfo)
    """
    # Convert to Path if string
    key_dir = Path(key_dir)
    key_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate key
    signing_key = SigningKey.generate()
    
    # Save keys
    private_path = key_dir / "signing_key.pem"
    public_path = key_dir / "signing_key.pub"
    signing_key.save(private_path, public_path)
    
    # Create metadata
    now = time.time()
    key_info = PublicKeyInfo(
        key_id=signing_key.public_key_hex()[:16],  # First 16 chars as ID
        public_key_hex=signing_key.public_key_hex(),
        issued_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        expires_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 365 * 24 * 60 * 60)),  # 1 year
    )
    
    # Save metadata
    metadata_path = key_dir / "pubkey.json"
    with open(metadata_path, 'w') as f:
        json.dump(key_info.to_dict(), f, indent=2)
    
    return signing_key, key_info


def load_or_create_keypair(key_dir: Union[str, Path] = DEFAULT_KEY_DIR) -> SigningKey:
    """
    Load existing keypair or create new one if not exists.
    
    Args:
        key_dir: Directory containing keys (can be str or Path)
        
    Returns:
        SigningKey
    """
    # Convert to Path if string
    key_dir = Path(key_dir)
    private_path = key_dir / "signing_key.pem"
    
    if private_path.exists():
        return SigningKey.load(private_path)
    else:
        key, _ = generate_keypair(key_dir)
        return key


def get_public_key_info(key_dir: Union[str, Path] = DEFAULT_KEY_DIR) -> PublicKeyInfo:
    """
    Load public key metadata.
    
    Args:
        key_dir: Directory containing keys (can be str or Path)
    """
    # Convert to Path if string
    key_dir = Path(key_dir)
    metadata_path = key_dir / "pubkey.json"
    
    if metadata_path.exists():
        with open(metadata_path, 'r') as f:
            return PublicKeyInfo.from_dict(json.load(f))
    
    # Generate if doesn't exist
    _, key_info = generate_keypair(key_dir)
    return key_info


if __name__ == "__main__":
    # Demo
    print("Generating new keypair...")
    signing_key, key_info = generate_keypair()
    
    print(f"Key ID: {key_info.key_id}")
    print(f"Public Key: {key_info.public_key_hex[:32]}...")
    print(f"Issued: {key_info.issued_at_utc}")
    
    # Sign something
    data = {"test": "data", "session_id": "12345"}
    signature = signing_key.sign_json(data)
    print(f"\nSignature: {signature[:32]}...")
    
    # Verify
    valid = verify_json_signature(data, signature)
    print(f"Verified: {valid}")
    
    # Tamper and verify fails
    data["test"] = "tampered"
    valid = verify_json_signature(data, signature)
    print(f"Verified after tamper: {valid}")
