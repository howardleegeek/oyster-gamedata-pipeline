"""OpenAI Chat Completions provider.

Auth: reads `OPENAI_API_KEY` from the environment.
"""

from __future__ import annotations

import os
import random
import time

try:
    import openai
    from openai import APIError, APIStatusError, RateLimitError
except ImportError as exc:  # pragma: no cover — import-time guard
    raise ImportError(
        "The `openai` package is required. Install with: pip install 'openai>=1.40'"
    ) from exc

DEFAULT_MODEL = "gpt-4o"
DEFAULT_MAX_TOKENS = 2048
MAX_RETRIES = 5
BASE_BACKOFF_SEC = 1.0


class OpenAIProvider:
    """LLMProvider backed by OpenAI Chat Completions."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it or pass `api_key=...` to OpenAIProvider."
            )
        self.model = model
        self.max_tokens = max_tokens
        self._client = openai.OpenAI(api_key=resolved_key)

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        # OpenAI expects system as a role-message; prepend it.
        full_messages: list[dict] = [{"role": "system", "content": system}, *messages]

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=full_messages,
                    max_tokens=self.max_tokens,
                    temperature=temperature,
                )
                choice = response.choices[0]
                content = choice.message.content or ""
                return content
            except RateLimitError as exc:
                last_exc = exc
                sleep_for = BASE_BACKOFF_SEC * (2**attempt) + random.uniform(0, 0.5)
                time.sleep(sleep_for)
            except APIStatusError as exc:
                last_exc = exc
                if exc.status_code and 500 <= exc.status_code < 600:
                    time.sleep(BASE_BACKOFF_SEC * (2**attempt))
                else:
                    raise
            except APIError as exc:
                last_exc = exc
                time.sleep(BASE_BACKOFF_SEC * (2**attempt))

        assert last_exc is not None
        raise RuntimeError(f"OpenAIProvider.chat exceeded {MAX_RETRIES} retries") from last_exc


__all__ = ["DEFAULT_MAX_TOKENS", "DEFAULT_MODEL", "OpenAIProvider"]
