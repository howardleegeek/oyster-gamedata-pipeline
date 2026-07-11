#!/usr/bin/env python3
"""Tests for provenance_bundle.py and the --offline-bundle mode of provenance_verify.py.

Uses real Ed25519 keypairs (no mocks).
"""

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile

import pytest

BIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE_SCRIPT = os.path.join(BIN_DIR, "bin", "provenance_bundle.py")
VERIFY_SCRIPT = os.path.join(BIN_DIR, "bin", "provenance_verify.py")
SIGN_SCRIPT = os.path.join(BIN_DIR, "bin", "provenance_sign.py")


def _run(cmd, **kwargs):
    """Run a subprocess and return CompletedProcess."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _pubkey_fingerprint(pubkey_bytes: bytes) -> str:
    return hashlib.sha256(pubkey_bytes).hexdigest()[:16]


@pytest.fixture
def tmp_keyfile():
    """Create a temporary keyfile path (does NOT create the file)."""
    with tempfile.TemporaryDirectory() as td:
        yield os.path.join(td, "test.key")


@pytest.fixture
def real_keypair(tmp_keyfile):
    """Generate a real Ed25519 keypair and return (keyfile_path, pubkey_bytes)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    seed = private_key.private_bytes_raw()
    pubkey = private_key.public_key().public_bytes_raw()

    with open(tmp_keyfile, "wb") as f:
        f.write(seed)
    with open(tmp_keyfile + ".pub", "wb") as f:
        f.write(pubkey)

    return tmp_keyfile, pubkey


@pytest.fixture
def minimal_session_dir():
    """Create a minimal session directory with some files."""
    with tempfile.TemporaryDirectory() as td:
        session_dir = os.path.join(td, "session_001")
        os.makedirs(session_dir)

        # Create a few files to simulate a session
        with open(os.path.join(session_dir, "metadata.json"), "w") as f:
            json.dump(
                {
                    "session_id": "sess-001",
                    "game": "test-game",
                    "duration_seconds": 120,
                },
                f,
            )

        with open(os.path.join(session_dir, "game_state.jsonl"), "w") as f:
            for i in range(10):
                f.write(json.dumps({"tick": i, "score": i * 10}) + "\n")

        with open(os.path.join(session_dir, "inputs.jsonl"), "w") as f:
            f.write(json.dumps({"timestamp": "2026-01-01T00:00:00Z", "key": "W"}) + "\n")

        # Create a subdirectory with a file
        depth_dir = os.path.join(session_dir, "depth")
        os.makedirs(depth_dir)
        with open(os.path.join(depth_dir, ".source"), "w") as f:
            f.write("ci_fixture\n")

        yield session_dir


class TestBundleCreation:
    """Test provenance_bundle.py creates valid bundles."""

    def test_bundle_creates_output_file(self, real_keypair, minimal_session_dir):
        keyfile, _ = real_keypair
        result = _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])
        assert result.returncode == 0, f"bundle failed: {result.stderr}"

        expected_bundle = minimal_session_dir + ".bundle.tar.gz"
        assert os.path.isfile(expected_bundle), "bundle file not created"

    def test_bundle_custom_output_path(self, real_keypair, minimal_session_dir):
        keyfile, _ = real_keypair
        output_path = os.path.join(minimal_session_dir, "..", "custom_bundle.tar.gz")
        result = _run(
            [
                sys.executable,
                BUNDLE_SCRIPT,
                minimal_session_dir,
                "--keyfile",
                keyfile,
                "--output",
                output_path,
            ]
        )
        assert result.returncode == 0, f"bundle failed: {result.stderr}"
        assert os.path.isfile(output_path)

    def test_bundle_contains_required_files(self, real_keypair, minimal_session_dir):
        keyfile, _ = real_keypair
        result = _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])
        assert result.returncode == 0

        bundle_path = minimal_session_dir + ".bundle.tar.gz"
        with tarfile.open(bundle_path, "r:gz") as tar:
            names = tar.getnames()

        required = [
            "manifest.signed.json",
            "session.tar.gz",
            "pubkey-fingerprint.txt",
            "verify.sh",
            "README.md",
        ]
        for req in required:
            assert req in names, f"bundle missing {req}"

    def test_bundle_manifest_is_signed(self, real_keypair, minimal_session_dir):
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extract("manifest.signed.json", path=td)

            manifest_path = os.path.join(td, "manifest.signed.json")
            with open(manifest_path) as f:
                manifest = json.load(f)

        assert "provenance" in manifest
        prov = manifest["provenance"]
        assert prov["scheme"] == "ed25519"
        assert "signature_b64" in prov
        assert "pubkey_b64" in prov
        assert "manifest_sha256" in prov

    def test_bundle_pubkey_fingerprint_matches(self, real_keypair, minimal_session_dir):
        keyfile, pubkey_bytes = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extract("pubkey-fingerprint.txt", path=td)

            with open(os.path.join(td, "pubkey-fingerprint.txt")) as f:
                fp_in_bundle = f.read().strip()

        expected_fp = _pubkey_fingerprint(pubkey_bytes)
        assert fp_in_bundle == expected_fp

    def test_bundle_size_within_limit(self, real_keypair, minimal_session_dir):
        """Bundle should be ≤ 1.2× session original size."""
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"
        bundle_size = os.path.getsize(bundle_path)

        # Compute original session size
        session_size = 0
        for root, _dirs, files in os.walk(minimal_session_dir):
            for fname in files:
                session_size += os.path.getsize(os.path.join(root, fname))

        # Allow 1.2× ratio (bundle has metadata + overhead)
        assert (
            bundle_size <= session_size * 1.2 + 5000
        ), f"bundle too large: {bundle_size} vs session {session_size}"

    def test_bundle_session_dir_not_found(self, real_keypair):
        keyfile, _ = real_keypair
        result = _run(
            [
                sys.executable,
                BUNDLE_SCRIPT,
                "/nonexistent/session",
                "--keyfile",
                keyfile,
            ]
        )
        assert result.returncode != 0


