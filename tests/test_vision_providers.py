"""Vision provider tests — verify PNG frames are encoded as image blocks.

Both providers are constructed with a fake API client so these tests
don't require a live API key and don't hit the network. The SDK imports
happen lazily at construction time; we inject a fake client via
monkeypatching the `_client` attribute after construction.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from oyster_agent_runner.providers.claude_vision import ClaudeVisionProvider
from oyster_agent_runner.providers.openai_vision import OpenAIVisionProvider

FAKE_PNG = b"\x89PNG\r\n\x1a\n<<fake frame bytes>>"


# --- Claude vision ----------------------------------------------------------


class _FakeAnthropicClient:
    """Stand-in for anthropic.Anthropic — captures the messages.create call."""

    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None
        self.messages = self  # expose `.messages.create(...)`

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs

        class _Block:
            def __init__(self, text: str) -> None:
                self.text = text

        class _Resp:
            content = [_Block('Mocked reasoning\n<action>{"op": "noop"}</action>')]

        return _Resp()


def _install_fake_client(provider: ClaudeVisionProvider) -> _FakeAnthropicClient:
    fake = _FakeAnthropicClient()
    provider._client = fake  # type: ignore[assignment]
    return fake


def test_claude_vision_provider_declares_wants_vision() -> None:
    assert ClaudeVisionProvider.wants_vision is True


def test_claude_vision_provider_injects_image_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-for-testing")
    prov = ClaudeVisionProvider(model="claude-sonnet-4-5")
    fake = _install_fake_client(prov)

    prov.set_next_frame(FAKE_PNG)
    text = prov.chat(
        system="system",
        messages=[{"role": "user", "content": "observation text"}],
        temperature=0.0,
    )
    # Returned text is the fake block concatenation.
    assert "<action>" in text

    # Inspect what was actually sent to Anthropic.
    assert fake.last_kwargs is not None
    sent = fake.last_kwargs["messages"]
    assert len(sent) == 1
    assert sent[0]["role"] == "user"
    content_blocks = sent[0]["content"]
    assert isinstance(content_blocks, list)
    # Image block is first (so the model sees the frame before the text).
    image_blocks = [b for b in content_blocks if b.get("type") == "image"]
    text_blocks = [b for b in content_blocks if b.get("type") == "text"]
    assert len(image_blocks) == 1
    assert len(text_blocks) == 1
    src = image_blocks[0]["source"]
    assert src["type"] == "base64"
    assert src["media_type"] == "image/png"
    # Round-trip the base64 to confirm the PNG bytes survived unchanged.
    assert base64.standard_b64decode(src["data"]) == FAKE_PNG
    # Text is preserved.
    assert text_blocks[0]["text"] == "observation text"


def test_claude_vision_provider_text_only_when_no_frame_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-for-testing")
    prov = ClaudeVisionProvider()
    fake = _install_fake_client(prov)

    prov.chat(system="system", messages=[{"role": "user", "content": "hi"}], temperature=0.0)

    assert fake.last_kwargs is not None
    sent = fake.last_kwargs["messages"]
    # No frame was set → message should pass through unchanged (string content).
    assert sent[0]["content"] == "hi"


def test_claude_vision_provider_consumes_frame_after_one_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling chat twice without a re-set_next_frame sends the 2nd text-only."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-for-testing")
    prov = ClaudeVisionProvider()
    fake = _install_fake_client(prov)

    prov.set_next_frame(FAKE_PNG)
    prov.chat(system="s", messages=[{"role": "user", "content": "first"}], temperature=0.0)
    first_kwargs = fake.last_kwargs
    assert isinstance(first_kwargs["messages"][0]["content"], list)

    # Second call: no new frame set.
    prov.chat(system="s", messages=[{"role": "user", "content": "second"}], temperature=0.0)
    second_kwargs = fake.last_kwargs
    assert second_kwargs["messages"][0]["content"] == "second"


def test_claude_vision_provider_rejects_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        ClaudeVisionProvider()


# --- OpenAI vision ----------------------------------------------------------


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs

        class _Msg:
            content = 'openai reasoning\n<action>{"op": "noop"}</action>'

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


def test_openai_vision_provider_declares_wants_vision() -> None:
    assert OpenAIVisionProvider.wants_vision is True


def test_openai_vision_provider_injects_image_url_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    prov = OpenAIVisionProvider(model="gpt-4o")
    fake = _FakeOpenAIClient()
    prov._client = fake  # type: ignore[assignment]

    prov.set_next_frame(FAKE_PNG)
    text = prov.chat(
        system="system",
        messages=[{"role": "user", "content": "describe"}],
        temperature=0.2,
    )
    assert "<action>" in text

    assert fake.last_kwargs is not None
    sent = fake.last_kwargs["messages"]
    # system prepended.
    assert sent[0] == {"role": "system", "content": "system"}
    last_user = sent[-1]
    assert last_user["role"] == "user"
    content = last_user["content"]
    assert isinstance(content, list)
    image_blocks = [b for b in content if b.get("type") == "image_url"]
    assert len(image_blocks) == 1
    url = image_blocks[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    encoded = url.split(",", 1)[1]
    assert base64.standard_b64decode(encoded) == FAKE_PNG


def test_openai_vision_provider_text_only_when_no_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    prov = OpenAIVisionProvider()
    fake = _FakeOpenAIClient()
    prov._client = fake  # type: ignore[assignment]

    prov.chat(system="s", messages=[{"role": "user", "content": "hi"}], temperature=0.0)
    last_user = fake.last_kwargs["messages"][-1]
    assert last_user["content"] == "hi"


# --- Runner integration -----------------------------------------------------


def test_runner_threads_frame_to_vision_provider(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """End-to-end: runner + vision provider + vision-capable env → frame arrives."""
    from oyster_agent_runner.environments.base import MockEnvironment
    from oyster_agent_runner.runner import AgentRunner, RunnerConfig
    from oyster_agent_runner.schema import AgentTask

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    prov = ClaudeVisionProvider()
    fake = _install_fake_client(prov)

    task = AgentTask(
        task_id="vision-e2e",
        natural_language_instruction="do 3 noops",
        max_steps=3,
        environment="mock",
        required_provider_model="claude-sonnet-4-5",
    )
    env = MockEnvironment(done_after_steps=3)
    runner = AgentRunner(RunnerConfig(write_frames=False, temperature=0.0))
    runner.run(task, env, prov, tmp_path / "run")

    # The last chat() call should have included an image block because
    # MockEnvironment's last_frame() was populated by _safe_render() or
    # env.render_frame() during the step before.
    assert fake.last_kwargs is not None
    content = fake.last_kwargs["messages"][-1]["content"]
    # At least one image block was sent across the 3 steps.
    assert isinstance(content, list), "final user message should be content-list form"
    assert any(b.get("type") == "image" for b in content)
