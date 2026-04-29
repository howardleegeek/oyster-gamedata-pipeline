"""OpenAI GPT-thinking provider — structural placeholder for buyer pitch.

The buyer narrative wants "we're not Anthropic-locked": this module
registers a GPT thinking-mode provider in the provider table and wires
up the same interface as `ClaudeThinkingProvider` so callers can swap
backends. Real OpenAI o1 / gpt-5-thinking SDK integration is a future
task; today the call path either:

  1. Returns a clearly-labelled mock `CompletionResult` when no
     `OPENAI_API_KEY` is set (with a warning logged so nobody mistakes
     it for a real model response), or
  2. Calls the OpenAI SDK with the reasoning model when a key is
     present — the SDK shape mirrors `client.responses.create(...)` for
     reasoning models, but we keep the call thin so the future task can
     swap to the official `chat.completions` reasoning surface without
     a refactor.

Why a separate provider instead of extending `OpenAIProvider`?
--------------------------------------------------------------
Same logic as `claude_thinking.py`: thinking-mode requests carry
distinct billing, distinct response shape (a `reasoning` block before
the final text), and a separate feature flag (`wants_thinking_capture`)
the runner uses to decide whether to emit `LLM_THINKING` events. A
class-attribute toggle is the cleanest signal.

Auth: reads `OPENAI_API_KEY` from the environment. Never log it.

Reference
---------
https://platform.openai.com/docs/guides/reasoning
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "o1-preview"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_MAX_TOKENS = 32_000

# Sentinel string the stub returns when no API key is present. Tests
# assert on this so consumers can detect "this is a stub" responses.
STUB_WARNING = "this is a stub — real GPT integration pending; OPENAI_API_KEY not set"
STUB_THINKING = (
    "[gpt-thinking-stub] reasoning trace placeholder; real o1/gpt-5-thinking integration pending"
)
STUB_TEXT = '[gpt-thinking-stub] mock action\n<action>{"op":"noop"}</action>'


class ProviderNotAvailable(RuntimeError):  # noqa: N818 — name fixed by spec
    """Raised when a provider's optional SDK is missing.

    Distinct from `RuntimeError` (missing API key) so callers can offer
    a different remediation hint — install the package vs. set an env
    var.
    """


@dataclass(frozen=True)
class CompletionResult:
    """Structured result from a thinking-mode completion call.

    Mirrors the shape we'd want for any reasoning-capable provider:

      - `text`: the user-visible final answer (what the runner parses
        for `<action>` tags).
      - `thinking`: the captured chain-of-thought / reasoning trace.
        Empty string distinguishes "thinking-mode ran but produced
        nothing" from "we never made a call" (None on the provider's
        `last_thinking` attribute).
      - `model`: the model id that produced this result. Useful in
        manifests and analytics; keeps responses self-describing.
      - `is_stub`: True iff this came from the no-key fallback path.
        Buyers and tests can branch on this without parsing the text.
    """

    text: str
    thinking: str
    model: str
    is_stub: bool = False


def _lazy_import_openai():
    """Defer the SDK import until a provider is actually instantiated.

    Mirrors `claude_thinking.py` — keeps the base test suite SDK-free
    when the optional `openai` package isn't installed. Raises
    `ProviderNotAvailable` (not `ImportError`) so registration code
    upstream can catch a single, semantic exception type.
    """
    try:
        import openai  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ProviderNotAvailable(
            "The `openai` package is required for GPTThinkingProvider. "
            "Install with: pip install openai"
        ) from exc
    return openai


def _redact_key(message: str, key: str | None) -> str:
    """Strip an API key from a string before it leaves the process.

    OpenAI client errors (and any of our own f-string mistakes) may
    embed the key in the message body. We redact aggressively because
    a leaked key is unrecoverable — better a slightly noisy log than a
    rotation incident.
    """
    if not key:
        return message
    return message.replace(key, "***REDACTED***")


class GPTThinkingProvider:
    """LLMProvider backed by OpenAI reasoning models.

    Today this is a structural placeholder: when `OPENAI_API_KEY` is
    unset, `complete()` returns a stub `CompletionResult` and logs a
    clear warning. When the key is present, the real SDK call path is
    exercised. Either way, the surface area matches
    `ClaudeThinkingProvider` so swapping providers in the runner is a
    one-line change.

    Class attributes used by the runner for feature detection:
      - `wants_thinking_capture: bool = True`
        The runner emits an `LLM_THINKING` event with `last_thinking`
        before the regular `LLM_REASONING` event.

    Parameters
    ----------
    model:
        OpenAI reasoning model id. Default `"o1-preview"`; will move to
        `"gpt-5-thinking"` when GA.
    reasoning_effort:
        Placeholder for OpenAI's `reasoning.effort` parameter
        (low / medium / high). Currently surfaced only in the SDK call
        and as constructor metadata; future task wires it up properly.
    max_tokens:
        Total output token budget. Mirrors Claude's
        `max_completion_tokens` for reasoning models.
    client:
        Optional pre-built `openai.OpenAI` client. Tests inject a fake
        here. When None, we lazy-construct the real client at first
        `complete()` call (only if a key is present).

    Raises
    ------
    ProviderNotAvailable:
        If the `openai` package isn't importable. Distinct from
        `RuntimeError` so `_make_provider` can present a different
        remediation hint.
    """

    wants_thinking_capture: bool = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        *,
        client: object | None = None,
    ) -> None:
        # Verify the optional SDK is present before we promise anything.
        # Even when an explicit `client` is passed (test path), we still
        # attempt the import so misconfigured environments fail loudly
        # at construction rather than silently at first call.
        _lazy_import_openai()
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        # Cached at construction so test injection works deterministically;
        # in the production path the real SDK client is built lazily on
        # the first `complete()` call (when we know we have a key).
        self._client = client
        # Captured from the most recent `complete()` call. None before
        # the first call so callers can distinguish "never called" from
        # "called but no thinking returned".
        self.last_thinking: str | None = None

    # Public API --------------------------------------------------------------

    def complete(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> CompletionResult:
        """Run a thinking-mode completion.

        Returns a `CompletionResult` with both the final text and the
        captured reasoning trace. When `OPENAI_API_KEY` is not set,
        returns a stub result and logs a warning — this lets the runner
        smoke-test the integration end-to-end without a real key, and
        lets the buyer pitch demo the "multi-provider" story without
        ops touching keys.
        """
        api_key = os.environ.get("OPENAI_API_KEY")

        if not api_key and self._client is None:
            # No key, no injected client → stub path. Loud warning so
            # nobody mistakes this for a real response in logs.
            logger.warning(STUB_WARNING)
            self.last_thinking = STUB_THINKING
            return CompletionResult(
                text=STUB_TEXT,
                thinking=STUB_THINKING,
                model=self.model,
                is_stub=True,
            )

        # Real path: build the client lazily if needed and call the SDK.
        # We keep the call body in a try/except so we can redact the
        # key from any error message before re-raising — OpenAI's error
        # surface occasionally embeds the key in URLs.
        client = self._client
        if client is None:
            openai = _lazy_import_openai()
            client = openai.OpenAI(api_key=api_key)
            self._client = client

        try:
            response = self._invoke_sdk(client, messages, system_prompt)
        except Exception as exc:  # noqa: BLE001 — we re-raise after redaction
            redacted = _redact_key(str(exc), api_key)
            raise RuntimeError(f"GPTThinkingProvider.complete failed: {redacted}") from None

        return self._consume_response(response)

    # Compatibility with LLMProvider protocol ---------------------------------

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        """LLMProvider protocol shim.

        Delegates to `complete()` and returns just the text — keeps the
        runner happy while exposing the richer `CompletionResult` for
        callers who want the thinking trace inline.

        `temperature` is ignored: reasoning models use their own
        sampling and the OpenAI API rejects custom temperatures for
        them. We accept it silently to match the Claude provider's
        behaviour.
        """
        del temperature
        result = self.complete(messages=messages, system_prompt=system)
        return result.text

    # Internals ---------------------------------------------------------------

    def _invoke_sdk(
        self,
        client: object,
        messages: list[dict],
        system_prompt: str,
    ) -> object:
        """Call the OpenAI SDK with reasoning-model parameters.

        Kept as a separate method so tests can patch a fake client with
        a `chat.completions.create` attribute and assert call kwargs
        without monkeypatching the SDK module itself.

        Note: the exact OpenAI surface for reasoning models has been in
        flux (`responses.create` vs. `chat.completions.create`). We
        target `chat.completions.create` here because every supported
        SDK version exposes it; the `reasoning_effort` kwarg is passed
        through verbatim and will become a no-op on older SDKs that
        ignore unknown kwargs. The future "real integration" task will
        switch to whichever surface OpenAI standardises on.
        """
        api_messages: list[dict] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        return client.chat.completions.create(  # type: ignore[attr-defined]
            model=self.model,
            messages=api_messages,
            max_completion_tokens=self.max_tokens,
            reasoning_effort=self.reasoning_effort,
        )

    def _consume_response(self, response: object) -> CompletionResult:
        """Pull text + reasoning out of an OpenAI chat-completion response.

        OpenAI's reasoning-model response has:
          - `response.choices[0].message.content`: final text
          - `response.choices[0].message.reasoning_content`: chain of
            thought (when the SDK exposes it; older versions only
            return the text).

        We tolerate both shapes — missing reasoning becomes an empty
        string, not a None, so `last_thinking` is comparable across
        providers (mirrors Claude's "empty string distinguishes
        produced-nothing from never-called").
        """
        text = ""
        thinking = ""

        choices = getattr(response, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            if message is not None:
                text = getattr(message, "content", "") or ""
                # Some SDKs surface reasoning as a separate field; others
                # nest it under `reasoning` or only expose it via the
                # `responses` API. We probe a few names to stay
                # forward-compatible.
                thinking = (
                    getattr(message, "reasoning_content", None)
                    or getattr(message, "reasoning", None)
                    or ""
                )

        self.last_thinking = thinking
        return CompletionResult(
            text=text,
            thinking=thinking,
            model=self.model,
            is_stub=False,
        )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_REASONING_EFFORT",
    "STUB_TEXT",
    "STUB_THINKING",
    "STUB_WARNING",
    "CompletionResult",
    "GPTThinkingProvider",
    "ProviderNotAvailable",
]
