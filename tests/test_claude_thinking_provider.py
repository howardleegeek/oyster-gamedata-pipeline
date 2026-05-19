"""Unit tests for `ClaudeThinkingProvider` — extended-thinking capture.

We mock the Anthropic SDK's response shape:

    response.content = [
        ThinkingBlock(type="thinking", thinking="...", signature="..."),
        TextBlock(type="text", text="reasoning + <action>...</action>"),
    ]

and verify:
  1. `wants_thinking_capture` is True at the class level.
  2. `chat(...)` returns ONLY the text block content.
  3. `last_thinking` is populated from the thinking block(s).
  4. Multiple thinking blocks concatenate with newlines.
  5. Redacted thinking blocks are surfaced as a marker (not silently dropped).
  6. `thinking={"type": "enabled", "budget_tokens": <n>}` is passed to the SDK.
  7. `temperature=1.0` is forced regardless of caller value.
  8. Missing API key raises RuntimeError early.
  9. max_tokens <= thinking_budget raises ValueError early.
"""

from __future__ import annotations

from typing import Any

import pytest

from oyster_agent_runner.providers.claude_thinking import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_THINKING_BUDGET,
    ClaudeThinkingProvider,
)

# --- Fake SDK objects --------------------------------------------------------


class _FakeThinkingBlock:
    """Stand-in for anthropic.types.ThinkingBlock (`.type == 'thinking'`)."""

    def __init__(self, thinking: str, signature: str = "sig") -> None:
        self.type = "thinking"
        self.thinking = thinking
        self.signature = signature


class _FakeRedactedBlock:
    def __init__(self, data: str = "<<encrypted>>") -> None:
        self.type = "redacted_thinking"
        self.data = data


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, blocks: list[Any]) -> None:
        self.content = blocks


class _FakeAnthropicClient:
    """Captures the `messages.create` kwargs and returns a canned response."""

    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.last_kwargs: dict[str, Any] | None = None
        self.call_count = 0
        self._response = response or _FakeResponse(
            [
                _FakeThinkingBlock("Default mock thinking."),
                _FakeTextBlock('Default mock reasoning\n<action>{"op":"noop"}</action>'),
            ]
        )
        self.messages = self  # expose `.messages.create(...)`

    def create(self, **kwargs: Any) -> Any:
        self.call_count += 1
        self.last_kwargs = kwargs
        return self._response


def _build_provider(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> ClaudeThinkingProvider:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-for-testing")
    prov = ClaudeThinkingProvider(**kwargs)
    return prov


def _install_fake(
    prov: ClaudeThinkingProvider, response: _FakeResponse | None = None
) -> _FakeAnthropicClient:
    fake = _FakeAnthropicClient(response=response)
    prov._client = fake  # type: ignore[assignment]
    return fake


# --- Tests ------------------------------------------------------------------


def test_wants_thinking_capture_is_true_at_class_level() -> None:
    """Runner uses the class attribute for feature detection — must be set
    even before construction."""
    assert ClaudeThinkingProvider.wants_thinking_capture is True


def test_chat_returns_only_text_block_content(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = _build_provider(monkeypatch)
    _install_fake(
        prov,
        _FakeResponse(
            [
                _FakeThinkingBlock("Should not appear in return value."),
                _FakeTextBlock('Final answer\n<action>{"op":"noop"}</action>'),
            ]
        ),
    )
    out = prov.chat(
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        temperature=0.7,
    )
    assert "Final answer" in out
    assert "<action>" in out
    # Crucially, the thinking text is NOT mixed into the chat() return.
    assert "Should not appear" not in out


def test_chat_captures_thinking_into_last_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = _build_provider(monkeypatch)
    _install_fake(
        prov,
        _FakeResponse(
            [
                _FakeThinkingBlock("Step 1: I should look around for a tree."),
                _FakeTextBlock('Heading north.\n<action>{"op":"look","yaw":0,"pitch":0}</action>'),
            ]
        ),
    )
    prov.chat(
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        temperature=0.7,
    )
    assert prov.last_thinking == "Step 1: I should look around for a tree."


def test_multiple_thinking_blocks_concatenate(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = _build_provider(monkeypatch)
    _install_fake(
        prov,
        _FakeResponse(
            [
                _FakeThinkingBlock("First chunk of reasoning."),
                _FakeThinkingBlock("Second chunk after refinement."),
                _FakeTextBlock('answer\n<action>{"op":"noop"}</action>'),
            ]
        ),
    )
    prov.chat(system="sys", messages=[{"role": "user", "content": "go"}], temperature=0.0)
    assert prov.last_thinking == "First chunk of reasoning.\nSecond chunk after refinement."


def test_redacted_thinking_block_surfaces_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redacted blocks shouldn't silently drop — we record that thinking
    happened so the trajectory's reasoning chain isn't deceptive."""
    prov = _build_provider(monkeypatch)
    _install_fake(
        prov,
        _FakeResponse(
            [
                _FakeRedactedBlock(data="encrypted-bytes"),
                _FakeTextBlock('answer\n<action>{"op":"noop"}</action>'),
            ]
        ),
    )
    prov.chat(system="sys", messages=[{"role": "user", "content": "go"}], temperature=0.0)
    assert "[redacted_thinking_block]" in (prov.last_thinking or "")


def test_chat_passes_thinking_config_to_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    prov = _build_provider(monkeypatch, thinking_budget=8000)
    fake = _install_fake(prov)
    prov.chat(system="sys", messages=[{"role": "user", "content": "x"}], temperature=0.5)
    assert fake.last_kwargs is not None
    thinking_arg = fake.last_kwargs.get("thinking")
    assert thinking_arg == {"type": "enabled", "budget_tokens": 8000}


def test_chat_forces_temperature_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic's thinking-mode requires temperature=1.0 — provider enforces it."""
    prov = _build_provider(monkeypatch)
    fake = _install_fake(prov)
    prov.chat(system="sys", messages=[{"role": "user", "content": "x"}], temperature=0.0)
    assert fake.last_kwargs["temperature"] == 1.0


def test_default_thinking_budget_matches_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec says thinking_budget should default to 16000 tokens."""
    assert DEFAULT_THINKING_BUDGET == 16_000
    prov = _build_provider(monkeypatch)
    fake = _install_fake(prov)
    prov.chat(system="s", messages=[{"role": "user", "content": "x"}], temperature=1.0)
    assert fake.last_kwargs["thinking"]["budget_tokens"] == 16_000


def test_max_tokens_must_exceed_thinking_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    with pytest.raises(ValueError, match="must exceed thinking_budget"):
        ClaudeThinkingProvider(max_tokens=8000, thinking_budget=8000)


def test_provider_rejects_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        ClaudeThinkingProvider()


def test_default_max_tokens_exceeds_default_thinking_budget() -> None:
    """Sanity check on module-level constants — required for the default
    constructor to succeed."""
    assert DEFAULT_MAX_TOKENS > DEFAULT_THINKING_BUDGET
