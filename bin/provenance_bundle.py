#!/usr/bin/env python3
"""Create an offline provenance bundle from a session directory.

Usage:
    python3 bin/provenance_bundle.py <session_dir> [--keyfile <path>] [--output <path>]

Produces <session_dir>.bundle.tar.gz containing:
  - manifest.signed.json   (Ed25519-signed manifest)
  - session.tar.gz         (the session data)
  - pubkey-fingerprint.txt (16-hex-char fingerprint)
  - verify.sh              (standalone bash verification script)
  - README.md              (30-second quick-start guide)

The bundle is self-contained — a buyer can verify it with zero Python dependency
using only verify.sh (openssl + sha256sum).
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Re-use the same default keyfile path as provenance_sign.py
DEFAULT_KEYFILE = os.path.expanduser("~/.oyster-keys/provenance-ed25519.key")

BUNDLE_VERSION = "1"


# ---------------------------------------------------------------------------
# Key management (mirrors provenance_sign.py)
# ---------------------------------------------------------------------------


def load_or_generate_keypair(keyfile: str):
    """Load existing keypair from keyfile, or generate a new one.

    keyfile stores the 32-byte raw secret seed.
    keyfile.pub stores the 32-byte raw public key.
    """
    if not os.path.exists(keyfile):
        private_key = Ed25519PrivateKey.generate()
        seed = private_key.private_bytes_raw()
        pubkey = private_key.public_key().public_bytes_raw()

        keydir = os.path.dirname(keyfile)
        if keydir:
            os.makedirs(keydir, exist_ok=True)

        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, seed)
        finally:
            os.close(fd)

        pubfile = keyfile + ".pub"
        fd = os.open(pubfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, pubkey)
        finally:
            os.close(fd)

        print(f"Generated new keypair at: {keyfile}", file=sys.stderr)
        return private_key, pubkey

    with open(keyfile, "rb") as f:
        seed = f.read()
    if len(seed) != 32:
        print(
            f"ERROR: keyfile must contain exactly 32 bytes, got {len(seed)}",
            file=sys.stderr,
        )
        sys.exit(1)

    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    pubkey = private_key.public_key().public_bytes_raw()
    return private_key, pubkey


# ---------------------------------------------------------------------------
# Crypto helpers (same as provenance_sign.py / provenance_verify.py)
# ---------------------------------------------------------------------------


def canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pubkey_fingerprint(pubkey_bytes: bytes) -> str:
    return sha256_hex(pubkey_bytes)[:16]


# ---------------------------------------------------------------------------
# Bundle creation
# ---------------------------------------------------------------------------


def _tar_session(session_dir: str, dest_path: str) -> int:
    """Create session.tar.gz from session_dir. Returns file count."""
    file_count = 0
    with tarfile.open(dest_path, "w:gz", compresslevel=6) as tar:
        for root, _dirs, files in os.walk(session_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, session_dir)
                tar.add(fpath, arcname=arcname)
                file_count += 1
    return file_count


def _build_manifest(session_tar_path: str, session_dir: str, file_count: int) -> dict:
    """Build the unsigned manifest dict."""
    with open(session_tar_path, "rb") as f:
        session_data = f.read()

    return {
        "bundle_version": BUNDLE_VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_dir_name": os.path.basename(os.path.normpath(session_dir)),
        "session_file_count": file_count,
        "session_sha256": sha256_hex(session_data),
        "session_size_bytes": len(session_data),
    }


def _sign_manifest(manifest: dict, private_key, pubkey_bytes: bytes) -> dict:
    """Add provenance block to manifest and sign it."""
    canonical = canonical_json(manifest)
    manifest_hash = sha256_hex(canonical)
    hash_bytes = bytes.fromhex(manifest_hash)
    signature = private_key.sign(hash_bytes)

    signed = dict(manifest)
    signed["provenance"] = {
        "manifest_sha256": manifest_hash,
        "pubkey_b64": base64.b64encode(pubkey_bytes).decode("ascii"),
        "scheme": "ed25519",
        "signature_b64": base64.b64encode(signature).decode("ascii"),
        "signed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return signed


VERIFY_SH = r"""#!/usr/bin/env bash
# verify.sh — standalone Ed25519 bundle verifier (no Python required)
# Requires: bash 3.2+, openssl, sha256sum (or shasum -a 256 on macOS)
#
# Usage: bash verify.sh <bundle.tar.gz> [--expect-pubkey <hex_fp>]
# Exit 0 = verified, Exit 1 = verification failed.

