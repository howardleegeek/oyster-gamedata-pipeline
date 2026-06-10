#!/usr/bin/env python3
"""Sign a batch manifest with an Ed25519 keypair.

Usage:
    python3 bin/provenance_sign.py <manifest.json> [--keyfile <path>]

If the keyfile does not exist, a new Ed25519 keypair is generated and saved.
The signed manifest is written to <manifest>.signed.json.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

DEFAULT_KEYFILE = os.path.expanduser("~/.oyster-keys/provenance-ed25519.key")


def canonical_json(obj: dict) -> bytes:
    """Return canonical JSON bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pubkey_fingerprint(pubkey_bytes: bytes) -> str:
    """First 16 hex chars of sha256(public_key_bytes)."""
    return sha256_hex(pubkey_bytes)[:16]


def load_or_generate_keypair(keyfile: str):
    """Load existing keypair from keyfile, or generate a new one.

    keyfile stores the 32-byte raw secret seed.
    keyfile.pub stores the 32-byte raw public key.
    """
    if not os.path.exists(keyfile):
        # Generate new keypair
        private_key = Ed25519PrivateKey.generate()
        seed = private_key.private_bytes_raw()  # 32 bytes
        pubkey = private_key.public_key().public_bytes_raw()  # 32 bytes

        keydir = os.path.dirname(keyfile)
        if keydir:
            os.makedirs(keydir, exist_ok=True)

        # Write private key (0600)
        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, seed)
        finally:
            os.close(fd)

        # Write public key (0644)
        pubfile = keyfile + ".pub"
        fd = os.open(pubfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, pubkey)
        finally:
            os.close(fd)

        print(f"Generated new keypair at: {keyfile}")
        return private_key, pubkey

    # Load existing keypair
    with open(keyfile, "rb") as f:
        seed = f.read()
    if len(seed) != 32:
        print(f"ERROR: keyfile must contain exactly 32 bytes, got {len(seed)}", file=sys.stderr)
        sys.exit(1)

    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    pubkey = private_key.public_key().public_bytes_raw()
    return private_key, pubkey


def sign_manifest(manifest_path: str, keyfile: str) -> str:
    """Sign a manifest file and write the signed version.

    Returns the path to the signed manifest.
    """
    # Load or generate keypair
    private_key, pubkey_bytes = load_or_generate_keypair(keyfile)

    # Read original manifest
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Compute canonical JSON and its SHA-256
    canonical = canonical_json(manifest)
    manifest_hash = sha256_hex(canonical)

    # Sign the hash bytes (not the hex string)
    hash_bytes = bytes.fromhex(manifest_hash)
    signature = private_key.sign(hash_bytes)  # 64 bytes

    # Build signed manifest
    signed = dict(manifest)  # shallow copy
    signed["provenance"] = {
        "scheme": "ed25519",
        "signed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest_sha256": manifest_hash,
        "signature_b64": base64.b64encode(signature).decode("ascii"),
        "pubkey_b64": base64.b64encode(pubkey_bytes).decode("ascii"),
    }

    # Write signed manifest
    signed_path = manifest_path + ".signed.json"
    with open(signed_path, "w", encoding="utf-8") as f:
        json.dump(signed, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")

    fp = pubkey_fingerprint(pubkey_bytes)
    print(f"Signed manifest: {signed_path}")
    print(f"Pubkey fingerprint: {fp}")

    return signed_path


def main():
    parser = argparse.ArgumentParser(description="Sign a batch manifest with Ed25519")
    parser.add_argument("manifest", help="Path to manifest.json")
    parser.add_argument(
        "--keyfile", default=DEFAULT_KEYFILE, help="Path to Ed25519 private key file"
    )
    args = parser.parse_args()

    if not os.path.isfile(args.manifest):
        print(f"ERROR: manifest file not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)

    sign_manifest(args.manifest, args.keyfile)


if __name__ == "__main__":
    main()
