"""OpenAI Chat Completions provider with image-content support.

Mirrors `providers.claude_vision.ClaudeVisionProvider` — accepts a PNG
frame via `set_next_frame(bytes)` and injects it into the final user
turn as an OpenAI-style `image_url` content block using a data: URI.

Auth: reads `OPENAI_API_KEY` from the environment.
"""

from __future__ import annotations

import base64
import os
import random
import time

DEFAULT_MODEL = "gpt-4o"
DEFAULT_MAX_TOKENS = 2048
MAX_RETRIES = 5
BASE_BACKOFF_SEC = 1.0


def _lazy_import_openai():
    try:
        import openai  # type: ignore[import-not-found]
        from openai import (  # type: ignore[import-not-found]
            APIError,
            APIStatusError,
            RateLimitError,
        )
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "The `openai` package is required for OpenAIVisionProvider. "
            "Install with: pip install 'openai>=1.40'"
        ) from exc
    return openai, APIError, APIStatusError, RateLimitError


class OpenAIVisionProvider:
    """LLMProvider backed by OpenAI vision-capable chat models.

    Supports GPT-4o, GPT-4.1, GPT-5-vision, and any future model on the
    `chat.completions.create` endpoint that accepts the content-list
    format.

    Usage
    -----
    >>> prov = OpenAIVisionProvider(model="gpt-4o")
    >>> prov.set_next_frame(png_bytes)
    >>> reply = prov.chat(system="...", messages=[...], temperature=0.7)
    """

    wants_vision: bool = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        *,
        image_media_type: str = "image/png",
        image_detail: str = "auto",
    ) -> None:
        resolved_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it or pass `api_key=...` "
                "to OpenAIVisionProvider."
            )
        openai, self._APIError, self._APIStatusError, self._RateLimitError = _lazy_import_openai()
        self.model = model
        self.max_tokens = max_tokens
        self.image_media_type = image_media_type
        self.image_detail = image_detail
        self._client = openai.OpenAI(api_key=resolved_key)
        self._next_frame: bytes | None = None

    # Vision hook -------------------------------------------------------------

    def set_next_frame(self, frame_png: bytes | None) -> None:
        self._next_frame = frame_png

    # LLMProvider protocol ----------------------------------------------------

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        full_messages: list[dict] = [{"role": "system", "content": system}]
        full_messages.extend(self._inject_frame_into_last_user(messages, self._next_frame))
        self._next_frame = None

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=full_messages,
                    max_tokens=self.max_tokens,
                    temperature=temperature,
                )
                return response.choices[0].message.content or ""
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
            f"OpenAIVisionProvider.chat exceeded {MAX_RETRIES} retries"
        ) from last_exc

    # Internals ---------------------------------------------------------------

    def _inject_frame_into_last_user(
        self, messages: list[dict], frame_png: bytes | None
    ) -> list[dict]:
        """Inject an `image_url` block into the final user turn.

        OpenAI's content-list format for mixed user messages:

            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                {"type": "text", "text": "..."},
            ]}
        """
        if frame_png is None or not messages:
            return messages
        last = messages[-1]
        if last.get("role") != "user":
            return messages

        encoded = base64.standard_b64encode(frame_png).decode("ascii")
        data_uri = f"data:{self.image_media_type};base64,{encoded}"

        existing_content = last.get("content", "")
        if isinstance(existing_content, str):
            text_blocks = [{"type": "text", "text": existing_content}]
        elif isinstance(existing_content, list):
            text_blocks = list(existing_content)
        else:
            text_blocks = [{"type": "text", "text": str(existing_content)}]

        image_block = {
            "type": "image_url",
            "image_url": {"url": data_uri, "detail": self.image_detail},
        }
        new_messages = list(messages[:-1])
        new_messages.append({"role": "user", "content": [image_block, *text_blocks]})
        return new_messages


__all__ = ["DEFAULT_MAX_TOKENS", "DEFAULT_MODEL", "OpenAIVisionProvider"]
