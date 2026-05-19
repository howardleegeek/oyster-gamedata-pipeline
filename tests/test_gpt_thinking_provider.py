"""Unit tests for `GPTThinkingProvider` — structural placeholder for
the GPT-5 / o1 reasoning provider that ships in the buyer pitch.

The point of these tests is *not* to validate real OpenAI behaviour
(that's the future "real integration" task) — it's to lock down:

  1. The provider raises `ProviderNotAvailable` when `openai` is unimportable.
  2. A test-injected fake client lets us exercise `complete()` end-to-end.
  3. `complete()` returns a `CompletionResult` whose `thinking` field
     is populated from the SDK's reasoning_content surface.
  4. `last_thinking` mirrors `result.thinking` after each call.
  5. The fake client receives the right call kwargs (model + messages
     + reasoning_effort + max_completion_tokens).
  6. Default `model` and `reasoning_effort` match the spec's placeholders.
  7. API keys never appear in error messages — `_redact_key` works.
  8. The CLI's `list-providers` command surfaces `gpt-thinking`.
  9. (Bonus) Stub path: no API key + no injected client → `is_stub=True`
     `CompletionResult` with the stub warning logged. This is the path
     the buyer pitch demos.
 10. (Bonus) `wants_thinking_capture` is True at the class level so the
     runner's feature detection works without an instance.
"""

from __future__ import annotations

import builtins
import logging
from typing import Any

import pytest
from typer.testing import CliRunner

from oyster_agent_runner.cli import app
from oyster_agent_runner.providers.gpt_thinking import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    STUB_WARNING,
    CompletionResult,
    GPTThinkingProvider,
    ProviderNotAvailable,
    _redact_key,
)

# --- Fake SDK objects --------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str, reasoning_content: str | None = None) -> None:
        self.content = content
        # Mirror the SDK shape: reasoning_content may be absent; tests
        # that omit it verify the empty-string fallback.
        if reasoning_content is not None:
            self.reasoning_content = reasoning_content


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, choices: list[_FakeChoice]) -> None:
        self.choices = choices


class _FakeChatCompletions:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None
        self.call_count = 0

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.call_count += 1
        self.last_kwargs = kwargs
        return self._response


class _FakeOpenAIClient:
    """Stand-in for `openai.OpenAI` that records call kwargs."""

    def __init__(self, response: _FakeResponse | None = None) -> None:
        if response is None:
            response = _FakeResponse(
                [
                    _FakeChoice(
                        _FakeMessage(
                            content='final answer\n<action>{"op":"noop"}</action>',
                            reasoning_content="step 1: think; step 2: act",
                        )
                    )
                ]
            )
        self.chat = type("_FakeChat", (), {})()
        self.chat.completions = _FakeChatCompletions(response)


# --- Tests -------------------------------------------------------------------


def test_provider_not_available_when_openai_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If `openai` can't be imported, instantiation raises ProviderNotAvailable
    with a `pip install openai` hint — distinct from a missing-key error."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openai":
            raise ImportError("No module named 'openai' (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ProviderNotAvailable, match="pip install openai"):
        GPTThinkingProvider()


def test_instantiation_with_mock_client_succeeds() -> None:
    """A pre-built fake client lets us construct the provider without
    touching the real openai SDK or any environment vars."""
    fake = _FakeOpenAIClient()
    prov = GPTThinkingProvider(client=fake)
    assert prov.model == DEFAULT_MODEL
    assert prov.reasoning_effort == DEFAULT_REASONING_EFFORT
    assert prov.last_thinking is None  # no calls yet


def test_complete_returns_completion_result_with_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: fake client returns a populated reasoning_content,
    `complete()` surfaces it in the CompletionResult."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-do-not-use")
    fake = _FakeOpenAIClient(
        _FakeResponse(
            [
                _FakeChoice(
                    _FakeMessage(
                        content='Heading north.\n<action>{"op":"look"}</action>',
                        reasoning_content="I should orient before moving.",
                    )
                )
            ]
        )
    )
    prov = GPTThinkingProvider(client=fake)

    result = prov.complete(
        messages=[{"role": "user", "content": "go"}],
        system_prompt="you are a minecraft agent",
    )

    assert isinstance(result, CompletionResult)
    assert "Heading north." in result.text
    assert "<action>" in result.text
    assert result.thinking == "I should orient before moving."
    assert result.is_stub is False
    assert result.model == DEFAULT_MODEL


