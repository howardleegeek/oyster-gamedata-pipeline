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


def _make_environment(env_key: str) -> Environment:
    """Resolve an env key to a concrete Environment.

    Keys:
      mock               deterministic fake (always available)
      minecraft          STUB — raises NotImplementedError on reset
      factorio           STUB — raises NotImplementedError on reset
      gym:<env_id>       STUB — raises NotImplementedError on reset
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
      mock      canned responses (always available)
      claude    Anthropic (requires ANTHROPIC_API_KEY)
      openai    OpenAI    (requires OPENAI_API_KEY)
    """
    if provider_key == "mock":
        return MockLLMProvider()
    if provider_key == "claude":
        from oyster_agent_runner.providers.claude import ClaudeProvider

        return ClaudeProvider(model=model)
    if provider_key == "openai":
        from oyster_agent_runner.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(model=model)
    raise typer.BadParameter(f"Unknown provider: {provider_key!r}")


@app.command("run")
def run_cmd(
    env: Annotated[str, typer.Option(help="Environment key: mock, minecraft, factorio, gym:<id>")],
    task: Annotated[str, typer.Option("--task", help="Natural-language task instruction")],
    provider: Annotated[
        str, typer.Option(help="LLM provider: mock, claude, or openai")
    ] = "mock",
    model: Annotated[
        str, typer.Option(help="Model id for the provider")
    ] = "claude-sonnet-4-5",
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
    table = Table(title="Result", show_header=False, border_style="green" if result.success else "red")
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


if __name__ == "__main__":
    app()