class TestOfflineBundleVerify:
    """Test provenance_verify.py --offline-bundle mode."""

    def test_verify_valid_bundle_exit_0(self, real_keypair, minimal_session_dir):
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"
        result = _run([sys.executable, VERIFY_SCRIPT, "--offline-bundle", bundle_path])
        assert result.returncode == 0, f"verify failed: {result.stderr}"
        assert "VERIFIED" in result.stdout

    def test_verify_with_expect_pubkey_match(self, real_keypair, minimal_session_dir):
        keyfile, pubkey_bytes = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"
        fp = _pubkey_fingerprint(pubkey_bytes)
        result = _run(
            [
                sys.executable,
                VERIFY_SCRIPT,
                "--offline-bundle",
                bundle_path,
                "--expect-pubkey",
                fp,
            ]
        )
        assert result.returncode == 0, f"verify failed: {result.stderr}"

    def test_verify_with_expect_pubkey_mismatch_exit_2(self, real_keypair, minimal_session_dir):
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"
        result = _run(
            [
                sys.executable,
                VERIFY_SCRIPT,
                "--offline-bundle",
                bundle_path,
                "--expect-pubkey",
                "deadbeefdeadbeef",
            ]
        )
        assert result.returncode == 2, f"expected exit 2, got {result.returncode}: {result.stderr}"
        assert "pubkey fingerprint mismatch" in result.stderr.lower()

    def test_verify_tampered_session_exit_1(self, real_keypair, minimal_session_dir):
        """Tamper with session.tar.gz inside the bundle."""
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"

        # Extract, tamper, re-pack
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=td)

            # Tamper with session.tar.gz
            session_tar = os.path.join(td, "session.tar.gz")
            with open(session_tar, "ab") as f:
                f.write(b"TAMPERED DATA")

            # Repack bundle
            tampered_bundle = os.path.join(td, "tampered.bundle.tar.gz")
            with tarfile.open(tampered_bundle, "w:gz") as tar:
                for fname in os.listdir(td):
                    fpath = os.path.join(td, fname)
                    if os.path.isfile(fpath):
                        tar.add(fpath, arcname=fname)

            result = _run([sys.executable, VERIFY_SCRIPT, "--offline-bundle", tampered_bundle])
            assert result.returncode == 1, f"expected exit 1, got {result.returncode}"
            assert (
                "merkle root mismatch" in result.stderr.lower()
                or "signature mismatch" in result.stderr.lower()
            )

    def test_verify_tampered_manifest_exit_1(self, real_keypair, minimal_session_dir):
        """Tamper with manifest.signed.json inside the bundle."""
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"

        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=td)

            # Tamper with manifest
            manifest_path = os.path.join(td, "manifest.signed.json")
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
            manifest["session_file_count"] = 9999
            with open(manifest_path, "w") as f:
                json.dump(manifest, f)

            # Repack bundle
            tampered_bundle = os.path.join(td, "tampered.bundle.tar.gz")
            with tarfile.open(tampered_bundle, "w:gz") as tar:
                for fname in os.listdir(td):
                    fpath = os.path.join(td, fname)
                    if os.path.isfile(fpath):
                        tar.add(fpath, arcname=fname)

            result = _run([sys.executable, VERIFY_SCRIPT, "--offline-bundle", tampered_bundle])
            assert result.returncode == 1, f"expected exit 1, got {result.returncode}"
            assert (
                "merkle root mismatch" in result.stderr.lower()
                or "signature mismatch" in result.stderr.lower()
            )

    def test_verify_bundle_not_found(self):
        result = _run(
            [
                sys.executable,
                VERIFY_SCRIPT,
                "--offline-bundle",
                "/nonexistent/bundle.tar.gz",
            ]
        )
        assert result.returncode == 1

    def test_verify_missing_bundle_file(self, real_keypair, minimal_session_dir):
        """Bundle missing manifest.signed.json."""
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"

        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=td)

            # Remove manifest
            os.remove(os.path.join(td, "manifest.signed.json"))

            bad_bundle = os.path.join(td, "bad.bundle.tar.gz")
            with tarfile.open(bad_bundle, "w:gz") as tar:
                for fname in os.listdir(td):
                    fpath = os.path.join(td, fname)
                    if os.path.isfile(fpath):
                        tar.add(fpath, arcname=fname)

            result = _run([sys.executable, VERIFY_SCRIPT, "--offline-bundle", bad_bundle])
            assert result.returncode == 1
            assert "missing" in result.stderr.lower()