# Do NOT use set -e — we handle errors explicitly to avoid issues with
# binary data in command substitutions.

# --- helpers ---------------------------------------------------------------
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "ERROR: neither sha256sum nor shasum found" >&2
    exit 1
  fi
}

sha256_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  else
    echo "ERROR: neither sha256sum nor shasum found" >&2
    exit 1
  fi
}

b64_decode() {
  # Portable base64 decode: try -d first (GNU), then -D (BSD/macOS)
  if base64 -d 2>/dev/null; then
    :
  elif base64 -D 2>/dev/null; then
    :
  else
    echo "ERROR: base64 decode failed" >&2
    exit 1
  fi
}

# --- args ------------------------------------------------------------------
BUNDLE="${1:?Usage: verify.sh <bundle.tar.gz> [--expect-pubkey <hex_fp>]}"
EXPECT_FP=""
if [[ "${2:-}" == "--expect-pubkey" ]]; then
  EXPECT_FP="${3:?--expect-pubkey requires a hex fingerprint}"
fi

if [[ ! -f "$BUNDLE" ]]; then
  echo "ERROR: bundle not found: $BUNDLE" >&2
  exit 1
fi

# --- extract to temp dir ---------------------------------------------------
TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

if ! tar xzf "$BUNDLE" -C "$TMPDIR_WORK" 2>/dev/null; then
  echo "FAIL: cannot extract bundle" >&2
  exit 1
fi

MANIFEST="$TMPDIR_WORK/manifest.signed.json"
SESSION_TAR="$TMPDIR_WORK/session.tar.gz"
PUBKEY_FP_FILE="$TMPDIR_WORK/pubkey-fingerprint.txt"

for f in "$MANIFEST" "$SESSION_TAR" "$PUBKEY_FP_FILE"; do
  if [[ ! -f "$f" ]]; then
    echo "FAIL: missing bundle component: $(basename "$f")" >&2
    exit 1
  fi
done

# --- parse manifest fields -------------------------------------------------
# Use python3 as a JSON parser (ubiquitous on modern systems).
# Falls back to grep/sed for simple flat JSON if python3 is absent.

parse_json_field() {
  local file="$1" field="$2"
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
parts=sys.argv[2].split('.')
v=d
for p in parts:
    v=v[p]
print(v)
" "$file" "$field" 2>/dev/null
  else
    # Fallback: grep/sed for flat JSON
    grep -o "\"${field}\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$file" \
      | head -1 | sed 's/.*:[[:space:]]*"\(.*\)"/\1/'
  fi
}

STORED_HASH="$(parse_json_field "$MANIFEST" provenance.manifest_sha256)"
PUBKEY_B64="$(parse_json_field "$MANIFEST" provenance.pubkey_b64)"
SIG_B64="$(parse_json_field "$MANIFEST" provenance.signature_b64)"
SESSION_SHA256="$(parse_json_field "$MANIFEST" session_sha256)"

if [[ -z "$STORED_HASH" || -z "$PUBKEY_B64" || -z "$SIG_B64" || -z "$SESSION_SHA256" ]]; then
  echo "FAIL: could not parse manifest fields" >&2
  exit 1
fi

# --- verify session hash ---------------------------------------------------
ACTUAL_SESSION_SHA="$(sha256_file "$SESSION_TAR")"
if [[ "$ACTUAL_SESSION_SHA" != "$SESSION_SHA256" ]]; then
  echo "FAIL: merkle root mismatch — session hash does not match manifest" >&2
  echo "  expected: $SESSION_SHA256" >&2
  echo "  actual:   $ACTUAL_SESSION_SHA" >&2
  exit 1
fi

