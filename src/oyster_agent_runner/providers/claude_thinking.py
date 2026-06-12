"""Anthropic Claude provider with extended-thinking capture.

This wraps the Anthropic Messages API in **thinking mode** so the bot's
chain-of-thought is captured verbatim alongside the final action text.

The thinking blocks land in the response's `content` list before the
final `text` block. We collect *all* thinking text (Claude can interleave
multiple thinking blocks) and expose it via `last_thinking` so the
runner can emit a separate `LLM_THINKING` trajectory event in addition
to the regular `LLM_REASONING` (which captures the final answer text).

Why a separate provider instead of a flag on `ClaudeProvider`?
--------------------------------------------------------------
1. Thinking-mode requests are billed at higher cost and can take 5-15 s
   per turn — opt-in via class is the cleanest signal.
2. Thinking-mode requires `temperature=1.0` (Anthropic API restriction
   — see the SDK docs); the wrapper enforces it without surprising
   callers of the regular provider.
3. The runner uses `wants_thinking_capture` as a feature flag to decide
   whether to emit the `LLM_THINKING` event — keeping the contract
   class-attribute-based matches the existing `wants_vision` pattern in
   `claude_vision.py`.

Auth: reads `ANTHROPIC_API_KEY` from the environment. Never log it.

Reference
---------
https://docs.claude.com/en/docs/build-with-claude/extended-thinking
"""

from __future__ import annotations

import os
import random
import time

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 32_000  # must be > thinking_budget_tokens; spec calls for ~16K thinking
DEFAULT_THINKING_BUDGET = 16_000
MAX_RETRIES = 5
BASE_BACKOFF_SEC = 1.0


def _lazy_import_anthropic():
    """Defer the SDK import until a provider is actually instantiated.

    Mirrors `claude_vision.py` — keeps the base test suite SDK-free.
    """
    try:
        import anthropic  # type: ignore[import-not-found]
        from anthropic import (  # type: ignore[import-not-found]
            APIError,
            APIStatusError,
            RateLimitError,
        )
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "The `anthropic` package is required for ClaudeThinkingProvider. "
            "Install with: pip install 'anthropic>=0.40'"
        ) from exc
    return anthropic, APIError, APIStatusError, RateLimitError


