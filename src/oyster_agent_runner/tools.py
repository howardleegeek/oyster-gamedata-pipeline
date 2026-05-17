"""Tool-use primitives — let agents declare & call named tools per step.

An agent can emit a special action of the form

    {"op": "call_tool", "tool": "find_resource", "args": {"kind": "wood"}}

The runner detects `op == "call_tool"`, routes to the `ToolProvider`
registered for the run, and logs a `TOOL_CALL` event alongside the
normal ACTION event. The tool's return value is appended to the next
user message so the agent can react.

This is intentionally independent of the Anthropic / OpenAI function-
calling protocols — those are provider-specific JSON schemas the
agent emits through the chat turn. We stay provider-agnostic by making
tool invocation just another kind of action the runner dispatches.

Environment-specific tools
--------------------------
For a Minecraft agent:

    tools = SimpleToolProvider([
        Tool(name="find_resource", description="Locate nearest block of a kind",
             handler=lambda args: {"x": 3, "y": 64, "z": -1}),
        Tool(name="craft_item", description="Craft N of a recipe",
             handler=lambda args: {"ok": True, "crafted": args["count"]}),
    ])
    runner.run(task, env, provider, output_dir, tools=tools)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class Tool:
    """A named tool the agent can invoke.

    `handler` MUST be side-effect safe — the runner doesn't sandbox
    anything. `returns_json` hints whether the handler's return value
    is already JSON-serializable (dict / list / scalar); if False the
    runner falls back to `repr()` before logging.
    """

    name: str
    description: str
    handler: ToolHandler
    returns_json: bool = True


@runtime_checkable
class ToolProvider(Protocol):
    """Adapter surface for a pool of tools available during a run."""

    def list_tools(self) -> list[Tool]:
        """Return the currently-registered tools in a stable order."""
        ...

    def call(self, name: str, args: dict[str, Any]) -> Any:
        """Invoke a tool by name. Raises KeyError for unknown names."""
        ...


# --- Ergonomic default implementation ----------------------------------------


class SimpleToolProvider:
    """Minimal ToolProvider — keep tools in a dict keyed by name.

    Initialize with a list of Tool dataclasses; later tools with the
    same name overwrite earlier ones (last-wins semantics matches
    typical registration patterns).
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        """Register a tool, overwriting any existing tool with the same name.

        Args:
            tool: The Tool instance to register. Its `name` attribute is used
                as the key for lookup and deduplication.
        """
        self._tools[tool.name] = tool

    def list_tools(self) -> list[Tool]:
        """Return the list of registered tools in registration order.

        Returns:
            A list of Tool instances currently registered with this provider.
        """
        return list(self._tools.values())

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(f"tool not registered: {name!r}")
        tool = self._tools[name]
        return tool.handler(args)


# --- Agent-facing prompt helper ---------------------------------------------


def tool_catalog_prompt(tools: list[Tool]) -> str:
    """Render the tool catalog as a system-prompt snippet.

    Example output:
        Available tools:
          - find_resource: Locate nearest block of a kind
          - craft_item: Craft N of a recipe

    Emit the action as `{"op": "call_tool", "tool": "<name>",
    "args": {...}}`.
    """
    if not tools:
        return ""
    lines = ["Available tools (invoke via `call_tool` action):"]
    for t in tools:
        lines.append(f"  - {t.name}: {t.description}")
    lines.append(
        "\nTo invoke a tool, emit: " '`{"op": "call_tool", "tool": "<name>", "args": {...}}`'
    )
    return "\n".join(lines)


# Event types for the trajectory log — kept here (not in schema) so the
# core schema stays minimal and the tool-use subsystem is opt-in.
EVENT_TOOL_CALL = "TOOL_CALL"
EVENT_TOOL_RESULT = "TOOL_RESULT"


__all__ = [
    "EVENT_TOOL_CALL",
    "EVENT_TOOL_RESULT",
    "SimpleToolProvider",
    "Tool",
    "ToolHandler",
    "ToolProvider",
    "tool_catalog_prompt",
]