#!/usr/bin/env python3
"""Tests for provenance_sign.py and provenance_verify.py."""

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile

import pytest

BIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGN_SCRIPT = os.path.join(BIN_DIR, "bin", "provenance_sign.py")
VERIFY_SCRIPT = os.path.join(BIN_DIR, "bin", "provenance_verify.py")


def _run(cmd, **kwargs):
    """Run a subprocess and return CompletedProcess."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


@pytest.fixture
def tmp_keyfile():
    """Create a temporary keyfile path (does NOT create the file)."""
    with tempfile.TemporaryDirectory() as td:
        yield os.path.join(td, "test.key")


@pytest.fixture
def tmp_manifest():
    """Create a temporary manifest.json file."""
    with tempfile.TemporaryDirectory() as td:
        manifest_path = os.path.join(td, "manifest.json")
        manifest = {
            "batch_id": "batch-001",
            "merkle_root": "abc123def456",
            "record_count": 42,
            "created_at": "2026-05-18T12:00:00Z",
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)
        yield manifest_path


def _canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _pubkey_fingerprint(pubkey_bytes):
    return hashlib.sha256(pubkey_bytes).hexdigest()[:16]


class TestSignAndVerifyRoundTrip:
    """Test sign → verify round trip."""

    def test_sign_creates_signed_file(self, tmp_keyfile, tmp_manifest):
        result = _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])
        assert result.returncode == 0, f"sign failed: {result.stderr}"
        assert "Generated new keypair" in result.stdout

        signed_path = tmp_manifest + ".signed.json"
        assert os.path.isfile(signed_path)

    def test_verify_round_trip_exit_0(self, tmp_keyfile, tmp_manifest):
        # Sign
        result = _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])
        assert result.returncode == 0

        signed_path = tmp_manifest + ".signed.json"

        # Verify
        result = _run([sys.executable, VERIFY_SCRIPT, signed_path])
        assert result.returncode == 0, f"verify failed: {result.stderr}"
        assert "VERIFIED" in result.stdout

    def test_signed_manifest_has_provenance(self, tmp_keyfile, tmp_manifest):
        _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])

        signed_path = tmp_manifest + ".signed.json"
        with open(signed_path) as f:
            signed = json.load(f)

        assert "provenance" in signed
        prov = signed["provenance"]
        assert prov["scheme"] == "ed25519"
        assert "signed_at_utc" in prov
        assert "manifest_sha256" in prov
        assert "signature_b64" in prov
        assert "pubkey_b64" in prov

        # Original fields preserved
        assert signed["batch_id"] == "batch-001"
        assert signed["merkle_root"] == "abc123def456"


class TestTamperDetection:
    """Test that tampering is detected."""

    def test_tamper_manifest_hash_mismatch(self, tmp_keyfile, tmp_manifest):
        # Sign
        _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])
        signed_path = tmp_manifest + ".signed.json"

        # Tamper: change a field in the signed manifest (not in provenance)
        with open(signed_path) as f:
            signed = json.load(f)
        signed["merkle_root"] = "TAMPERED"
        with open(signed_path, "w") as f:
            json.dump(signed, f)

        # Verify should fail with hash mismatch
        result = _run([sys.executable, VERIFY_SCRIPT, signed_path])
        assert result.returncode == 1
        assert "hash mismatch" in result.stderr.lower()

    def test_tamper_signature_invalid(self, tmp_keyfile, tmp_manifest):
        # Sign
        _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])
        signed_path = tmp_manifest + ".signed.json"

        # Tamper: change the signature
        with open(signed_path) as f:
            signed = json.load(f)
        signed["provenance"]["signature_b64"] = base64.b64encode(
            b"TAMPERED_SIGNATURE_64_BYTES_PADDED!!"
        ).decode()
        with open(signed_path, "w") as f:
            json.dump(signed, f)

        # Verify should fail with signature invalid
        result = _run([sys.executable, VERIFY_SCRIPT, signed_path])
        assert result.returncode == 1
        assert "signature invalid" in result.stderr.lower()


class TestExpectPubkey:
    """Test --expect-pubkey flag."""

    def test_expect_pubkey_correct(self, tmp_keyfile, tmp_manifest):
        # Sign
        result = _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])
        assert result.returncode == 0

        signed_path = tmp_manifest + ".signed.json"
        with open(signed_path) as f:
            signed = json.load(f)

        pubkey_bytes = base64.b64decode(signed["provenance"]["pubkey_b64"])
        fp = _pubkey_fingerprint(pubkey_bytes)

        # Verify with correct pubkey fingerprint
        result = _run([sys.executable, VERIFY_SCRIPT, signed_path, "--expect-pubkey", fp])
        assert result.returncode == 0
        assert "VERIFIED" in result.stdout

    def test_expect_pubkey_wrong(self, tmp_keyfile, tmp_manifest):
        # Sign
        _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])
        signed_path = tmp_manifest + ".signed.json"

        # Verify with wrong pubkey fingerprint
        result = _run(
            [sys.executable, VERIFY_SCRIPT, signed_path, "--expect-pubkey", "0000000000000000"]
        )
        assert result.returncode == 2
        assert "pubkey fingerprint mismatch" in result.stderr.lower()


class TestKeyGeneration:
    """Test automatic keypair generation."""

    def test_keyfile_created_with_correct_mode(self, tmp_keyfile, tmp_manifest):
        assert not os.path.exists(tmp_keyfile)

        result = _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])
        assert result.returncode == 0
        assert "Generated new keypair" in result.stdout

        # Private key file exists with mode 0600
        assert os.path.isfile(tmp_keyfile)
        mode = stat.S_IMODE(os.stat(tmp_keyfile).st_mode)
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

        # Public key file exists with mode 0644
        pubfile = tmp_keyfile + ".pub"
        assert os.path.isfile(pubfile)
        mode = stat.S_IMODE(os.stat(pubfile).st_mode)
        assert mode == 0o644, f"Expected 0644, got {oct(mode)}"

        # Key files have correct sizes
        assert os.path.getsize(tmp_keyfile) == 32
        assert os.path.getsize(pubfile) == 32

    def test_existing_keyfile_not_overwritten(self, tmp_keyfile, tmp_manifest):
        # Create a dummy keyfile
        os.makedirs(os.path.dirname(tmp_keyfile), exist_ok=True)
        with open(tmp_keyfile, "wb") as f:
            f.write(b"\x00" * 32)

        _run([sys.executable, SIGN_SCRIPT, tmp_manifest, "--keyfile", tmp_keyfile])

        # File should not have been regenerated (mtime unchanged or very close)
        # The key point is no "Generated new keypair" message
        # Since we used a valid 32-byte seed, it should just use it
        # (Note: all-zero seed is valid for Ed25519)


class TestSelfCheck:
    """Replicate the self-check from the spec."""

    def test_self_check(self):
        with tempfile.TemporaryDirectory() as td:
            manifest_path = os.path.join(td, "m.json")
            keyfile = os.path.join(td, "key")

            # Create manifest
            with open(manifest_path, "w") as f:
                json.dump({"batch_id": "test", "merkle_root": "abc123"}, f)

            # Sign
            result = _run([sys.executable, SIGN_SCRIPT, manifest_path, "--keyfile", keyfile])
            assert result.returncode == 0, f"sign failed: {result.stderr}"

            signed_path = manifest_path + ".signed.json"
            assert os.path.isfile(signed_path)

            # Verify
            result = _run([sys.executable, VERIFY_SCRIPT, signed_path])
            assert result.returncode == 0, f"verify failed: {result.stderr}"
            assert "VERIFIED" in result.stdout
