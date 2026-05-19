#!/usr/bin/env python3
"""Verify an Ed25519-signed batch manifest.

Usage:
    python3 bin/provenance_verify.py <signed_manifest.json> [--expect-pubkey <hex>]
    python3 bin/provenance_verify.py --offline-bundle <bundle.tar.gz> [--expect-pubkey <hex>]

Exit codes:
    0 - Verification successful
    1 - Verification failed (hash mismatch or signature invalid)
    2 - Pubkey fingerprint mismatch (when --expect-pubkey is provided)
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import tarfile
import tempfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical_json(obj: dict) -> bytes:
    """Return canonical JSON bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pubkey_fingerprint(pubkey_bytes: bytes) -> str:
    """First 16 hex chars of sha256(public_key_bytes)."""
    return sha256_hex(pubkey_bytes)[:16]


def verify_manifest(signed_path: str, expect_pubkey_hex: str | None = None) -> int:
    """Verify a signed manifest.

    Returns exit code: 0=OK, 1=verify fail, 2=pubkey mismatch.
    """
    # Read signed manifest
    with open(signed_path, "r", encoding="utf-8") as f:
        signed = json.load(f)

    # Extract provenance
    if "provenance" not in signed:
        print("FAIL: no 'provenance' field in manifest", file=sys.stderr)
        return 1

    prov = signed["provenance"]

    if prov.get("scheme") != "ed25519":
        print(f"FAIL: unsupported scheme '{prov.get('scheme')}'", file=sys.stderr)
        return 1

    try:
        signature_b64 = prov["signature_b64"]
        pubkey_b64 = prov["pubkey_b64"]
        stored_hash = prov["manifest_sha256"]
    except KeyError as e:
        print(f"FAIL: missing provenance field: {e}", file=sys.stderr)
        return 1

    # Decode pubkey
    try:
        pubkey_bytes = base64.b64decode(pubkey_b64)
        public_key = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
    except Exception as e:
        print(f"FAIL: invalid public key: {e}", file=sys.stderr)
        return 1

    # Check expected pubkey fingerprint
    if expect_pubkey_hex is not None:
        fp = pubkey_fingerprint(pubkey_bytes)
        if fp != expect_pubkey_hex.lower():
            print(
                f"FAIL: pubkey fingerprint mismatch: got {fp}, expected {expect_pubkey_hex}",
                file=sys.stderr,
            )
            return 2

    # Recompute manifest hash (strip provenance field)
    manifest = {k: v for k, v in signed.items() if k != "provenance"}
    canonical = canonical_json(manifest)
    recomputed_hash = sha256_hex(canonical)

    # Check hash matches
    if recomputed_hash != stored_hash:
        print(
            f"FAIL: hash mismatch — stored: {stored_hash}, recomputed: {recomputed_hash}",
            file=sys.stderr,
        )
        return 1

    # Verify signature
    try:
        signature = base64.b64decode(signature_b64)
        hash_bytes = bytes.fromhex(stored_hash)
        public_key.verify(signature, hash_bytes)
    except InvalidSignature:
        print("FAIL: signature invalid", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FAIL: signature verification error: {e}", file=sys.stderr)
        return 1

    fp = pubkey_fingerprint(pubkey_bytes)
    print(f"VERIFIED ✓ pubkey fingerprint: {fp}")
    return 0


def verify_offline_bundle(
    bundle_path: str, expect_pubkey_hex: str | None = None
) -> int:
    """Verify an offline provenance bundle.

    Extracts the bundle to a temp directory, verifies the Ed25519 signature
    on the manifest, and checks the session tarball hash.

    Returns exit code: 0=OK, 1=verify fail, 2=pubkey mismatch.
    """
    if not os.path.isfile(bundle_path):
        print(f"FAIL: bundle file not found: {bundle_path}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract bundle
        try:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=tmpdir)
        except Exception as e:
            print(f"FAIL: cannot extract bundle: {e}", file=sys.stderr)
            return 1

        manifest_path = os.path.join(tmpdir, "manifest.signed.json")
        session_tar_path = os.path.join(tmpdir, "session.tar.gz")

        if not os.path.isfile(manifest_path):
            print("FAIL: bundle missing manifest.signed.json", file=sys.stderr)
            return 1

        if not os.path.isfile(session_tar_path):
            print("FAIL: bundle missing session.tar.gz", file=sys.stderr)
            return 1

        # Read signed manifest
        with open(manifest_path, "r", encoding="utf-8") as f:
            signed = json.load(f)

        # Extract provenance
        if "provenance" not in signed:
            print("FAIL: no 'provenance' field in manifest", file=sys.stderr)
            return 1

        prov = signed["provenance"]

        if prov.get("scheme") != "ed25519":
            print(f"FAIL: unsupported scheme '{prov.get('scheme')}'", file=sys.stderr)
            return 1

        try:
            signature_b64 = prov["signature_b64"]
            pubkey_b64 = prov["pubkey_b64"]
            stored_hash = prov["manifest_sha256"]
        except KeyError as e:
            print(f"FAIL: missing provenance field: {e}", file=sys.stderr)
            return 1

        # Decode pubkey
        try:
            pubkey_bytes = base64.b64decode(pubkey_b64)
            public_key = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
        except Exception as e:
            print(f"FAIL: invalid public key: {e}", file=sys.stderr)
            return 1

        # Check expected pubkey fingerprint
        if expect_pubkey_hex is not None:
            fp = pubkey_fingerprint(pubkey_bytes)
            if fp != expect_pubkey_hex.lower():
                print(
                    f"FAIL: pubkey fingerprint mismatch: got {fp}, expected {expect_pubkey_hex}",
                    file=sys.stderr,
                )
                return 2

        # Verify session tarball hash (merkle root check)
        session_sha256 = signed.get("session_sha256")
        if session_sha256 is None:
            print("FAIL: manifest missing session_sha256", file=sys.stderr)
            return 1

        with open(session_tar_path, "rb") as f:
            actual_session_hash = sha256_hex(f.read())

        if actual_session_hash != session_sha256:
            print(
                "FAIL: merkle root mismatch — session hash does not match manifest",
                file=sys.stderr,
            )
            print(f"  expected: {session_sha256}", file=sys.stderr)
            print(f"  actual:   {actual_session_hash}", file=sys.stderr)
            return 1

        # Recompute manifest hash (strip provenance field)
        manifest = {k: v for k, v in signed.items() if k != "provenance"}
        canonical = canonical_json(manifest)
        recomputed_hash = sha256_hex(canonical)

        # Check hash matches
        if recomputed_hash != stored_hash:
            print(
                "FAIL: merkle root mismatch — manifest hash does not match",
                file=sys.stderr,
            )
            print(f"  expected: {stored_hash}", file=sys.stderr)
            print(f"  actual:   {recomputed_hash}", file=sys.stderr)
            return 1

        # Verify Ed25519 signature
        try:
            signature = base64.b64decode(signature_b64)
            hash_bytes = bytes.fromhex(stored_hash)
            public_key.verify(signature, hash_bytes)
        except InvalidSignature:
            print("FAIL: signature mismatch", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"FAIL: signature verification error: {e}", file=sys.stderr)
            return 1

    fp = pubkey_fingerprint(pubkey_bytes)
    print(f"VERIFIED ✓ pubkey fingerprint: {fp}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Verify an Ed25519-signed batch manifest or offline bundle"
    )
    parser.add_argument(
        "signed_manifest",
        nargs="?",
        default=None,
        help="Path to signed manifest JSON (omit when using --offline-bundle)",
    )
    parser.add_argument(
        "--offline-bundle",
        default=None,
        help="Path to offline provenance bundle (.tar.gz)",
    )
    parser.add_argument(
        "--expect-pubkey",
        default=None,
        help="Expected pubkey fingerprint (first 16 hex chars of sha256(pubkey))",
    )
    args = parser.parse_args()

    if args.offline_bundle is not None:
        exit_code = verify_offline_bundle(args.offline_bundle, args.expect_pubkey)
    elif args.signed_manifest is not None:
        exit_code = verify_manifest(args.signed_manifest, args.expect_pubkey)
    else:
        parser.print_help()
        sys.exit(1)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
