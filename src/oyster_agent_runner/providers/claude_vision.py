"""Anthropic Claude provider with image-content support.

Unlike `providers.claude.ClaudeProvider`, this provider accepts PNG
frames alongside each user message and injects them as `image` blocks
on the Anthropic API's content list. The agent therefore sees both the
textual observation *and* the rendered frame on every step.

Integration with the runner
---------------------------
The base `LLMProvider` protocol signature is text-only:

    chat(system: str, messages: list[dict], temperature: float) -> str

To keep that contract intact, `ClaudeVisionProvider.chat` pulls the
image from a protocol-level `set_next_frame(bytes)` call made by the
vision-aware runner just before each `chat`. This is a clean seam —
the runner knows about `wants_vision`, the provider keeps the frame in
an instance attribute, and text-only callers don't see the extra plumbing.

Auth: reads `ANTHROPIC_API_KEY` from the environment. Never log it.
"""

from __future__ import annotations

import base64
import os
import random
import time

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 2048
MAX_RETRIES = 5
BASE_BACKOFF_SEC = 1.0


def _lazy_import_anthropic():
    """Defer the SDK import until a provider is actually instantiated.

    Raising at module-import time would break the base test suite (which
    has no anthropic SDK installed intentionally). We bind the symbols
    at construction time so the base tests don't pay for a heavy import.
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
            "The `anthropic` package is required for ClaudeVisionProvider. "
            "Install with: pip install 'anthropic>=0.40'"
        ) from exc
    return anthropic, APIError, APIStatusError, RateLimitError


class ClaudeVisionProvider:
    """LLMProvider backed by Anthropic Claude with PNG image-content support.

    Usage
    -----
    >>> prov = ClaudeVisionProvider(model="claude-sonnet-4-5")
    >>> prov.set_next_frame(png_bytes)
    >>> reply = prov.chat(system="...", messages=[...], temperature=0.7)

    If `set_next_frame(None)` is called (or never called) the provider
    degrades to the same behavior as the text-only `ClaudeProvider`.

    `wants_vision = True` is set as a class attribute so the runner can
    feature-detect: `if getattr(provider, "wants_vision", False): ...`.
    """

    wants_vision: bool = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        *,
        image_media_type: str = "image/png",
    ) -> None:
        resolved_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it or pass `api_key=...` "
                "to ClaudeVisionProvider."
            )
        anthropic, self._APIError, self._APIStatusError, self._RateLimitError = (
            _lazy_import_anthropic()
        )
        self.model = model
        self.max_tokens = max_tokens
        self.image_media_type = image_media_type
        self._client = anthropic.Anthropic(api_key=resolved_key)
        self._next_frame: bytes | None = None

    # Vision hook -------------------------------------------------------------

    def set_next_frame(self, frame_png: bytes | None) -> None:
        """Attach a frame to the *next* chat call, then auto-clear.

        Single-use by design — forgetting to call `set_next_frame` for a
        step means that step goes in text-only, which is the safe
        fallback if an env can't render.
        """
        self._next_frame = frame_png

    # LLMProvider protocol ----------------------------------------------------

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        api_messages = self._inject_frame_into_last_user(messages, self._next_frame)
        # Consume the frame so a subsequent chat without a new set_next_frame
        # goes text-only.
        self._next_frame = None

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=api_messages,
                    temperature=temperature,
                )
                parts: list[str] = []
                for block in response.content:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(text)
                return "".join(parts)
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
            f"ClaudeVisionProvider.chat exceeded {MAX_RETRIES} retries"
        ) from last_exc

    # Internals ---------------------------------------------------------------

    def _inject_frame_into_last_user(
        self, messages: list[dict], frame_png: bytes | None
    ) -> list[dict]:
        """Return a copy of `messages` with a PNG image block prepended to
        the final user turn (when `frame_png` is not None).

        The Anthropic content-list format for mixed user messages is:

            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", ...}},
                {"type": "text", "text": "..."},
            ]}

        If the last message isn't a user turn or the frame is None, we
        return `messages` unchanged (string-content form the SDK also
        accepts).
        """
        if frame_png is None or not messages:
            return messages
        last = messages[-1]
        if last.get("role") != "user":
            return messages

        encoded = base64.standard_b64encode(frame_png).decode("ascii")
        existing_content = last.get("content", "")
        if isinstance(existing_content, str):
            text_blocks = [{"type": "text", "text": existing_content}]
        elif isinstance(existing_content, list):
            text_blocks = list(existing_content)
        else:
            text_blocks = [{"type": "text", "text": str(existing_content)}]

        image_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": self.image_media_type,
                "data": encoded,
            },
        }
        new_messages = list(messages[:-1])
        new_messages.append({"role": "user", "content": [image_block, *text_blocks]})
        return new_messages


__all__ = ["DEFAULT_MAX_TOKENS", "DEFAULT_MODEL", "ClaudeVisionProvider"]