class ClaudeThinkingProvider:
    """LLMProvider backed by Claude with extended thinking enabled.

    The provider's `chat(...)` method returns the **action text** (the
    final assistant turn the runner parses for `<action>` tags) but
    captures the full thinking trace into `self.last_thinking` for the
    runner to emit as a `LLM_THINKING` event.

    Class attributes used by the runner for feature detection:
      - `wants_thinking_capture: bool = True`
        The runner emits an `LLM_THINKING` event with `last_thinking`
        before the regular `LLM_REASONING` event.

    Parameters
    ----------
    model:
        Claude model id. Default is `claude-sonnet-4-5`.
    api_key:
        Override `ANTHROPIC_API_KEY` env var.
    max_tokens:
        Total output token budget. MUST be greater than `thinking_budget`
        because thinking tokens count against `max_tokens`.
    thinking_budget:
        How many tokens Claude is allowed to spend reasoning. The spec
        calls for 16,000.

    Raises
    ------
    RuntimeError:
        If `ANTHROPIC_API_KEY` is unset and `api_key=None`.
    ValueError:
        If `max_tokens <= thinking_budget`.
    """

    wants_thinking_capture: bool = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        *,
        thinking_budget: int = DEFAULT_THINKING_BUDGET,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it or pass `api_key=...` "
                "to ClaudeThinkingProvider."
            )
        if max_tokens <= thinking_budget:
            raise ValueError(
                f"max_tokens ({max_tokens}) must exceed thinking_budget ({thinking_budget}); "
                f"thinking output is counted against max_tokens by the API."
            )
        # Defer SDK import + client creation to first chat() call so that
        # unit tests can inject a fake _client without needing anthropic installed.
        self._resolved_key: str = resolved_key
        self._client: object | None = None
        self._APIError: type | None = None
        self._APIStatusError: type | None = None
        self._RateLimitError: type | None = None
        self.model = model
        self.max_tokens = max_tokens
        self.thinking_budget = thinking_budget
        # Captured from the most recent `chat()` call. None before the
        # first call. Empty string if the response had no thinking blocks
        # (e.g. SDK degradation), which the runner can distinguish from
        # None to know whether thinking-mode was actually requested.
        self.last_thinking: str | None = None

    # Internals ---------------------------------------------------------------

    def _ensure_client(self) -> None:
        """Lazily create the Anthropic client on first use.

        Deferring this lets unit tests inject a fake ``_client`` attribute
        without needing the ``anthropic`` package installed.
        """
        if self._client is not None:
            return
        anthropic, self._APIError, self._APIStatusError, self._RateLimitError = (
            _lazy_import_anthropic()
        )
        self._client = anthropic.Anthropic(api_key=self._resolved_key)

    # LLMProvider protocol ----------------------------------------------------

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        """Send a thinking-mode request, capture thinking, return action text.

        Note on `temperature`: Anthropic's extended-thinking API requires
        `temperature=1.0`. We honor that silently — the caller's value is
        ignored when thinking is enabled. Logging this every call would
        spam the trajectory; we trust the operator to read the docstring.
        """
        # Thinking mode is incompatible with non-default temperature; force it
        # to 1.0 regardless of what the runner passed in.
        del temperature

        self._ensure_client()

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=messages,
                    temperature=1.0,
                    thinking={
                        "type": "enabled",
                        "budget_tokens": self.thinking_budget,
                    },
                )
                return self._consume_response(response)
            except self._RateLimitError as exc:  # type: ignore[misc]
                last_exc = exc
                time.sleep(BASE_BACKOFF_SEC * (2**attempt) + random.uniform(0, 0.5))
            except self._APIStatusError as exc:  # type: ignore[misc]
                last_exc = exc
                status = getattr(exc, "status_code", None)
                if status and 500 <= status < 600:
                    time.sleep(BASE_BACKOFF_SEC * (2**attempt))
                else:
                    raise
            except self._APIError as exc:  # type: ignore[misc]
                last_exc = exc
                time.sleep(BASE_BACKOFF_SEC * (2**attempt))

        assert last_exc is not None
        raise RuntimeError(
            f"ClaudeThinkingProvider.chat exceeded {MAX_RETRIES} retries"
        ) from last_exc

    # Internals ---------------------------------------------------------------

    def _consume_response(self, response: object) -> str:
        """Pull thinking + text out of an Anthropic response object.

        The SDK returns a `Message` whose `content` is an ordered list of
        blocks. Possible block types in thinking mode:
          - `thinking` — `{type, thinking, signature}`
          - `redacted_thinking` — `{type, data}` (we surface a marker
            string but cannot include the redacted text itself)
          - `text` — `{type, text}` (the final action)
          - `tool_use` etc. — Phase 1 doesn't enable tools; ignored.

        We concatenate ALL `thinking.thinking` strings into
        `self.last_thinking` and return the concatenated `text` blocks
        for the runner to parse.
        """
        thinking_parts: list[str] = []
        text_parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "thinking":
                thinking_text = getattr(block, "thinking", None)
                if thinking_text:
                    thinking_parts.append(thinking_text)
            elif block_type == "redacted_thinking":
                # We can't expose the redacted content (Anthropic encrypts
                # it for safety), but we record that thinking happened so
                # the trajectory doesn't silently lose a step's reasoning.
                thinking_parts.append("[redacted_thinking_block]")
            elif block_type == "text" or hasattr(block, "text"):
                text = getattr(block, "text", None)
                if text:
                    text_parts.append(text)
        # Persist whatever we found (empty string distinguishes "thinking
        # mode ran but produced nothing" from "we never made a call").
        self.last_thinking = "\n".join(thinking_parts)
        return "".join(text_parts)


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_THINKING_BUDGET",
    "ClaudeThinkingProvider",
]
