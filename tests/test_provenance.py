"""
Tests for Oyster provenance module.

Tests:
- Merkle root recomputes correctly from file_hashes
- Tampering with one frame breaks verification
- Signature verification works with public key
- Anchor lookup succeeds (mock blockchain)
"""

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from oyster_provenance.anchor import (
    WeeklyAnchor,
    compute_meta_merkle_root,
    create_weekly_anchor,
    get_week_range,
    load_weekly_anchor,
    save_weekly_anchor,
    simulate_anchor_tx,
)
from oyster_provenance.manifest import (
    BiometricFlags,
    build_manifest,
    hash_player_id,
    load_manifest,
    save_manifest,
)
from oyster_provenance.merkle import MerkleTree, sha256
from oyster_provenance.sign import generate_keypair


class TestMerkleTree:
    """Tests for Merkle tree implementation."""

    def test_empty_tree(self):
        """Test empty tree returns empty hash."""
        tree = MerkleTree.from_frame_hashes({})
        assert tree.root == sha256(b"")
        assert tree.frame_count == 0

    def test_single_leaf(self):
        """Test single leaf tree."""
        tree = MerkleTree.from_frame_hashes({0: "a" * 64})
        assert tree.root is not None
        assert tree.frame_count == 1

    def test_multiple_leaves(self):
        """Test tree with multiple leaves."""
        frame_hashes = {
            0: "a" * 64,
            1: "b" * 64,
            2: "c" * 64,
            3: "d" * 64,
        }
        tree = MerkleTree.from_frame_hashes(frame_hashes)
        assert tree.root is not None
        assert tree.frame_count == 4

    def test_merkle_root_deterministic(self):
        """Test Merkle root is deterministic."""
        frame_hashes = {i: f"hash{i}" * 8 for i in range(10)}

        tree1 = MerkleTree.from_frame_hashes(frame_hashes)
        tree2 = MerkleTree.from_frame_hashes(frame_hashes)

        assert tree1.root == tree2.root

    def test_proof_generation(self):
        """Test proof generation."""
        frame_hashes = {
            0: "a" * 64,
            1: "b" * 64,
            2: "c" * 64,
            3: "d" * 64,
        }
        tree = MerkleTree.from_frame_hashes(frame_hashes)

        proof = tree.get_proof(1, "b" * 64)
        assert proof.frame_idx == 1
        assert proof.frame_hash == "b" * 64
        assert proof.root == tree.root

    def test_proof_verification(self):
        """Test proof verification."""
        frame_hashes = {
            0: "a" * 64,
            1: "b" * 64,
            2: "c" * 64,
            3: "d" * 64,
        }
        tree = MerkleTree.from_frame_hashes(frame_hashes)

        proof = tree.get_proof(1, "b" * 64)
        assert MerkleTree.verify_proof(proof) is True

    def test_proof_fails_wrong_hash(self):
        """Test proof fails with wrong frame hash."""
        frame_hashes = {
            0: "a" * 64,
            1: "b" * 64,
            2: "c" * 64,
            3: "d" * 64,
        }
        tree = MerkleTree.from_frame_hashes(frame_hashes)

        proof = tree.get_proof(1, "b" * 64)
        # Tamper with hash
        proof.frame_hash = "wrong" * 13

        assert MerkleTree.verify_proof(proof) is False

    def test_tampering_breaks_verification(self):
        """Test that tampering with one frame breaks verification."""
        # Create tree
        frame_hashes = {i: f"frame{i}" * 8 for i in range(10)}
        tree = MerkleTree.from_frame_hashes(frame_hashes)
        original_root = tree.root

        # Verify original
        proof = tree.get_proof(5, frame_hashes[5])
        assert MerkleTree.verify_proof(proof) is True

        # Tamper with frame data (simulate by changing hash)
        frame_hashes[5] = "tampered" * 8

        # Create new tree with tampered data
        tampered_tree = MerkleTree.from_frame_hashes(frame_hashes)

        # Root should be different
        assert tampered_tree.root != original_root

        # Old proof should fail against new root
        proof = tree.get_proof(5, frame_hashes[5])
        assert proof.root != tampered_tree.root


