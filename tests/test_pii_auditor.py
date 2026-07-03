#!/usr/bin/env python3
"""
Tests for PII Auditor, Redactor, and Right-to-Delete functionality.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add bin to path
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from pii_auditor import (
    audit_session,
    determine_verdict,
    is_private_ip,
    luhn_check,
    scan_file_for_pii,
    scan_session,
)
from pii_redactor import mask_ip, pseudonymize_username, redact_file_content, redact_session, sha8
from right_to_delete import (
    DELETION_LOG,
    check_deletion_status,
    hash_contributor_id,
    mark_for_deletion,
)


class TestPIIAuditor:
    """Tests for PII detection."""

    def test_is_private_ip(self):
        """Test private IP detection."""
        assert is_private_ip("192.168.1.1") == True
        assert is_private_ip("10.0.0.1") == True
        assert is_private_ip("172.16.0.1") == True
        assert is_private_ip("172.31.0.1") == True
        assert is_private_ip("127.0.0.1") == True
        assert is_private_ip("8.8.8.8") == False
        assert is_private_ip("1.2.3.4") == False

    def test_luhn_check(self):
        """Test Luhn algorithm for credit card validation."""
        # Valid test cards
        assert luhn_check("4532015112830366") == True  # Visa
        assert luhn_check("5425233430109903") == True  # Mastercard
        assert luhn_check("378282246310005") == True  # Amex

        # Invalid numbers
        assert luhn_check("1234567890123456") == False
        assert luhn_check("9999999999999999") == False

    def test_detect_emails(self):
        """Test email detection."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Contact me at john.doe@example.com or jane@company.org")
            temp_path = f.name

        try:
            flags = scan_file_for_pii(Path(temp_path))
            assert "john.doe@example.com" in flags["emails"]
            assert "jane@company.org" in flags["emails"]
        finally:
            os.unlink(temp_path)

    def test_detect_ssns(self):
        """Test SSN detection."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("SSN: 123-45-6789 and also 987-65-4321")
            temp_path = f.name

        try:
            flags = scan_file_for_pii(Path(temp_path))
            assert "123-45-6789" in flags["ssns"]
            assert "987-65-4321" in flags["ssns"]
        finally:
            os.unlink(temp_path)

    def test_detect_phones(self):
        """Test phone number detection."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Call me at (555) 123-4567 or +1-555-987-6543")
            temp_path = f.name

        try:
            flags = scan_file_for_pii(Path(temp_path))
            assert len(flags["phones"]) >= 1
        finally:
            os.unlink(temp_path)

    def test_detect_credit_cards(self):
        """Test credit card detection with Luhn validation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            # Valid Visa test number
            f.write("Card: 4532015112830366")
            temp_path = f.name

        try:
            flags = scan_file_for_pii(Path(temp_path))
            assert "4532015112830366" in flags["credit_cards"]
        finally:
            os.unlink(temp_path)

    def test_detect_public_ip(self):
        """Test public IP detection (not private)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Server at 8.8.8.8 and 1.2.3.4")
            temp_path = f.name

        try:
            flags = scan_file_for_pii(Path(temp_path))
            assert "8.8.8.8" in flags["ip_addresses"]
            assert "1.2.3.4" in flags["ip_addresses"]
        finally:
            os.unlink(temp_path)

    def test_private_ip_not_flagged(self):
        """Test that private IPs are not flagged as leaks."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Local network: 192.168.1.1, 10.0.0.1")
            temp_path = f.name

        try:
            flags = scan_file_for_pii(Path(temp_path))
            # Private IPs should NOT be in the flags
            assert "192.168.1.1" not in flags["ip_addresses"]
            assert "10.0.0.1" not in flags["ip_addresses"]
        finally:
            os.unlink(temp_path)

    def test_determine_verdict_fail(self):
        """Test verdict is FAIL when high-risk PII found (credit cards, SSNs)."""
        flags = {
            "player_username": "Steve123",
            "real_names_in_chat": [],
            "emails": [],
            "credit_cards": ["4532015112830366"],
            "ip_addresses": [],
            "ssns": ["123-45-6789"],
            "phones": [],
        }

        verdict, recommendations = determine_verdict(flags)
        assert verdict == "FAIL"
        assert len(recommendations) > 0

    def test_determine_verdict_pass(self):
        """Test verdict is PASS when no high-risk PII found (only player username, emails, etc)."""
        flags = {
            "player_username": "Steve123",
            "real_names_in_chat": [],
            "emails": ["test@example.com"],
            "credit_cards": [],
            "ip_addresses": [],
            "ssns": [],
            "phones": ["555-123-4567"],
        }

        verdict, recommendations = determine_verdict(flags)
        # Emails, phones are medium risk - verdict is PASS with warnings
        assert verdict == "PASS"
        assert len(recommendations) > 0  # Has recommendations


class TestPIIAuditorErrorHandling:
    """Regression tests: PII auditor must surface read failures, not silently
    report 'no PII' on a corrupted/unreadable session."""

    def test_scan_file_for_pii_missing_file_logs_and_returns_empty(self, caplog):
        """A missing file must log a WARNING and return empty flags (not raise)."""
        missing = Path("/tmp/definitely_does_not_exist_pii_auditor_test_xyz123.jsonl")
        if missing.exists():
            missing.unlink()

        with caplog.at_level("WARNING", logger="pii_auditor"):
            flags = scan_file_for_pii(missing)

        # Empty flags returned (caller can continue scanning other files)
        assert flags == {
            "real_names_in_chat": [],
            "emails": [],
            "credit_cards": [],
            "ip_addresses": [],
            "ssns": [],
            "phones": [],
        }
        # And the failure was surfaced via a WARNING log
        assert any("failed to read" in rec.message for rec in caplog.records), \
            f"expected a WARNING mentioning the failed read, got: {[r.message for r in caplog.records]}"

    def test_scan_file_for_pii_unreadable_file_logs_and_returns_empty(self, tmp_path, caplog):
        """A chmod-000 file must log a WARNING and return empty flags, not raise."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root bypasses chmod 000, cannot test unreadable file")

        unreadable = tmp_path / "unreadable.jsonl"
        unreadable.write_text("hello world\n")
        unreadable.chmod(0o000)
        try:
            with caplog.at_level("WARNING", logger="pii_auditor"):
                flags = scan_file_for_pii(unreadable)
        finally:
            unreadable.chmod(0o644)  # restore so tmp_path cleanup works

        assert flags == {
            "real_names_in_chat": [],
            "emails": [],
            "credit_cards": [],
            "ip_addresses": [],
            "ssns": [],
            "phones": [],
        }
        assert any("failed to read" in rec.message for rec in caplog.records)

    def test_scan_session_unreadable_game_state_logs_and_keeps_going(self, tmp_path, caplog):
        """An unreadable game_state.jsonl must log a WARNING; scan_session
        must not raise and must still process the other files."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root bypasses chmod 000, cannot test unreadable file")

        session_dir = tmp_path / "session"
        session_dir.mkdir()

        # game_state.jsonl is unreadable
        gs = session_dir / "game_state.jsonl"
        gs.write_text("placeholder\n")
        gs.chmod(0o000)
        try:
            # inputs.jsonl is readable and has PII
            inputs = session_dir / "inputs.jsonl"
            inputs.write_text("email: leak@example.com\n")

            with caplog.at_level("WARNING", logger="pii_auditor"):
                flags = scan_session(session_dir)
        finally:
            gs.chmod(0o644)

        # The readable file was still scanned
        assert "leak@example.com" in flags["emails"]
        # The unreadable file was surfaced in logs
        assert any("failed to read" in rec.message for rec in caplog.records), \
            f"expected a WARNING for the unreadable game_state, got: {[r.message for r in caplog.records]}"

    def test_bare_except_exception_removed_from_pii_auditor(self):
        """Static guard: the bare 'except Exception:' (silent swallow) must
        no longer appear in bin/pii_auditor.py — we now log WARNINGs."""
        src = Path("bin/pii_auditor.py").read_text()
        # The original lines were:
        #   except Exception:
        #   except Exception:
        # both bare (no `as exc`). Allow `except Exception as exc:` if it
        # ever shows up, but the bare form is what silently swallowed.
        assert "except Exception:\n" not in src, \
            "bin/pii_auditor.py still has a bare 'except Exception:' — that is a silent error swallow"


class TestPIIRedactor:
    """Tests for PII redaction."""

    def test_sha8(self):
        """Test 8-character hash generation."""
        h1 = sha8("test_user")
        h2 = sha8("test_user")
        h3 = sha8("different_user")

        assert len(h1) == 8
        assert h1 == h2  # Same input = same hash
        assert h1 != h3  # Different input = different hash

    def test_pseudonymize_username(self):
        """Test username pseudonymization."""
        pseudonym = pseudonymize_username("Steve123")

        assert pseudonym.startswith("player_")
        assert len(pseudonym) == 15  # player_ + 8 chars

    def test_mask_ip(self):
        """Test IP masking."""
        assert mask_ip("192.168.1.100") == "192.168.1.0"
        assert mask_ip("10.0.0.50") == "10.0.0.0"
        assert mask_ip("8.8.8.8") == "8.8.8.0"

    def test_redact_file_content(self):
        """Test file content redaction."""
        content = """
        Player: Steve123
        Email: steve@example.com
        Phone: (555) 123-4567
        SSN: 123-45-6789
        Card: 4532015112830366
        """

        redacted = redact_file_content(content, "Steve123", "player_abc12345")

        assert "player_abc12345" in redacted
        assert "steve@example.com" not in redacted
        assert "[email_redacted]" in redacted

    def test_redact_session_idempotent(self):
        """Test that redaction is idempotent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)

            # Create test files
            game_state = session_dir / "game_state.jsonl"
            with open(game_state, "w") as f:
                f.write(json.dumps({"player": "Steve123", "score": 100}) + "\n")

            # First redaction
            result1 = redact_session(session_dir)
            pseudonym1 = result1["pseudonymized_to"]

            # Read the file after first redaction
            with open(game_state, "r") as f:
                content_after_first = f.read()

            # Second redaction (should be idempotent - same pseudonym)
            result2 = redact_session(session_dir)
            pseudonym2 = result2["pseudonymized_to"]

            # Should produce same result
            assert pseudonym1 == pseudonym2


