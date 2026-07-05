"""
Environment Plugin Registry for Oyster Agent Runner.

Discovers ``environments/*.py`` modules dynamically and provides a uniform
``AgentTask -> Environment`` factory.  Plugins are auto-registered via a
module-level ``register(registry)`` hook or by exporting an ``Environment``
subclass with a ``name`` class attribute.

Usage::

    from oyster_agent_runner.environments.registry import get_registry
    registry = get_registry()
    env = registry.create(task)          # AgentTask -> Environment
    env = registry.create("code_runner") # by name string

CLI::

    python -m oyster_agent_runner.environments.registry list
    python -m oyster_agent_runner.environments.registry create --name code_runner
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Type

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core protocols / data classes
# ---------------------------------------------------------------------------


class Environment(Protocol):
    """Minimal interface every environment plugin must satisfy."""

    name: str

    def setup(self, task: "AgentTask") -> None: ...
    def execute(self, task: "AgentTask") -> Any: ...
    def teardown(self) -> None: ...


@dataclass
class AgentTask:
    """Uniform task description passed to every environment."""

    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    work_dir: Optional[str] = None
    timeout_sec: float = 300.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.work_dir is None:
            self.work_dir = tempfile.mkdtemp(prefix="oyster_task_")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_FactoryFn = Callable[[], Type[Environment]]


def _is_env_class(cls: type) -> bool:
    """Check whether *cls* satisfies the Environment structural contract."""
    if cls is object:
        return False
    return {"name", "setup", "execute", "teardown"}.issubset(dir(cls))


class EnvironmentRegistry:
    """Central registry for environment plugins.

    Supports module-scan discovery and manual registration.
    """

    def __init__(self) -> None:
        self._factories: Dict[str, _FactoryFn] = {}
        self._discovered: bool = False

    def register_factory(self, name: str, factory: _FactoryFn) -> None:
        """Register a callable that returns an ``Environment`` class."""
        if name in self._factories:
            logger.warning("Overwriting existing environment factory: %s", name)
        self._factories[name] = factory
        logger.debug("Registered environment factory: %s", name)

    def register_class(self, cls: Type[Environment]) -> None:
        """Register an ``Environment`` subclass by its ``name`` attribute."""
        env_name = getattr(cls, "name", None)
        if not env_name:
            raise ValueError(f"Environment class {cls.__name__} has no 'name' attribute")
        self.register_factory(env_name, lambda: cls)

    def discover(self, plugin_dir: Optional[str] = None) -> int:
        """Scan *plugin_dir* for ``*.py`` modules and import them.

        Returns the number of modules successfully loaded.
        """
        if self._discovered:
            return 0

        if plugin_dir is None:
            plugin_dir = str(Path(__file__).resolve().parent)

        plugin_path = Path(plugin_dir)
        if not plugin_path.is_dir():
            logger.warning("Plugin directory does not exist: %s", plugin_dir)
            return 0

        count = 0
        for py_file in sorted(plugin_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"oyster_agent_runner.environments.{py_file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, str(py_file))
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)

                reg_fn = getattr(mod, "register", None)
                if callable(reg_fn):
                    reg_fn(self)

                for _, obj in inspect.getmembers(mod, inspect.isclass):
                    if _is_env_class(obj) and getattr(obj, "name", None):
                        self.register_class(obj)

                count += 1
                logger.info("Loaded environment plugin: %s", py_file.name)
            except Exception as exc:
                logger.debug("Failed to load plugin module %s: %s", py_file.name, exc)
                logger.exception("Failed to load plugin module: %s", py_file.name)

        self._discovered = True
        return count

    def create(self, task_or_name: "AgentTask | str", **kwargs: Any) -> Environment:
        """Create an ``Environment`` instance by name or from an ``AgentTask``."""
        name = task_or_name.name if isinstance(task_or_name, AgentTask) else task_or_name
        factory = self._factories.get(name)
        if factory is None:
            available = ", ".join(sorted(self._factories))
            raise KeyError(f"Unknown environment '{name}'. Available: {available}")
        return factory()()

    def list_names(self) -> List[str]:
        """Return sorted list of registered environment names."""
        return sorted(self._factories)

    def __contains__(self, name: str) -> bool:
        return name in self._factories

    def __len__(self) -> int:
        return len(self._factories)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry: Optional[EnvironmentRegistry] = None


def get_registry() -> EnvironmentRegistry:
    """Return the global singleton registry (lazy-initialised)."""
    global _registry
    if _registry is None:
        _registry = EnvironmentRegistry()
    return _registry


def reset_registry() -> None:
    """Reset the global singleton (useful for testing)."""
    global _registry
    _registry = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry-point for the environment registry."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="registry",
        description="Oyster Agent Runner – environment plugin registry",
    )
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="List registered environments")
    p_list.add_argument("--dir", default=None, help="Plugin directory to scan")

    p_create = sub.add_parser("create", help="Create an environment instance")
    p_create.add_argument("--name", required=True, help="Environment name")
    p_create.add_argument("--dir", default=None, help="Plugin directory to scan")

    p_disc = sub.add_parser("discover", help="Run discovery and print results")
    p_disc.add_argument("--dir", default=None, help="Plugin directory to scan")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    registry = get_registry()
    plugin_dir = getattr(args, "dir", None)

    if args.command in ("list", "create"):
        registry.discover(plugin_dir)

    if args.command == "list":
        names = registry.list_names()
        for n in names if names else ["(no environments registered)"]:
            print(n)
        return 0

    if args.command == "discover":
        count = registry.discover(plugin_dir)
        print(f"Discovered {count} plugin module(s), {len(registry)} environment(s)")
        return 0

    if args.command == "create":
        try:
            env = registry.create(args.name)
            print(f"Created environment: {env.name}")
            return 0
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