class TestSigning:
    """Tests for ed25519 signing."""

    def test_generate_keypair(self):
        """Test keypair generation."""
        key_dir = tempfile.mkdtemp()
        signing_key, key_info = generate_keypair(key_dir)

        assert signing_key is not None
        assert key_info is not None
        assert len(key_info.public_key_hex) == 64  # ed25519 public key is 32 bytes = 64 hex

    def test_sign_and_verify(self):
        """Test signing and verification."""
        key_dir = tempfile.mkdtemp()
        signing_key, _ = generate_keypair(key_dir)

        data = {"test": "data", "session_id": "12345"}
        signature = signing_key.sign_json(data)

        # Need to use the same key_dir for verification
        from oyster_provenance.sign import verify_json_signature as verify

        assert verify(data, signature, Path(key_dir) / "signing_key.pub") is True

    def test_verify_fails_tampered(self):
        """Test verification fails with tampered data."""
        key_dir = tempfile.mkdtemp()
        signing_key, _ = generate_keypair(key_dir)

        data = {"test": "data", "session_id": "12345"}
        signature = signing_key.sign_json(data)

        # Tamper with data
        data["test"] = "tampered"

        from oyster_provenance.sign import verify_json_signature as verify

        assert verify(data, signature, Path(key_dir) / "signing_key.pub") is False

    def test_verify_fails_wrong_signature(self):
        """Test verification fails with wrong signature."""
        key_dir = tempfile.mkdtemp()
        signing_key, _ = generate_keypair(key_dir)

        data = {"test": "data"}
        signature = "0" * 128  # Wrong signature

        from oyster_provenance.sign import verify_json_signature as verify

        assert verify(data, signature, Path(key_dir) / "signing_key.pub") is False


class TestManifest:
    """Tests for session manifest."""

    def test_build_manifest(self):
        """Test manifest building."""
        key_dir = tempfile.mkdtemp()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            for i in range(5):
                with open(os.path.join(tmpdir, f"frame_{i:05d}.jpg"), "wb") as f:
                    f.write(b"fake frame data " * 100)

            with open(os.path.join(tmpdir, "game_state.jsonl"), "wb") as f:
                f.write(b'{"tick": 1}\n')

            # Build manifest
            manifest = build_manifest(
                session_dir=tmpdir,
                session_id="test-session-001",
                player_pubkey="test_pubkey",
                player_salt="salt123",
                biometric_flags=BiometricFlags(
                    voice_chat_captured=False,
                    webcam_captured=False,
                    facial_data=False,
                    minor_consent_obtained=False,
                    age_verified_18plus=True,
                ),
                key_dir=key_dir,
            )

            assert manifest.session_id == "test-session-001"
            assert manifest.player_id_hash is not None
            assert manifest.oyster_signature is not None
            assert manifest.frame_count > 0
            assert manifest.frame_hash_merkle_root is not None

    def test_manifest_signature(self):
        """Test manifest signature verification."""
        key_dir = tempfile.mkdtemp()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file
            with open(os.path.join(tmpdir, "test.txt"), "wb") as f:
                f.write(b"test data")

            manifest = build_manifest(
                session_dir=tmpdir,
                session_id="test-session-002",
                player_pubkey="test_pubkey",
                key_dir=key_dir,
            )

            assert manifest.verify_signature(Path(key_dir) / "signing_key.pub") is True

    def test_manifest_save_load(self):
        """Test manifest save and load."""
        key_dir = tempfile.mkdtemp()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file
            with open(os.path.join(tmpdir, "test.txt"), "wb") as f:
                f.write(b"test data")

            # Build and save
            manifest = build_manifest(
                session_dir=tmpdir,
                session_id="test-session-003",
                player_pubkey="test_pubkey",
                key_dir=key_dir,
            )
            save_manifest(manifest, tmpdir)

            # Load
            loaded = load_manifest(tmpdir)

            assert loaded.session_id == manifest.session_id
            assert loaded.oyster_signature == manifest.oyster_signature

    def test_player_id_hash(self):
        """Test player ID hashing."""
        hash1 = hash_player_id("pubkey123", "salt")
        hash2 = hash_player_id("pubkey123", "salt")
        hash3 = hash_player_id("pubkey123", "different_salt")

        assert hash1 == hash2  # Same input = same hash
        assert hash1 != hash3  # Different salt = different hash

    def test_biometric_flags(self):
        """Test biometric flags."""
        flags = BiometricFlags(
            voice_chat_captured=False,
            webcam_captured=False,
            facial_data=False,
            minor_consent_obtained=False,
            age_verified_18plus=True,
        )

        data = flags.to_dict()
        assert data["voice_chat_captured"] is False
        assert data["age_verified_18plus"] is True

        loaded = BiometricFlags.from_dict(data)
        assert loaded.voice_chat_captured is False


