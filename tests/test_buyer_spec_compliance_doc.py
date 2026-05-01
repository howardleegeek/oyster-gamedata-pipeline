"""Tests guarding the structure of ``docs/BUYER_SPEC_COMPLIANCE.md``.

The compliance landing page is a buyer-facing doc that mirrors the
oyster-enrichment site/buyer-spec.html landing page but for the *trajectory*
side of the pipeline (Phase 1 Mineflayer bundles → buyer-spec v1 layout).

These tests are intentionally cheap: they keep the doc honest about
referencing the canonical adapter (``buyer_spec_adapter.py``) and the
six required sections. If a future refactor renames the adapter or
loses a section, these tests fail fast and the compliance page does not
silently rot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "BUYER_SPEC_COMPLIANCE.md"

#: The six top-level sections required by the acceptance criteria. Each
#: entry is a substring expected to appear in a Markdown heading
#: (``##`` line) — we match on substring rather than exact text so the
#: heading prose can be tweaked without breaking the test.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "Phase 1 trajectory",  # buyer-spec mapping
    "ground-truth",  # what we have ground-truth for
    "synthesize",  # what we synthesize
    "NULL",  # what is NULL
    "Coordinate conversion",  # coordinate frame conversion
    "Usage",  # CLI usage
)


@pytest.mark.unit
def test_compliance_doc_exists() -> None:
    """The buyer-spec compliance page is checked into ``docs/``."""
    assert DOC_PATH.is_file(), f"missing buyer-spec compliance doc at {DOC_PATH}"
    # Sanity bound: the spec calls for ~5 KB. We allow 2 KB ≤ size ≤ 20 KB
    # so the test catches an empty / accidentally-truncated file but does
    # not become a stylistic straitjacket.
    size = DOC_PATH.stat().st_size
    assert 2_000 <= size <= 20_000, f"doc size {size} bytes outside [2000, 20000] window"


@pytest.mark.unit
def test_compliance_doc_has_six_sections() -> None:
    """All six required sections from the acceptance criteria are present."""
    text = DOC_PATH.read_text(encoding="utf-8")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    # Lower-case both sides so the test does not care about title-case
    # variations buyers' eyes will not notice.
    headings_lc = "\n".join(headings).lower()

    missing = [needle for needle in REQUIRED_SECTIONS if needle.lower() not in headings_lc]
    assert not missing, (
        f"compliance doc is missing the following section markers in `## ` headings: "
        f"{missing}; found headings: {headings}"
    )


@pytest.mark.unit
def test_compliance_doc_references_buyer_spec_adapter() -> None:
    """The doc must point readers at the canonical adapter module.

    Engineer I8's ``buyer_spec_adapter.py`` is the implementation backing
    every claim on the page. The page is *about* that module — it must
    name it explicitly so a buyer can grep their way from the doc to the
    code that runs on their bundle.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    assert (
        "buyer_spec_adapter.py" in text or "buyer_spec_adapter" in text
    ), "compliance doc does not reference the buyer_spec_adapter module"


@pytest.mark.unit
def test_compliance_doc_cites_mineflayer_ground_truth() -> None:
    """The doc credits Mineflayer as the ground-truth source for Phase 1.

    This is the single most important narrative claim on the page: buyers
    need to know that ``player_position`` / ``player_rotation_*`` / the
    1-block-per-meter ``metric_scale`` are NOT estimates — they come
    straight from ``bot.entity.position`` / ``bot.entity.yaw`` /
    ``bot.entity.pitch`` and are exact within Minecraft's tick model.
    """
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "mineflayer" in text, "compliance doc does not mention Mineflayer"
    # And it must explicitly say ground-truth (or a close synonym) so the
    # claim is not buried.
    assert (
        "ground-truth" in text or "ground truth" in text
    ), "compliance doc does not call out Mineflayer values as ground-truth"
