"""LLM provider protocol + a deterministic MockLLMProvider for tests."""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Chat-completions adapter.

    `messages` is an OpenAI-style list: `[{"role": "user"|"assistant", "content": str}]`.
    The provider MUST return the full text of the assistant's reply.
    """

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        ...


# --- Mock provider for tests -------------------------------------------------


class MockLLMProvider:
    """Deterministic provider — echoes a canned action.

    Reply format the runner expects from any provider:
        Reasoning about the observation...
        <action>{"op": "noop"}</action>

    `MockLLMProvider` always emits the same reasoning + the action stored
    in `canned_action`, making end-to-end tests fully deterministic.
    """

    def __init__(self, canned_action: dict | None = None, reasoning: str = "mock reasoning") -> None:
        self.canned_action = canned_action if canned_action is not None else {"op": "noop"}
        self.reasoning = reasoning
        self.call_count = 0

    def chat(self, system: str, messages: list[dict], temperature: float) -> str:
        self.call_count += 1
        action_json = json.dumps(self.canned_action)
        return f"{self.reasoning}\n<action>{action_json}</action>"


__all__ = ["LLMProvider", "MockLLMProvider"]
