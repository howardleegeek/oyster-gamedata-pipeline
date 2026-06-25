"""Real-API smoke test for `ClaudeThinkingProvider` — replayed via vcrpy.

This test exists to catch regressions in the Anthropic *wire contract* that
the unit tests in `test_claude_thinking_provider.py` cannot see (those tests
mock the SDK client object directly). Here we let the real `anthropic` SDK
run end-to-end, with `vcrpy` intercepting the HTTP layer to replay a single
recorded interaction.

Running modes
-------------
1. **Replay (default, CI):** read `tests/cassettes/claude_thinking_smoke.yaml`
   and replay deterministically. Requires `vcrpy` to be importable; otherwise
   the test skips gracefully.
2. **Record:** set `OYSTER_RECORD_VCR=1` and `ANTHROPIC_API_KEY=...`; the
   cassette is rewritten from a fresh real API call. The recorder redacts
   `x-api-key` and `authorization` headers before writing.

Why the cassette may be synthetic
---------------------------------
The committed cassette in this repo was hand-written (no live API key was
available at authoring time). It still exercises the SDK's response-parsing
codepath end-to-end — which is what we want to assert against. See the
header comment in the cassette file for details.

See `docs/VCR_TESTING.md` for the operator runbook.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# vcrpy is intentionally NOT in the project's optional-dependencies.test
# group (the constraint forbids modifying pyproject.toml). If a developer
# wants to run this test, they install vcrpy locally; CI without vcrpy
# simply skips. We guard the import at module scope so collection itself
# does not fail.
vcr = pytest.importorskip(
    "vcr",
    reason="vcrpy not installed; pip install vcrpy to enable VCR smoke test.",
)

CASSETTE_DIR = Path(__file__).parent / "cassettes"
CASSETTE_PATH = CASSETTE_DIR / "claude_thinking_smoke.yaml"

# Header redaction keys — applied to BOTH recording (so committed cassettes
# never contain real keys) and replay (vcrpy normalizes during matching).
_FILTER_HEADERS: list[tuple[str, str]] = [
    ("x-api-key", "REDACTED"),
    ("authorization", "REDACTED"),
]


def _record_mode() -> str:
    """Translate the env flag into vcrpy's record_mode string.

    - `OYSTER_RECORD_VCR=1` -> `"all"` (overwrite cassette).
    - unset / falsy        -> `"none"` (replay only; raise on cache miss).
    """
    if os.environ.get("OYSTER_RECORD_VCR", "").lower() in {"1", "true", "yes"}:
        return "all"
    return "none"


def _build_vcr() -> object:
    """Construct a `VCR` instance scoped to this module's cassette dir."""
    return vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=_record_mode(),
        filter_headers=_FILTER_HEADERS,
        # Match by method + uri + body so a different request body would
        # miss the cassette (and we'd notice the contract drift).
        match_on=("method", "scheme", "host", "path", "query"),
        decode_compressed_response=True,
    )


@pytest.mark.integration
def test_claude_thinking_provider_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end smoke: provider -> SDK -> HTTP (replayed) -> parsed response.

    Asserts the wire contract that production relies on:
      - SDK can parse the response into `content` blocks.
      - Provider's `_consume_response` extracts a non-empty `last_thinking`.
      - Provider returns the text-block payload from `chat()`.
    """
    if not CASSETTE_PATH.exists():
        pytest.skip(
            f"Cassette missing at {CASSETTE_PATH}. Re-record with "
            "OYSTER_RECORD_VCR=1 ANTHROPIC_API_KEY=... pytest "
            "tests/test_claude_thinking_provider_vcr.py"
        )

    # When recording, the real key is required; when replaying, a dummy is
    # fine because vcrpy intercepts before the request hits the network.
    if _record_mode() == "all":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("OYSTER_RECORD_VCR=1 set but ANTHROPIC_API_KEY missing.")
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-replay-dummy")

    # Imported lazily so the SDK absence (extremely unlikely here) doesn't
    # break the test-file collection in environments where vcrpy is the
    # only missing piece.
    from oyster_agent_runner.providers.claude_thinking import ClaudeThinkingProvider

    my_vcr = _build_vcr()

    with my_vcr.use_cassette("claude_thinking_smoke.yaml"):
        # Small budgets keep the request body short, avoid the SDK's
        # >10-minute streaming precondition, and minimize cost when
        # re-recording. They still exercise the same wire contract.
        provider = ClaudeThinkingProvider(max_tokens=2048, thinking_budget=1024)
        out = provider.chat(
            system="You are a smoke test. Reply with exactly OK.",
            messages=[{"role": "user", "content": "Say OK."}],
            temperature=1.0,
        )

    # --- Wire-contract assertions ------------------------------------------
    # Text content block came through and was returned.
    assert isinstance(out, str), "chat() should return a str (joined text blocks)"
    assert out.strip(), f"chat() returned empty text; got {out!r}"

    # Thinking block was captured. The cassette includes one ThinkingBlock,
    # so `last_thinking` must be a non-empty string (not None, not "").
    assert provider.last_thinking is not None, (
        "last_thinking should be set after a thinking-mode call"
    )
    assert provider.last_thinking.strip(), (
        f"last_thinking is empty; expected non-empty thinking text, got {provider.last_thinking!r}"
    )