class TestRightToDelete:
    """Tests for GDPR deletion functionality."""

    def test_hash_contributor_id(self):
        """Test contributor ID hashing."""
        h1 = hash_contributor_id("user123")
        h2 = hash_contributor_id("user123")
        h3 = hash_contributor_id("user456")

        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 16  # SHA256 hex[:16]

    def test_mark_for_deletion(self, tmp_path):
        """Test marking data for deletion."""
        # Create a test session
        session_dir = tmp_path / "session_001"
        session_dir.mkdir()

        metadata = session_dir / "metadata.json"
        with open(metadata, "w") as f:
            json.dump({"contributor_id_hash": hash_contributor_id("user123")}, f)

        # Mark for deletion
        result = mark_for_deletion(
            contributor_id="user123",
            requested_at="2026-05-17",
            reason="user request",
            sessions_dir=tmp_path,
        )

        assert result["status"] == "pending"
        assert result["contributor_id_hash"] == hash_contributor_id("user123")
        assert len(result["sessions_marked"]) == 1

    def test_check_deletion_status_not_found(self):
        """Test checking status for non-existent request."""
        # Clean up any existing log
        if DELETION_LOG.exists():
            DELETION_LOG.unlink()

        status = check_deletion_status("nonexistent_user")
        assert status["status"] == "not_found"