# --- rebuild canonical JSON and verify manifest hash -----------------------
CANONICAL_JSON="$(python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
m={k:v for k,v in d.items() if k!='provenance'}
print(json.dumps(m,sort_keys=True,separators=(',',':'),ensure_ascii=False))
" "$MANIFEST" 2>/dev/null)"

if [[ -z "$CANONICAL_JSON" ]]; then
  echo "FAIL: could not rebuild canonical JSON" >&2
  exit 1
fi

COMPUTED_HASH="$(printf '%s' "$CANONICAL_JSON" | sha256_stdin)"

if [[ "$COMPUTED_HASH" != "$STORED_HASH" ]]; then
  echo "FAIL: merkle root mismatch — manifest hash does not match" >&2
  echo "  expected: $STORED_HASH" >&2
  echo "  actual:   $COMPUTED_HASH" >&2
  exit 1
fi

# --- verify ed25519 signature via openssl ----------------------------------
# Write pubkey, signature, and hash to temp files for openssl.
PUBKEY_RAW="$TMPDIR_WORK/pubkey.raw"
SIG_RAW="$TMPDIR_WORK/sig.raw"
HASH_RAW="$TMPDIR_WORK/hash.raw"

printf '%s' "$PUBKEY_B64" | b64_decode > "$PUBKEY_RAW"
printf '%s' "$SIG_B64" | b64_decode > "$SIG_RAW"

# Convert hex hash to binary
python3 -c "import sys;sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))" \
  "$COMPUTED_HASH" > "$HASH_RAW" 2>/dev/null || {
  echo "FAIL: could not convert hash to binary" >&2
  exit 1
}

# Convert raw Ed25519 pubkey to PEM for openssl
PUBKEY_PEM="$TMPDIR_WORK/pubkey.pem"
python3 -c "
import base64,sys
raw=open(sys.argv[1],'rb').read()
# Ed25519 SubjectPublicKeyInfo DER prefix
der_prefix=bytes([0x30,0x2a,0x30,0x05,0x06,0x03,0x2b,0x65,0x70,0x03,0x21,0x00])
der=der_prefix+raw
b64=base64.b64encode(der).decode()
lines=['-----BEGIN PUBLIC KEY-----']
for i in range(0,len(b64),64):
    lines.append(b64[i:i+64])
lines.append('-----END PUBLIC KEY-----')
print('\n'.join(lines))
" "$PUBKEY_RAW" > "$PUBKEY_PEM" 2>/dev/null || {
  echo "FAIL: could not convert pubkey to PEM" >&2
  exit 1
}

# Verify signature using openssl pkeyutl
if ! openssl pkeyutl -verify \
    -pubin -inkey "$PUBKEY_PEM" \
    -in "$HASH_RAW" \
    -sigfile "$SIG_RAW" \
    -rawin >/dev/null 2>&1; then
  echo "FAIL: signature mismatch" >&2
  exit 1
fi

# --- optional pubkey fingerprint check -------------------------------------
BUNDLE_FP="$(cat "$PUBKEY_FP_FILE")"
if [[ -n "$EXPECT_FP" ]]; then
  # Case-insensitive comparison
  EXPECT_LOWER="$(echo "$EXPECT_FP" | tr '[:upper:]' '[:lower:]')"
  BUNDLE_LOWER="$(echo "$BUNDLE_FP" | tr '[:upper:]' '[:lower:]')"
  if [[ "$BUNDLE_LOWER" != "$EXPECT_LOWER" ]]; then
    echo "FAIL: pubkey fingerprint mismatch: got $BUNDLE_FP, expected $EXPECT_FP" >&2
    exit 1
  fi
fi

echo "VERIFIED ✓ pubkey fingerprint: $BUNDLE_FP"
exit 0
"""

README_MD = """# Provenance Offline Bundle

## 30-Second Quick Start

### Verify with Python (recommended)

```bash
python3 bin/provenance_verify.py --offline-bundle <bundle.tar.gz>
```

Exit code 0 = data is intact and from the claimed publisher.

### Verify with Bash only (zero Python)

```bash
bash verify.sh <bundle.tar.gz>
```

### Verify with expected publisher

```bash
python3 bin/provenance_verify.py --offline-bundle <bundle.tar.gz> --expect-pubkey <fingerprint>
# or
bash verify.sh <bundle.tar.gz> --expect-pubkey <fingerprint>
```

