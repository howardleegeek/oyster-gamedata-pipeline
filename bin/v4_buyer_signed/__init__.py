"""V₄ — Buyer-Signed Reference Verifier (YELLOW node).

🟡 YELLOW node in BFT N=4, f=1 system. Closes the two CRITICAL gaps that
V₁/V₂/V₃ are forever-uncatchable on:

- B-01: self-consistent oula+quat Hamilton swap (R02 PASSes by construction)
- B-03: coordinated keyCode + inputs.jsonl W→B swap (R09+R13 both PASS)

Root cause both attacks share: the producer controls every artifact a
code-only verifier can read. V₄ breaks this by anchoring trust to a
*buyer-signed reference* (`buyer_reference.json`) sitting outside the
producer's reach. See ``docs/SPEC_V4_BUYER_SIGNED_PROTOCOL.md`` for the
full design + IL12 iron law.

This implementation uses **HMAC-SHA256** keyed off the
``BUYER_SHARED_SECRET`` env var as the signing primitive. The spec
recommends ed25519 long-term (§ 3.3) but defers to HMAC for v1 bring-up
(open Q1) so we can ship without a cryptography lib dependency.

IRON LAW IL12 enforced: V₄ MUST emit ABSTAIN whenever any of the signing
preconditions fail; it MUST NEVER return passed=True in those cases.
"""
from .verifier import (
    canonical_record,
    compute_signature,
    load_buyer_reference,
    v4_buyer_reference_diff,
    verify_signature,
)

__all__ = [
    "canonical_record",
    "compute_signature",
    "load_buyer_reference",
    "v4_buyer_reference_diff",
    "verify_signature",
]
