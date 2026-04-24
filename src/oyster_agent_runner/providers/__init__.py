"""LLM providers backing the agent.

All providers implement the `LLMProvider` protocol in `base`. Concrete
providers are imported lazily so callers only pay the SDK import cost
for the provider they actually use.

Multimodal providers (`claude_vision`, `openai_vision`) additionally
expose `wants_vision = True` and a `set_next_frame(bytes)` method so
the runner can thread PNG frames into each chat turn.
"""

from oyster_agent_runner.providers.base import LLMProvider, MockLLMProvider

__all__ = ["LLMProvider", "MockLLMProvider"]