class TestVerifyShStandalone:
    """Test verify.sh runs independently without Python."""

    def test_verify_sh_valid_bundle(self, real_keypair, minimal_session_dir):
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"

        # Extract verify.sh from bundle
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extract("verify.sh", path=td)

            verify_sh = os.path.join(td, "verify.sh")
            os.chmod(verify_sh, 0o755)

            result = _run(["bash", verify_sh, bundle_path])
            assert result.returncode == 0, f"verify.sh failed: {result.stderr}"
            assert "VERIFIED" in result.stdout

    def test_verify_sh_tampered_bundle(self, real_keypair, minimal_session_dir):
        """verify.sh should detect tampering."""
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"

        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=td)

            # Tamper with session.tar.gz
            session_tar = os.path.join(td, "session.tar.gz")
            with open(session_tar, "ab") as f:
                f.write(b"TAMPERED")

            # Repack
            tampered_bundle = os.path.join(td, "tampered.bundle.tar.gz")
            with tarfile.open(tampered_bundle, "w:gz") as tar:
                for fname in os.listdir(td):
                    fpath = os.path.join(td, fname)
                    if os.path.isfile(fpath):
                        tar.add(fpath, arcname=fname)

            verify_sh = os.path.join(td, "verify.sh")
            os.chmod(verify_sh, 0o755)

            result = _run(["bash", verify_sh, tampered_bundle])
            assert result.returncode == 1, f"expected exit 1, got {result.returncode}"
            assert (
                "merkle root mismatch" in result.stderr.lower()
                or "signature mismatch" in result.stderr.lower()
            )

    def test_verify_sh_expect_pubkey(self, real_keypair, minimal_session_dir):
        keyfile, pubkey_bytes = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"
        fp = _pubkey_fingerprint(pubkey_bytes)

        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extract("verify.sh", path=td)

            verify_sh = os.path.join(td, "verify.sh")
            os.chmod(verify_sh, 0o755)

            result = _run(["bash", verify_sh, bundle_path, "--expect-pubkey", fp])
            assert result.returncode == 0, f"verify.sh failed: {result.stderr}"

    def test_verify_sh_wrong_pubkey(self, real_keypair, minimal_session_dir):
        keyfile, _ = real_keypair
        _run([sys.executable, BUNDLE_SCRIPT, minimal_session_dir, "--keyfile", keyfile])

        bundle_path = minimal_session_dir + ".bundle.tar.gz"

        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extract("verify.sh", path=td)

            verify_sh = os.path.join(td, "verify.sh")
            os.chmod(verify_sh, 0o755)

            result = _run(["bash", verify_sh, bundle_path, "--expect-pubkey", "deadbeefdeadbeef"])
            assert result.returncode == 1
            assert "mismatch" in result.stderr.lower()


class TestExistingModesUnchanged:
    """Ensure existing provenance_verify.py modes still work."""

    def test_original_verify_mode_still_works(self, tmp_keyfile):
        """Sign a manifest and verify it using the original mode."""
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

            # Sign
            result = _run([sys.executable, SIGN_SCRIPT, manifest_path, "--keyfile", tmp_keyfile])
            assert result.returncode == 0, f"sign failed: {result.stderr}"

            signed_path = manifest_path + ".signed.json"

            # Verify using original mode
            result = _run([sys.executable, VERIFY_SCRIPT, signed_path])
            assert result.returncode == 0, f"verify failed: {result.stderr}"
            assert "VERIFIED" in result.stdout

    def test_original_verify_with_expect_pubkey(self, tmp_keyfile):
        """Original mode with --expect-pubkey still works."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.generate()
        seed = private_key.private_bytes_raw()
        pubkey = private_key.public_key().public_bytes_raw()

        with open(tmp_keyfile, "wb") as f:
            f.write(seed)
        with open(tmp_keyfile + ".pub", "wb") as f:
            f.write(pubkey)

        fp = _pubkey_fingerprint(pubkey)

        with tempfile.TemporaryDirectory() as td:
            manifest_path = os.path.join(td, "manifest.json")
            manifest = {"batch_id": "batch-002", "record_count": 10}
            with open(manifest_path, "w") as f:
                json.dump(manifest, f)

            result = _run([sys.executable, SIGN_SCRIPT, manifest_path, "--keyfile", tmp_keyfile])
            assert result.returncode == 0

            signed_path = manifest_path + ".signed.json"
            result = _run([sys.executable, VERIFY_SCRIPT, signed_path, "--expect-pubkey", fp])
            assert result.returncode == 0, f"verify failed: {result.stderr}"