def test_last_thinking_populated_after_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """`last_thinking` mirrors the most recent result's thinking field —
    the runner reads it directly to emit `LLM_THINKING` events."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-x")
    fake = _FakeOpenAIClient(
        _FakeResponse(
            [
                _FakeChoice(
                    _FakeMessage(
                        content='ans\n<action>{"op":"noop"}</action>',
                        reasoning_content="trace alpha",
                    )
                )
            ]
        )
    )
    prov = GPTThinkingProvider(client=fake)
    prov.complete(messages=[{"role": "user", "content": "x"}])
    assert prov.last_thinking == "trace alpha"


def test_complete_passes_correct_kwargs_to_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake client should see model, messages, reasoning_effort, and
    max_completion_tokens forwarded verbatim."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-y")
    fake = _FakeOpenAIClient()
    prov = GPTThinkingProvider(
        model="gpt-5-thinking",
        reasoning_effort="medium",
        max_tokens=4096,
        client=fake,
    )

    user_messages = [{"role": "user", "content": "hello"}]
    prov.complete(messages=user_messages, system_prompt="be helpful")

    kwargs = fake.chat.completions.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "gpt-5-thinking"
    assert kwargs["max_completion_tokens"] == 4096
    assert kwargs["reasoning_effort"] == "medium"
    # System prompt is prepended; user message follows.
    assert kwargs["messages"][0] == {"role": "system", "content": "be helpful"}
    assert kwargs["messages"][1] == {"role": "user", "content": "hello"}
    assert fake.chat.completions.call_count == 1


def test_default_model_and_reasoning_effort_match_spec() -> None:
    """The buyer-pitch placeholder targets o1-preview at high effort —
    lock those defaults so the spec doesn't drift silently."""
    assert DEFAULT_MODEL == "o1-preview"
    assert DEFAULT_REASONING_EFFORT == "high"
    assert DEFAULT_MAX_TOKENS == 32_000

    fake = _FakeOpenAIClient()
    prov = GPTThinkingProvider(client=fake)
    assert prov.model == "o1-preview"
    assert prov.reasoning_effort == "high"
    assert prov.max_tokens == 32_000


def test_api_key_redacted_in_error_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the SDK call raises and the exception text contains the API
    key (real-world failure mode for misconfigured proxies), the
    re-raised RuntimeError must redact it."""
    secret = "sk-this-is-very-secret-do-not-leak"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    class _ExplodingCompletions:
        def create(self, **_: Any) -> Any:
            raise RuntimeError(f"upstream 401 for key={secret} at api.openai.com")

    class _ExplodingClient:
        def __init__(self) -> None:
            self.chat = type("_C", (), {})()
            self.chat.completions = _ExplodingCompletions()

    prov = GPTThinkingProvider(client=_ExplodingClient())
    with pytest.raises(RuntimeError) as excinfo:
        prov.complete(messages=[{"role": "user", "content": "x"}])

    message = str(excinfo.value)
    assert secret not in message
    assert "***REDACTED***" in message


def test_redact_key_helper_handles_none_and_empty() -> None:
    """`_redact_key` should be safe to call with None / empty key
    (no-op), and otherwise replace every occurrence."""
    assert _redact_key("hello", None) == "hello"
    assert _redact_key("hello", "") == "hello"
    assert _redact_key("key=abc and key=abc again", "abc") == (
        "key=***REDACTED*** and key=***REDACTED*** again"
    )


def test_stub_path_when_no_key_and_no_client(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No key + no injected client = stub. Returns a labelled
    CompletionResult with `is_stub=True` and logs the warning so it's
    impossible to confuse with a real response in production logs."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    prov = GPTThinkingProvider()

    with caplog.at_level(logging.WARNING, logger="oyster_agent_runner.providers.gpt_thinking"):
        result = prov.complete(messages=[{"role": "user", "content": "anything"}])

    assert result.is_stub is True
    assert "stub" in result.text.lower()
    assert result.thinking != ""
    assert prov.last_thinking == result.thinking
    assert any(STUB_WARNING in record.message for record in caplog.records)


def test_wants_thinking_capture_is_true_at_class_level() -> None:
    """Runner uses the class attribute for feature detection — must be
    set even before construction so the `_make_provider` factory can
    decide whether to enable the LLM_THINKING event."""
    assert GPTThinkingProvider.wants_thinking_capture is True


def test_chat_protocol_shim_returns_only_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """`chat()` exists for LLMProvider protocol compatibility — it must
    return only the text (not the thinking trace) so the runner's
    action parser doesn't trip over reasoning markup."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-z")
    fake = _FakeOpenAIClient(
        _FakeResponse(
            [
                _FakeChoice(
                    _FakeMessage(
                        content='out\n<action>{"op":"noop"}</action>',
                        reasoning_content="should not appear in chat() return",
                    )
                )
            ]
        )
    )
    prov = GPTThinkingProvider(client=fake)
    out = prov.chat(
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        temperature=0.7,
    )
    assert "out" in out
    assert "<action>" in out
    assert "should not appear" not in out


def test_cli_list_providers_includes_gpt_thinking() -> None:
    """The `list-providers` table must surface the new key so the buyer
    pitch's "we're not Anthropic-locked" claim is verifiable from the
    CLI without reading source."""
    runner = CliRunner()
    result = runner.invoke(app, ["list-providers"])
    assert result.exit_code == 0, result.output
    assert "gpt-thinking" in result.output


def test_cli_list_providers_json_includes_gpt_thinking() -> None:
    """JSON output of list-providers must include the new entry too —
    machine consumers (dispatcher / ops dashboards) read the JSON form."""
    import json as _json

    runner = CliRunner()
    result = runner.invoke(app, ["list-providers", "--json"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    keys = {row["key"] for row in data}
    assert "gpt-thinking" in keys
