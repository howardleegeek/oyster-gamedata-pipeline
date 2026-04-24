"""Anthropic Claude provider.

Uses the official `anthropic` Python SDK. The SDK handles retries for
rate-limits (429) and 5xx automatically, but we layer our own bounded
retry with exponential backoff on top so a transient outage doesn't
break a long-running data-collection session.

Auth: reads `ANTHROPIC_API_KEY` from the environment. Never log it.
"""

from __future__ import annotations

import os
import random
import time

try:
    import anthropic
    from anthropic import APIError, APIStatusError, RateLimitError
except ImportError as exc:  # pragma: no cover — import-time guard
    raise ImportError(
        "The `anthropic` package is required. Install with: pip install 'anthropic>=0.40'"
    ) from exc

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 2048
MAX_RETRIES = 5
BASE_BACKOFF_SEC = 1.0


class ClaudeProvider:
    """LLMProvider backed by Anthropic Claude."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it or pass `api_key=...` to ClaudeProvider."
            )
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=resolved_key)

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=messages,
                    temperature=temperature,
                )
                # Concatenate all text blocks — Claude occasionally emits multiple.
                parts: list[str] = []
                for block in response.content:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(text)
                return "".join(parts)
            except RateLimitError as exc:
                last_exc = exc
                sleep_for = BASE_BACKOFF_SEC * (2**attempt) + random.uniform(0, 0.5)
                time.sleep(sleep_for)
            except APIStatusError as exc:
                last_exc = exc
                # Only retry 5xx — 4xx other than 429 is a permanent error.
                if exc.status_code and 500 <= exc.status_code < 600:
                    time.sleep(BASE_BACKOFF_SEC * (2**attempt))
                else:
                    raise
            except APIError as exc:
                last_exc = exc
                time.sleep(BASE_BACKOFF_SEC * (2**attempt))

        assert last_exc is not None
        raise RuntimeError(f"ClaudeProvider.chat exceeded {MAX_RETRIES} retries") from last_exc


__all__ = ["ClaudeProvider", "DEFAULT_MAX_TOKENS", "DEFAULT_MODEL"]
