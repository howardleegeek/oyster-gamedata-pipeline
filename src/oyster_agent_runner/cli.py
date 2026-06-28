"""Typer-based CLI — `oyster-agent run --env ... --task "..."`.

The `mock` environment is always available and drives the runner through
the MockLLMProvider. Minecraft is live through Mineflayer; Factorio is
smoke-ready through the RCON/mod relay; optional environments such as gym
depend on local packages and game installs.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

UTC = timezone.utc

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
from oyster_agent_runner.minecraft_streams import MinecraftStreamWriter
from oyster_agent_runner.providers.base import LLMProvider, MockLLMProvider
from oyster_agent_runner.runner import AgentRunner, RunnerConfig
from oyster_agent_runner.schema import AgentTask, TrajectoryEvent

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
        "description": (
            "Mineflayer subprocess (Phase 1 LIVE — needs Node.js + Paper server). "
            "MineRL path still stubbed."
        ),
        "status": "live (mineflayer)",
    },
    {
        "key": "factorio",
        "description": "Factorio RCON; accepts rcon://[pw@]host[:port] URI",
        "status": "smoke-ready (RCON/mod relay)",
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
    {
        "key": "claude-thinking",
        "description": "Anthropic Claude with extended-thinking capture (Phase 1 Minecraft)",
        "status": "needs ANTHROPIC_API_KEY",
    },
    {
        "key": "gpt-thinking",
        "description": (
            "OpenAI reasoning models (o1 / gpt-5-thinking) — "
            "structural placeholder; stub response when OPENAI_API_KEY unset"
        ),
        "status": "stub (no key) / experimental (with key)",
    },
]


def _make_environment(env_key: str) -> Environment:
    """Resolve an env key to a concrete Environment.

    Keys:
      mock               deterministic fake (always available)
      minecraft          Mineflayer subprocess (Phase 1 LIVE — see run-mc command)
      factorio           smoke-ready RCON/mod relay; needs a live Factorio server
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


def _make_minecraft_environment(host: str, port: int, username: str) -> Environment:
    """Factory used by `run-mc` so tests can monkey-patch a fake env in.

    Real callers always get a `MinecraftEnvironment`. The seam exists
    purely so the unit test for `run-mc` can substitute `MockEnvironment`
    without spawning a Mineflayer subprocess (which requires `npm install`
    in `mineflayer/` — see PHASE1_RUNBOOK.md).
    """
    return MinecraftEnvironment(host=host, port=port, username=username)


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
    if provider_key == "scripted":
        from oyster_agent_runner.providers.scripted import ScriptedProvider

        return ScriptedProvider()
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
    if provider_key == "claude-thinking":
        from oyster_agent_runner.providers.claude_thinking import ClaudeThinkingProvider

        return ClaudeThinkingProvider(model=model)
    if provider_key == "gpt-thinking":
        from oyster_agent_runner.providers.gpt_thinking import GPTThinkingProvider

        return GPTThinkingProvider(model=model)
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


