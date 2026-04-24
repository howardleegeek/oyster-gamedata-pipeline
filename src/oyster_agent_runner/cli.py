"""Typer-based CLI — `oyster-agent run --env ... --task "..."`.

The real environment integrations (MineRL, Factorio RCON, gym) are not
yet wired up; invoking them today raises `NotImplementedError`. The
`mock` environment is always available and drives the runner through
the MockLLMProvider — useful for smoke-testing the install.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from oyster_agent_runner.environments.base import Environment, MockEnvironment
from oyster_agent_runner.environments.factorio import FactorioEnvironment
from oyster_agent_runner.environments.gym_env import GymEnvironment
from oyster_agent_runner.environments.minecraft import MinecraftEnvironment
from oyster_agent_runner.providers.base import LLMProvider, MockLLMProvider
from oyster_agent_runner.runner import AgentRunner, RunnerConfig
from oyster_agent_runner.schema import AgentTask

app = typer.Typer(
    name="oyster-agent",
    help="Oyster Labs Layer 4 — agent-driven gameplay data generation.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


# --- Registries --------------------------------------------------------------
#
# `ENV_REGISTRY` / `PROVIDER_REGISTRY` are the single source of truth for
# the `run`, `list-envs`, and `list-providers` commands. Each entry is a
# `{key, description, status}` row so listing is a one-liner.

ENV_REGISTRY: list[dict[str, str]] = [
    {
        "key": "mock",
        "description": "Deterministic fake for tests and smoke-checks",
        "status": "ready",
    },
    {
        "key": "minecraft",
        "description": "MineRL (pixel) or Mineflayer (headless) — see module docs",
        "status": "stub",
    },
    {
        "key": "factorio",
        "description": "Factorio RCON; accepts rcon://[pw@]host[:port] URI",
        "status": "stub (RCON parsing live)",
    },
    {
        "key": "gym:<env_id>",
        "description": "gymnasium.make(env_id); works if gymnasium is installed",
        "status": "real-if-installed",
    },
]

PROVIDER_REGISTRY: list[dict[str, str]] = [
    {
        "key": "mock",
        "description": "Canned reasoning + action — deterministic, always available",
        "status": "ready",
    },
    {
        "key": "claude",
        "description": "Anthropic Claude (text only)",
        "status": "needs ANTHROPIC_API_KEY",
    },
    {
        "key": "openai",
        "description": "OpenAI Chat Completions (text only)",
        "status": "needs OPENAI_API_KEY",
    },
    {
        "key": "claude-vision",
        "description": "Anthropic Claude + PNG frame as image content block",
        "status": "needs ANTHROPIC_API_KEY",
    },
    {
        "key": "openai-vision",
        "description": "OpenAI + data:URI image_url content block",
        "status": "needs OPENAI_API_KEY",
    },
]


def _make_environment(env_key: str) -> Environment:
    """Resolve an env key to a concrete Environment.

    Keys:
      mock               deterministic fake (always available)
      minecraft          STUB — raises NotImplementedError on reset
      factorio           STUB — raises NotImplementedError on reset
      gym:<env_id>       real wrapper if gymnasium is installed, stub otherwise
    """
    if env_key == "mock":
        return MockEnvironment()
    if env_key == "minecraft":
        return MinecraftEnvironment()
    if env_key == "factorio":
        return FactorioEnvironment()
    if env_key.startswith("gym:"):
        return GymEnvironment(env_id=env_key.split(":", 1)[1])
    raise typer.BadParameter(f"Unknown environment: {env_key!r}")


def _make_provider(provider_key: str, model: str) -> LLMProvider:
    """Resolve a provider key to a concrete LLMProvider.

    Keys:
      mock             canned responses (always available)
      claude           Anthropic           (ANTHROPIC_API_KEY)
      openai           OpenAI              (OPENAI_API_KEY)
      claude-vision    Anthropic + images  (ANTHROPIC_API_KEY)
      openai-vision    OpenAI + images     (OPENAI_API_KEY)
    """
    if provider_key == "mock":
        return MockLLMProvider()
    if provider_key == "claude":
        from oyster_agent_runner.providers.claude import ClaudeProvider

        return ClaudeProvider(model=model)
    if provider_key == "openai":
        from oyster_agent_runner.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(model=model)
    if provider_key == "claude-vision":
        from oyster_agent_runner.providers.claude_vision import ClaudeVisionProvider

        return ClaudeVisionProvider(model=model)
    if provider_key == "openai-vision":
        from oyster_agent_runner.providers.openai_vision import OpenAIVisionProvider

        return OpenAIVisionProvider(model=model)
    raise typer.BadParameter(f"Unknown provider: {provider_key!r}")


@app.command("run")
def run_cmd(
    env: Annotated[str, typer.Option(help="Environment key: mock, minecraft, factorio, gym:<id>")],
    task: Annotated[str, typer.Option("--task", help="Natural-language task instruction")],
    provider: Annotated[
        str,
        typer.Option(help="LLM provider: mock, claude, openai, claude-vision, openai-vision"),
    ] = "mock",
    model: Annotated[str, typer.Option(help="Model id for the provider")] = "claude-sonnet-4-5",
    max_steps: Annotated[int, typer.Option(help="Hard cap on agent steps")] = 100,
    hours: Annotated[
        float | None,
        typer.Option("--hours", help="Wall-clock cap in hours (advisory)"),
    ] = None,
    output_dir: Annotated[
        Path, typer.Option(help="Directory to write trajectory + frames into")
    ] = Path("runs"),
    task_id: Annotated[
        str | None, typer.Option(help="Explicit task id (default: random uuid4)")
    ] = None,
    success_criteria: Annotated[
        list[str] | None,
        typer.Option(
            "--criterion",
            help="Append one success criterion; repeat to add more.",
        ),
    ] = None,
) -> None:
    """Run the agent loop end-to-end and print a summary."""
    resolved_id = task_id if task_id is not None else f"run-{uuid.uuid4().hex[:8]}"
    run_output_dir = output_dir / resolved_id

    agent_task = AgentTask(
        task_id=resolved_id,
        natural_language_instruction=task,
        success_criteria=list(success_criteria or []),
        max_steps=max_steps,
        target_hours=hours,
        environment=env,
        required_provider_model=model,
    )

    console.print(
        Panel.fit(
            f"[bold]L4 agent run[/bold]  id=[cyan]{agent_task.task_id}[/cyan]\n"
            f"env=[green]{env}[/green]  provider=[green]{provider}[/green]  "
            f"model=[green]{model}[/green]\n"
            f"max_steps={max_steps}  hours={hours}\n"
            f"output_dir={run_output_dir.resolve()}",
            title="oyster-agent",
            border_style="blue",
        )
    )

    environment = _make_environment(env)
    llm_provider = _make_provider(provider, model)
    runner = AgentRunner(
        RunnerConfig(
            write_frames=True,
            wall_clock_cap_sec=(hours * 3600.0) if hours is not None else None,
        )
    )

    result = runner.run(agent_task, environment, llm_provider, run_output_dir)

    # Render result summary.
    table = Table(
        title="Result", show_header=False, border_style="green" if result.success else "red"
    )
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("success", str(result.success))
    table.add_row("termination_reason", result.termination_reason)
    table.add_row("total_steps", str(result.total_steps))
    table.add_row("wall_clock_sec", f"{result.wall_clock_sec:.2f}")
    table.add_row("final_reward", str(result.final_reward))
    if result.error_message:
        table.add_row("error", result.error_message)
    table.add_row("trajectory", result.trajectory_path)
    console.print(table)


@app.command("schema")
def schema_cmd() -> None:
    """Print the JSON schemas for AgentTask / TrajectoryEntry / TaskResult."""
    from oyster_agent_runner.schema import AgentTask, TaskResult, TrajectoryEntry

    schemas = {
        "AgentTask": AgentTask.model_json_schema(),
        "TrajectoryEntry": TrajectoryEntry.model_json_schema(),
        "TaskResult": TaskResult.model_json_schema(),
    }
    console.print_json(json.dumps(schemas))


@app.command("list-envs")
def list_envs_cmd(
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON instead of a table")
    ] = False,
) -> None:
    """Print the registered environments available to `oyster-agent run`."""
    if as_json:
        console.print_json(json.dumps(ENV_REGISTRY))
        return
    table = Table(title="Environments", border_style="blue")
    table.add_column("key", style="bold cyan")
    table.add_column("status", style="magenta")
    table.add_column("description")
    for row in ENV_REGISTRY:
        table.add_row(row["key"], row["status"], row["description"])
    console.print(table)


@app.command("list-providers")
def list_providers_cmd(
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON instead of a table")
    ] = False,
) -> None:
    """Print the registered LLM providers available to `oyster-agent run`."""
    if as_json:
        console.print_json(json.dumps(PROVIDER_REGISTRY))
        return
    table = Table(title="Providers", border_style="blue")
    table.add_column("key", style="bold cyan")
    table.add_column("status", style="magenta")
    table.add_column("description")
    for row in PROVIDER_REGISTRY:
        table.add_row(row["key"], row["status"], row["description"])
    console.print(table)


@app.command("validate-task")
def validate_task_cmd(
    task_path: Annotated[Path, typer.Argument(help="Path to a JSON AgentTask file.")],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit the validated task as JSON on success."),
    ] = False,
) -> None:
    """Validate an `AgentTask` JSON file against the schema.

    Exits 0 and prints the parsed task (pretty-rendered or JSON) on
    success. Exits 1 with a human-readable Pydantic error report on
    failure.
    """
    from pydantic import ValidationError

    from oyster_agent_runner.schema import AgentTask

    try:
        raw = task_path.read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]cannot read file: {exc}[/red]")
        raise typer.Exit(code=2) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"[red]invalid JSON:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        task = AgentTask.model_validate(data)
    except ValidationError as exc:
        console.print("[red]validation failed[/red]")
        # Pydantic's JSON error report is already structured — print it
        # as JSON for machine consumption + human readability.
        console.print_json(exc.json(indent=2))
        raise typer.Exit(code=1) from exc

    if as_json:
        console.print_json(task.model_dump_json())
        return

    table = Table(title="AgentTask (valid)", border_style="green")
    table.add_column("field", style="bold")
    table.add_column("value")
    for key, value in task.model_dump().items():
        table.add_row(key, str(value))
    console.print(table)


if __name__ == "__main__":
    app()
