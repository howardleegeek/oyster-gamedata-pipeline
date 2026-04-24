"""LLM providers backing the agent.

All providers implement the `LLMProvider` protocol in `base`. Concrete
providers are imported lazily so callers only pay the SDK import cost
for the provider they actually use.
"""

from oyster_agent_runner.providers.base import LLMProvider, MockLLMProvider

__all__ = ["LLMProvider", "MockLLMProvider"]