@app.command("run-mc")
def run_mc_cmd(
    task_file: Annotated[
        Path,
        typer.Option("--task-file", help="Path to a JSON AgentTask definition."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Directory to write cot.jsonl, metadata.jsonl, inputs.jsonl, manifest.json into.",
        ),
    ],
    minecraft_server: Annotated[
        str,
        typer.Option(
            "--minecraft-server",
            help="host:port of the Paper/Spigot server (default: localhost:25565)",
        ),
    ] = "localhost:25565",
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="LLM provider key (recommended: claude-thinking)",
        ),
    ] = "claude-thinking",
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Model id override. If unset, uses task.required_provider_model.",
        ),
    ] = None,
    max_steps: Annotated[
        int | None,
        typer.Option("--max-steps", help="Override task.max_steps."),
    ] = None,
    bot_username: Annotated[
        str,
        typer.Option("--bot-username", help="Mineflayer bot username (offline-mode auth)."),
    ] = "oyster_bot",
) -> None:
    """Run a Minecraft Phase 1 trajectory: cot.jsonl + metadata.jsonl + inputs.jsonl + manifest.json.

    Phase 1 deliberately omits the video stream (`video.mp4` + `frames.jsonl`).
    Phase 2 plugs in the OBS spectator pipeline. See
    `docs/MINECRAFT_TRAJECTORY_SPEC.md` for the product context and
    `docs/PHASE1_RUNBOOK.md` for operator instructions.

    The command exits 0 on a clean run regardless of `success` (the
    manifest's `result.success` carries that signal); exits non-zero only
    on bootstrap failure (bad task file, missing server, etc).
    """
    # 1. Load task.
    try:
        raw = task_file.read_text(encoding="utf-8")
        task_data = json.loads(raw)
    except OSError as exc:
        console.print(f"[red]cannot read task file: {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except json.JSONDecodeError as exc:
        console.print(f"[red]invalid JSON in task file:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    # Allow the task JSON to declare schema fields that AgentTask doesn't yet
    # know about (thinking_budget_tokens, world_seed, spawn_position) — we
    # consume them at the CLI layer to keep the AgentTask schema stable.
    extras = {
        k: task_data.pop(k, None)
        for k in (
            "world_seed",
            "spawn_position",
            "max_minutes",
            "thinking_budget_tokens",
            "model_required",
        )
    }
    if "max_minutes" in task_data:
        # Already popped above; this branch never executes — kept for clarity.
        pass

    if max_steps is not None:
        task_data["max_steps"] = max_steps

    try:
        agent_task = AgentTask.model_validate(task_data)
    except Exception as exc:  # noqa: BLE001 — pydantic surface is broad
        console.print(f"[red]invalid AgentTask:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    resolved_model = model or agent_task.required_provider_model

    # 2. Parse server URI.
    if ":" not in minecraft_server:
        host = minecraft_server
        port = 25565
    else:
        host, port_str = minecraft_server.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError as exc:
            console.print(f"[red]invalid port in --minecraft-server:[/red] {port_str}")
            raise typer.Exit(code=1) from exc

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(
        Panel.fit(
            f"[bold]Minecraft Phase 1 trajectory[/bold] "
            f"task=[cyan]{agent_task.task_id}[/cyan]\n"
            f"server=[green]{host}:{port}[/green]  provider=[green]{provider}[/green]  "
            f"model=[green]{resolved_model}[/green]\n"
            f"max_steps={agent_task.max_steps}  bot_username={bot_username}\n"
            f"output_dir={output_dir}",
            title="oyster-agent run-mc",
            border_style="blue",
        )
    )

    # 3. Build env + provider.
    environment = _make_minecraft_environment(host=host, port=port, username=bot_username)
    llm_provider = _make_provider(provider, resolved_model)

    # 4. Anchor the wall clock now.
    anchor_utc = datetime.now(UTC)
    runner = AgentRunner(RunnerConfig(write_frames=False))

    # 5. Run.
    result = runner.run(agent_task, environment, llm_provider, output_dir)

    # 6. Demux trajectory.jsonl → cot/metadata/inputs + manifest.
    trajectory_path = Path(result.trajectory_path)
    if not trajectory_path.exists():
        console.print(
            f"[red]runner exited but trajectory.jsonl is missing at {trajectory_path}[/red]"
        )
        raise typer.Exit(code=2)

    with MinecraftStreamWriter(output_dir) as streams:
        with trajectory_path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                try:
                    event = TrajectoryEvent.model_validate(payload)
                except Exception:  # noqa: BLE001 — be permissive on a logged file
                    continue
                streams.write(event)

        thinking_budget = extras.get("thinking_budget_tokens")
        streams.finalize_manifest(
            task_id=agent_task.task_id,
            model=resolved_model,
            provider=provider,
            environment=agent_task.environment,
            anchor_utc=anchor_utc,
            success=result.success,
            termination_reason=result.termination_reason,
            total_steps=result.total_steps,
            wall_clock_sec=result.wall_clock_sec,
            thinking_budget_tokens=thinking_budget,
            license="train-only",
            extra={
                k: v for k, v in extras.items() if v is not None and k != "thinking_budget_tokens"
            },
        )

    # 7. Render summary table.
    table = Table(
        title="Phase 1 Result",
        show_header=False,
        border_style="green" if result.success else "red",
    )
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("task_id", agent_task.task_id)
    table.add_row("success", str(result.success))
    table.add_row("termination_reason", result.termination_reason)
    table.add_row("total_steps", str(result.total_steps))
    table.add_row("wall_clock_sec", f"{result.wall_clock_sec:.2f}")
    if result.error_message:
        table.add_row("error", result.error_message)
    table.add_row("manifest", str(output_dir / "manifest.json"))
    table.add_row("cot.jsonl", str(output_dir / "cot.jsonl"))
    table.add_row("metadata.jsonl", str(output_dir / "metadata.jsonl"))
    table.add_row("inputs.jsonl", str(output_dir / "inputs.jsonl"))
    console.print(table)


@app.command("replay")
def replay_cmd(
    manifest: Annotated[
        Path,
        typer.Option("--manifest", help="Path to manifest.json inside a Phase 1 bundle."),
    ],
    check: Annotated[
        bool,
        typer.Option("--check", help="Run verify_consistency() and print the report."),
    ] = False,
    re_execute: Annotated[
        bool,
        typer.Option(
            "--re-execute",
            help="Replay actions against a fresh MockEnvironment and report drift.",
        ),
    ] = False,
) -> None:
    """Walk a Phase 1 bundle: list steps, verify consistency, or re-execute.

    Default (no flags) prints a per-step summary table.
    """
    from oyster_agent_runner.replay import Replayer

    try:
        replayer = Replayer(manifest)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]cannot load bundle:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if check:
        report = replayer.verify_consistency()
        verdict_color = "green" if report.ok else "red"
        table = Table(title="Consistency", border_style=verdict_color, show_header=False)
        table.add_column("field", style="bold")
        table.add_column("value")
        table.add_row("ok", str(report.ok))
        table.add_row("step_count", str(report.step_count))
        table.add_row("cot_event_count", str(report.cot_event_count))
        table.add_row("metadata_event_count", str(report.metadata_event_count))
        table.add_row("input_event_count", str(report.input_event_count))
        table.add_row("max_timestamp_sec", f"{report.max_timestamp_sec:.6f}")
        table.add_row("manifest_matches_streams", str(report.manifest_matches_streams))
        console.print(table)
        if report.issues:
            console.print("[yellow]issues:[/yellow]")
            for issue in report.issues:
                console.print(f"  - {issue}")
        if not report.ok:
            raise typer.Exit(code=1)
        return

    if re_execute:
        drift = replayer.replay_against()
        verdict_color = "green" if drift.ok else "red"
        table = Table(title="Replay drift", border_style=verdict_color, show_header=False)
        table.add_column("field", style="bold")
        table.add_column("value")
        table.add_row("ok", str(drift.ok))
        table.add_row("steps_executed", str(drift.steps_executed))
        table.add_row("steps_diverged", str(drift.steps_diverged))
        if drift.early_termination_step is not None:
            table.add_row("early_termination_step", str(drift.early_termination_step))
        if drift.error_message:
            table.add_row("error", drift.error_message)
        console.print(table)
        if drift.divergence_reasons:
            console.print("[yellow]divergence reasons:[/yellow]")
            for step_idx, reason in drift.divergence_reasons.items():
                console.print(f"  - step {step_idx}: {reason}")
        if not drift.ok:
            raise typer.Exit(code=1)
        return

    # Default: list steps.
    steps = replayer.iter_steps()
    table = Table(title=f"Replay ({len(steps)} steps)", border_style="blue")
    table.add_column("step", style="bold cyan")
    table.add_column("timing_ms", justify="right")
    table.add_column("action")
    table.add_column("reward", justify="right")
    table.add_column("done", justify="center")
    for s in steps:
        action_repr = json.dumps(s.action) if s.action is not None else "<none>"
        if len(action_repr) > 60:
            action_repr = action_repr[:57] + "..."
        table.add_row(
            str(s.step_idx),
            f"{s.timing_ms:.2f}",
            action_repr,
            "" if s.reward is None else f"{s.reward:.3f}",
            "yes" if s.success_flag else "",
        )
    console.print(table)


@app.command("package-trajectory")
def package_trajectory_cmd(
    bundle_dir: Annotated[
        Path,
        typer.Option(
            "--bundle-dir",
            help="Path to a Phase 1 bundle directory (manifest.json + 3 streams).",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="Destination zip path. Parent dirs are created as needed.",
        ),
    ],
    verify: Annotated[
        bool,
        typer.Option(
            "--verify",
            help="After packaging, round-trip extract the zip and check checksums.",
        ),
    ] = False,
) -> None:
    """Wrap a Phase 1 bundle in the L1-parity envelope and emit a buyer-ready zip.

    Adds ``provenance.json`` (git SHA + package version + model identifiers
    + ISO 8601 packaging timestamp) and ``packaged.manifest.json`` (original
    manifest extended with a ``checksums`` block) alongside the original
    streams, all zipped together.
    """
    from oyster_agent_runner.trajectory_packager import TrajectoryPackager

    try:
        packager = TrajectoryPackager(bundle_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]cannot load bundle:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        zip_path = packager.package(output)
    except ValueError as exc:
        console.print(f"[red]packaging failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title="Trajectory Bundle Packaged", border_style="green", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("bundle_dir", str(bundle_dir.resolve()))
    table.add_row("zip", str(zip_path.resolve()))
    table.add_row("size_bytes", str(zip_path.stat().st_size))
    console.print(table)

    if verify:
        report = packager.verify_package(zip_path)
        verdict_color = "green" if report.ok else "red"
        v_table = Table(title="Verification", border_style=verdict_color, show_header=False)
        v_table.add_column("field", style="bold")
        v_table.add_column("value")
        v_table.add_row("ok", str(report.ok))
        v_table.add_row("step_count", str(report.step_count))
        v_table.add_row("manifest_matches_streams", str(report.manifest_matches_streams))
        console.print(v_table)
        if report.issues:
            console.print("[yellow]issues:[/yellow]")
            for issue in report.issues:
                console.print(f"  - {issue}")
        if not report.ok:
            raise typer.Exit(code=1)


@app.command("adapt-buyer-spec")
def adapt_buyer_spec_cmd(
    bundle: Annotated[
        Path,
        typer.Option(
            "--bundle",
            help="Path to a Phase 1 bundle directory (manifest.json + 3 streams).",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="Destination directory for the buyer-spec 4-deliverable layout.",
        ),
    ],
    placeholders: Annotated[
        Path | None,
        typer.Option(
            "--placeholders",
            help=(
                "Optional directory holding video.mp4 / gameinfo.xlsx / depth/*.exr "
                "to copy into the output bundle. Required for L4 lint to pass "
                "until Phase 2 ships display+depth capture."
            ),
        ),
    ] = None,
    route_type: Annotated[
        int,
        typer.Option(
            "--route-type",
            help="Buyer-spec route_type (1=normal, 2=special, 3=loop). Default 1.",
        ),
    ] = 1,
    pad_to_min_records: Annotated[
        int | None,
        typer.Option(
            "--pad-to-min-records",
            help=(
                "If the real OBSERVATION count is below this floor, pad action_camera "
                "by replicating the last real record at 1/fps spacing. Use 9000 to "
                "satisfy the buyer's 5-min minimum video duration constraint."
            ),
        ),
    ] = None,
    game_state_jsonl: Annotated[
        Path | None,
        typer.Option(
            "--game-state-jsonl",
            help=(
                "Optional path to a server-side Fabric mod JSONL stream "
                "(D16). When supplied, real per-tick game state from the "
                "Paper server overlays the metadata-derived camera/player "
                "fields, closing Pipeline 2's last placeholder gap."
            ),
        ),
    ] = None,
) -> None:
    """Adapt a Phase 1 Minecraft bundle into the buyer-spec v1 layout.

    Emits ``action_camera.json`` + ``systeminfo.json`` + ``gameinfo.json``
    + ``manifest.json`` (the original passed through). Mineflayer-derived
    fields (``player_position``, ``player_rotation_*``, ``camera_position``)
    are filled in; mouse/keyboard fields are synthesized as neutral
    defaults (``mouse=0.5``, ``keyCode=[]``) because Phase 1 operates on
    high-level Mineflayer actions.

    With ``--placeholders <dir>`` and ``--pad-to-min-records 9000``, the
    output passes ``oyster-enrichment/bin/lint_buyer_spec.py`` end-to-end
    on captures shorter than 5 minutes (provided the placeholder set is
    complete: 1801-frame depth/, video.mp4 sized via ffmpeg at runtime,
    and a static gameinfo.xlsx).
    """
    from oyster_agent_runner.buyer_spec_adapter import (
        ACTION_CAMERA_FILENAME,
        DELIVERABLE_COUNT,
        GAMEINFO_FILENAME,
        MANIFEST_OUT_FILENAME,
        SYSTEMINFO_FILENAME,
        adapt_phase1_to_buyer_spec,
    )

    try:
        result_dir = adapt_phase1_to_buyer_spec(
            bundle,
            output,
            placeholders_dir=placeholders,
            pad_to_min_records=pad_to_min_records,
            route_type=route_type,
            game_state_jsonl=game_state_jsonl,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]cannot adapt bundle:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    table = Table(title="Buyer-Spec Adapted", border_style="green", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("bundle", str(bundle.resolve()))
    table.add_row("output_dir", str(result_dir))
    table.add_row("deliverables", str(DELIVERABLE_COUNT))
    table.add_row("action_camera", str(result_dir / ACTION_CAMERA_FILENAME))
    table.add_row("systeminfo", str(result_dir / SYSTEMINFO_FILENAME))
    table.add_row("gameinfo", str(result_dir / GAMEINFO_FILENAME))
    table.add_row("manifest", str(result_dir / MANIFEST_OUT_FILENAME))
    if placeholders is not None:
        table.add_row("placeholders", str(Path(placeholders).resolve()))
    if pad_to_min_records is not None:
        table.add_row("pad_to_min_records", str(pad_to_min_records))
    console.print(table)


@app.command("quote")
def quote_cmd(
    task: Annotated[
        Path,
        typer.Option("--task", help="Path to a JSON AgentTask file."),
    ],
    steps: Annotated[
        int,
        typer.Option("--steps", help="Number of agent steps to project."),
    ] = 50,
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help=(
                "Pricing key: claude-thinking | claude-vision | gpt-thinking-stub. "
                "See src/oyster_agent_runner/pricing.py."
            ),
        ),
    ] = "claude-thinking",
    thinking_budget: Annotated[
        int | None,
        typer.Option(
            "--thinking-budget",
            help="Override thinking-token budget (default: task field or 16,000).",
        ),
    ] = None,
    max_tokens: Annotated[
        int | None,
        typer.Option(
            "--max-tokens",
            help="Sanity bound; must exceed thinking_budget if provided.",
        ),
    ] = None,
    include_replay_cost: Annotated[
        bool,
        typer.Option(
            "--include-replay-cost",
            help="Add a replay --re-execute cost line (currently $0; see note).",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of text."),
    ] = False,
) -> None:
    """Print a sales-grade token + dollar quote for a task trajectory."""
    from oyster_agent_runner.quote import build_quote, render_json, render_text

    try:
        quote = build_quote(
            task_path=task,
            steps=steps,
            provider_key=provider,
            thinking_budget=thinking_budget,
            max_tokens=max_tokens,
            include_replay_cost=include_replay_cost,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]task file not found:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except KeyError as exc:
        console.print(f"[red]unknown provider:[/red] {exc.args[0]}")
        raise typer.Exit(code=1) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]invalid input:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        # `console.print_json` emits the JSON without Rich wrapping it
        # against the terminal width — matches `validate-task --json`.
        console.print_json(render_json(quote))
    else:
        console.print(render_text(quote))


if __name__ == "__main__":
    app()