class TestIntegration:
    """Integration tests for the full PII workflow."""

    def test_full_audit_and_redact_workflow(self, tmp_path):
        """Test complete audit -> redact -> re-audit workflow."""
        session_dir = tmp_path / "test_session"
        session_dir.mkdir()

        # Create session with PII
        game_state = session_dir / "game_state.jsonl"
        with open(game_state, "w") as f:
            f.write(json.dumps({"player": "Steve123", "score": 100}) + "\n")

        inputs_file = session_dir / "inputs.jsonl"
        with open(inputs_file, "w") as f:
            f.write(json.dumps({"chat": "Hello John Smith, my email is john@test.com"}) + "\n")

        # Run audit
        result = audit_session(session_dir, session_dir / "pii_audit.json")

        # Should detect player username and email
        assert result["flags"]["player_username"] == "Steve123"
        assert "john@test.com" in result["flags"]["emails"]

        # Should pass (emails are medium risk, not high risk)
        # High risk = credit cards, SSNs
        assert result["verdict"] == "PASS"

        # Redact
        redact_session(session_dir)

        # Re-audit
        result2 = audit_session(session_dir)

        # Should still flag username but email should be redacted
        assert result2["flags"]["player_username"] is not None
        # Email should be gone (or redacted)
        assert "john@test.com" not in result2["flags"]["emails"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