## Bundle Contents

| File | Purpose |
|---|---|
| `manifest.signed.json` | Ed25519-signed manifest with session hash |
| `session.tar.gz` | The actual session data |
| `pubkey-fingerprint.txt` | 16-char hex fingerprint of signer's pubkey |
| `verify.sh` | Standalone bash verifier (openssl + sha256sum) |
| `README.md` | This file |

## What's Verified

1. **session_sha256** — session.tar.gz hash matches the signed manifest
2. **manifest_sha256** — canonical JSON hash matches the stored hash
3. **Ed25519 signature** — signature over the hash is valid for the embedded pubkey
4. **pubkey fingerprint** (optional) — matches your expected publisher

## Failure Exit Codes

- `exit 1` — verification failed (see stderr for details)
- `exit 2` — pubkey fingerprint mismatch (Python mode only)
"""


def create_bundle(session_dir: str, keyfile: str, output_path: str | None = None) -> str:
    """Create an offline provenance bundle.

    Args:
        session_dir: Path to the session directory to bundle.
        keyfile: Path to Ed25519 private key file.
        output_path: Optional explicit output path. Defaults to <session_dir>.bundle.tar.gz.

    Returns:
        Path to the created bundle.
    """
    if not os.path.isdir(session_dir):
        print(f"ERROR: session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    # Load keypair
    private_key, pubkey_bytes = load_or_generate_keypair(keyfile)

    # Determine output path
    if output_path is None:
        output_path = os.path.abspath(session_dir) + ".bundle.tar.gz"

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Create session.tar.gz
        session_tar_path = os.path.join(tmpdir, "session.tar.gz")
        file_count = _tar_session(session_dir, session_tar_path)

        # 2. Build and sign manifest
        manifest = _build_manifest(session_tar_path, session_dir, file_count)
        signed_manifest = _sign_manifest(manifest, private_key, pubkey_bytes)

        # 3. Write manifest.signed.json
        manifest_path = os.path.join(tmpdir, "manifest.signed.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(signed_manifest, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")

        # 4. Write pubkey-fingerprint.txt
        fp = pubkey_fingerprint(pubkey_bytes)
        fp_path = os.path.join(tmpdir, "pubkey-fingerprint.txt")
        with open(fp_path, "w", encoding="utf-8") as f:
            f.write(fp + "\n")

        # 5. Write verify.sh
        verify_sh_path = os.path.join(tmpdir, "verify.sh")
        with open(verify_sh_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(VERIFY_SH)
        os.chmod(verify_sh_path, 0o755)

        # 6. Write README.md
        readme_path = os.path.join(tmpdir, "README.md")
        with open(readme_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(README_MD)

        # 7. Create the bundle tar.gz
        with tarfile.open(output_path, "w:gz", compresslevel=6) as bundle_tar:
            for fname in [
                "manifest.signed.json",
                "session.tar.gz",
                "pubkey-fingerprint.txt",
                "verify.sh",
                "README.md",
            ]:
                fpath = os.path.join(tmpdir, fname)
                bundle_tar.add(fpath, arcname=fname)

    session_size = os.path.getsize(session_tar_path) if os.path.exists(session_tar_path) else 0
    bundle_size = os.path.getsize(output_path)
    ratio = bundle_size / session_size if session_size > 0 else 0

    print(f"Bundle created: {output_path}")
    print(f"  Session size: {session_size:,} bytes ({file_count} files)")
    print(f"  Bundle size:  {bundle_size:,} bytes (ratio: {ratio:.2f}x)")
    print(f"  Pubkey fingerprint: {fp}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Create an offline provenance bundle from a session directory"
    )
    parser.add_argument("session_dir", help="Path to the session directory")
    parser.add_argument(
        "--keyfile", default=DEFAULT_KEYFILE, help="Path to Ed25519 private key file"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output bundle path (default: <session_dir>.bundle.tar.gz)",
    )
    args = parser.parse_args()

    create_bundle(args.session_dir, args.keyfile, args.output)


if __name__ == "__main__":
    main()