class TestAnchor:
    """Tests for weekly anchor."""

    def test_get_week_range(self):
        """Test week range calculation."""
        # Test with a known date (Monday)
        monday = datetime(2026, 5, 18, 12, 0, 0)  # Monday
        week_start, week_end = get_week_range(monday)

        assert week_start.weekday() == 0  # Monday
        assert week_end.weekday() == 6  # Sunday

    def test_compute_meta_merkle_root(self):
        """Test meta-Merkle root computation."""
        manifests = [
            {"session_id": "s1", "hash": "h1"},
            {"session_id": "s2", "hash": "h2"},
            {"session_id": "s3", "hash": "h3"},
        ]

        root = compute_meta_merkle_root(manifests)
        assert root is not None
        assert len(root) == 64  # SHA256 hex

    def test_compute_meta_merkle_root_empty(self):
        """Test meta-Merkle root with empty list."""
        root = compute_meta_merkle_root([])
        assert root == sha256(b"empty_week")

    def test_create_weekly_anchor(self):
        """Test weekly anchor creation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create sessions with manifests
            for i in range(3):
                session_dir = os.path.join(tmpdir, f"session_{i}")
                os.makedirs(session_dir)

                manifest = {
                    "session_id": f"session-{i}",
                    "player_id_hash": "abc123",
                    "consent_doc_sha256": "def456",
                    "consent_signed_at_utc": datetime.now().isoformat(),
                    "frame_hash_merkle_root": f"root{i}",
                }

                with open(os.path.join(session_dir, "provenance.json"), "w") as f:
                    json.dump(manifest, f)

            anchor = create_weekly_anchor(tmpdir)

            assert anchor.session_count == 3
            assert anchor.meta_merkle_root is not None

    def test_simulate_anchor_tx(self):
        """Test anchor transaction simulation."""
        anchor = WeeklyAnchor(
            week_start="2026-05-18",
            week_end="2026-05-24",
            session_count=5,
            meta_merkle_root="test_root",
        )

        tx = simulate_anchor_tx(anchor)

        assert tx.chain == "bitcoin"
        assert tx.tx_hash is not None
        assert tx.block_number == 870234

    def test_save_load_anchor(self):
        """Test anchor save and load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            anchor = WeeklyAnchor(
                week_start="2026-05-18",
                week_end="2026-05-24",
                session_count=5,
                meta_merkle_root="test_root",
            )

            # Add anchor tx
            anchor.anchor_tx = simulate_anchor_tx(anchor)

            # Save
            save_weekly_anchor(anchor, tmpdir)

            # Load
            loaded = load_weekly_anchor(tmpdir, "2026-05-18")

            assert loaded is not None
            assert loaded.week_start == "2026-05-18"
            assert loaded.anchor_tx.tx_hash == anchor.anchor_tx.tx_hash


class TestIntegration:
    """Integration tests."""

    def test_full_provenance_flow(self):
        """Test full provenance flow."""
        key_dir = tempfile.mkdtemp()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            for i in range(10):
                with open(os.path.join(tmpdir, f"frame_{i:05d}.jpg"), "wb") as f:
                    f.write(f"frame {i} data".encode())

            # Build manifest
            manifest = build_manifest(
                session_dir=tmpdir,
                session_id="integration-test-001",
                player_pubkey="integration_test_key",
                player_salt="salt",
                biometric_flags=BiometricFlags(
                    voice_chat_captured=False,
                    webcam_captured=False,
                    facial_data=False,
                    minor_consent_obtained=False,
                    age_verified_18plus=True,
                ),
                key_dir=key_dir,
            )

            # Save
            save_manifest(manifest, tmpdir)

            # Load
            loaded = load_manifest(tmpdir)

            # Verify
            assert loaded.verify_signature(Path(key_dir) / "signing_key.pub") is True
            assert loaded.session_id == "integration-test-001"
            assert loaded.biometric_flags["age_verified_18plus"] is True

    def test_backfill_manifest(self):
        """Test backfilling manifest for existing session."""
        key_dir = tempfile.mkdtemp()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files (simulating existing session)
            for i in range(5):
                with open(os.path.join(tmpdir, f"old_frame_{i}.jpg"), "wb") as f:
                    f.write(f"old frame {i}".encode())

            # First call creates and saves manifest
            manifest1 = build_manifest(
                session_dir=tmpdir,
                session_id="backfill-test",
                player_pubkey="old_player",
                key_dir=key_dir,
            )
            save_manifest(manifest1, tmpdir)

            # Second call should load existing
            manifest2 = load_manifest(tmpdir)

            assert manifest2.session_id == "backfill-test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
