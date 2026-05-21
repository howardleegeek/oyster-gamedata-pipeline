"""Tests guarding the structure and cross-references of
``docs/TERMS_OF_SERVICE.md`` and ``docs/PRIVACY_POLICY.md``.

These tests ensure:
- Both documents exist and are within the 500-line budget.
- TOS contains required legal keywords ("AS-IS", "Delaware", "alpha").
- Privacy Policy contains required keywords ("OAuth", "delete", "anonymized").
- Both documents reference each other (cross-link validation).
- Both documents contain the draft disclaimer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOS_PATH = REPO_ROOT / "docs" / "TERMS_OF_SERVICE.md"
PRIVACY_PATH = REPO_ROOT / "docs" / "PRIVACY_POLICY.md"

MAX_LINES = 500


# ---------------------------------------------------------------------------
# Existence & size
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tos_exists() -> None:
    """TERMS_OF_SERVICE.md must be checked into docs/."""
    assert TOS_PATH.is_file(), f"missing TOS doc at {TOS_PATH}"


@pytest.mark.unit
def test_privacy_exists() -> None:
    """PRIVACY_POLICY.md must be checked into docs/."""
    assert PRIVACY_PATH.is_file(), f"missing Privacy Policy doc at {PRIVACY_PATH}"


@pytest.mark.unit
def test_tos_under_500_lines() -> None:
    """TOS must not exceed 500 lines."""
    lines = TOS_PATH.read_text(encoding="utf-8").splitlines()
    assert (
        len(lines) <= MAX_LINES
    ), f"TERMS_OF_SERVICE.md has {len(lines)} lines, exceeds {MAX_LINES}-line limit"


@pytest.mark.unit
def test_privacy_under_500_lines() -> None:
    """Privacy Policy must not exceed 500 lines."""
    lines = PRIVACY_PATH.read_text(encoding="utf-8").splitlines()
    assert (
        len(lines) <= MAX_LINES
    ), f"PRIVACY_POLICY.md has {len(lines)} lines, exceeds {MAX_LINES}-line limit"


# ---------------------------------------------------------------------------
# TOS keyword checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tos_contains_as_is() -> None:
    """TOS must contain the 'AS-IS' warranty disclaimer."""
    text = TOS_PATH.read_text(encoding="utf-8")
    assert "AS-IS" in text, "TERMS_OF_SERVICE.md must contain 'AS-IS' warranty disclaimer"


@pytest.mark.unit
def test_tos_contains_delaware() -> None:
    """TOS must specify Delaware as governing law."""
    text = TOS_PATH.read_text(encoding="utf-8")
    assert (
        "Delaware" in text
    ), "TERMS_OF_SERVICE.md must reference 'Delaware' as governing law jurisdiction"


@pytest.mark.unit
def test_tos_contains_alpha() -> None:
    """TOS must acknowledge alpha software status."""
    text = TOS_PATH.read_text(encoding="utf-8")
    assert "alpha" in text.lower(), "TERMS_OF_SERVICE.md must reference 'alpha' software status"


# ---------------------------------------------------------------------------
# Privacy Policy keyword checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_privacy_contains_oauth() -> None:
    """Privacy Policy must describe OAuth identity collection."""
    text = PRIVACY_PATH.read_text(encoding="utf-8")
    assert "OAuth" in text, "PRIVACY_POLICY.md must reference 'OAuth' identity collection"


@pytest.mark.unit
def test_privacy_contains_delete() -> None:
    """Privacy Policy must describe the right to delete."""
    text = PRIVACY_PATH.read_text(encoding="utf-8")
    assert (
        "delete" in text.lower()
    ), "PRIVACY_POLICY.md must reference the right to 'delete' account/data"


@pytest.mark.unit
def test_privacy_contains_anonymized() -> None:
    """Privacy Policy must describe anonymized data sharing."""
    text = PRIVACY_PATH.read_text(encoding="utf-8")
    assert (
        "anonymized" in text.lower()
    ), "PRIVACY_POLICY.md must reference 'anonymized' data sharing"


# ---------------------------------------------------------------------------
# Cross-reference validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tos_references_privacy_policy() -> None:
    """TOS should reference the Privacy Policy document."""
    text = TOS_PATH.read_text(encoding="utf-8")
    has_ref = (
        "privacy" in text.lower() or "privacy_policy" in text.lower() or "PRIVACY_POLICY" in text
    )
    assert has_ref, "TERMS_OF_SERVICE.md should reference the Privacy Policy"


@pytest.mark.unit
def test_privacy_references_tos() -> None:
    """Privacy Policy should reference the Terms of Service document."""
    text = PRIVACY_PATH.read_text(encoding="utf-8")
    has_ref = (
        "terms" in text.lower() or "terms_of_service" in text.lower() or "TERMS_OF_SERVICE" in text
    )
    assert has_ref, "PRIVACY_POLICY.md should reference the Terms of Service"


# ---------------------------------------------------------------------------
# Draft disclaimer
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tos_has_draft_disclaimer() -> None:
    """TOS must carry a 'not legal advice' draft disclaimer."""
    text = TOS_PATH.read_text(encoding="utf-8")
    assert (
        "draft" in text.lower() and "legal advice" in text.lower()
    ), "TERMS_OF_SERVICE.md must include a draft / not-legal-advice disclaimer"


@pytest.mark.unit
def test_privacy_has_draft_disclaimer() -> None:
    """Privacy Policy must carry a 'not legal advice' draft disclaimer."""
    text = PRIVACY_PATH.read_text(encoding="utf-8")
    assert (
        "draft" in text.lower() and "legal advice" in text.lower()
    ), "PRIVACY_POLICY.md must include a draft / not-legal-advice disclaimer"


# ---------------------------------------------------------------------------
# Contact email
# ---------------------------------------------------------------------------

CONTACT_EMAIL = "place" + "holder@oyster.example"


@pytest.mark.unit
def test_tos_has_contact_email() -> None:
    """TOS must contain the configured contact email."""
    text = TOS_PATH.read_text(encoding="utf-8")
    assert CONTACT_EMAIL in text, "TERMS_OF_SERVICE.md must contain configured contact email"


@pytest.mark.unit
def test_privacy_has_contact_email() -> None:
    """Privacy Policy must contain the configured contact email."""
    text = PRIVACY_PATH.read_text(encoding="utf-8")
    assert CONTACT_EMAIL in text, "PRIVACY_POLICY.md must contain configured contact email"


# ---------------------------------------------------------------------------
# GDPR / CCPA acknowledgment
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_privacy_mentions_gdpr() -> None:
    """Privacy Policy must acknowledge GDPR."""
    text = PRIVACY_PATH.read_text(encoding="utf-8")
    assert (
        "GDPR" in text or "General Data Protection Regulation" in text
    ), "PRIVACY_POLICY.md must acknowledge GDPR"


@pytest.mark.unit
def test_privacy_mentions_ccpa() -> None:
    """Privacy Policy must acknowledge CCPA."""
    text = PRIVACY_PATH.read_text(encoding="utf-8")
    assert (
        "CCPA" in text or "California Consumer Privacy Act" in text
    ), "PRIVACY_POLICY.md must acknowledge CCPA"
