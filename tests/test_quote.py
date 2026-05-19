"""Tests for the `quote` CLI subcommand and the underlying estimator.

The quote module is split into pure helpers (`estimate_per_step_tokens`,
`estimate_trajectory_cost`) and a top-level `build_quote` that loads a
task file. Most coverage targets the helpers (deterministic, no IO);
the CLI tests use Typer's `CliRunner` to exercise the rendered output.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oyster_agent_runner.cli import app
from oyster_agent_runner.pricing import (
    DEFAULT_MARGIN_MULTIPLIER,
    PROVIDER_PRICING,
    get_pricing,
)
from oyster_agent_runner.quote import (
    BASE_INPUT_TOKENS,
    BASE_OUTPUT_TOKENS,
    build_quote,
    estimate_per_step_tokens,
    estimate_trajectory_cost,
    render_json,
    render_text,
)

cli_runner = CliRunner()


# --- Test fixtures -----------------------------------------------------------


def _write_task(tmp_path: Path, payload: dict, name: str = "task.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _phase1_task_payload(instruction: str = "Build a 3x3 wood house.") -> dict:
    """Mirror the shape of `tasks/MC-tutorial-001.json` exactly."""
    return {
        "task_id": "test-task",
        "natural_language_instruction": instruction,
        "success_criteria": ["wall placed"],
        "max_steps": 50,
        "target_hours": 0.1,
        "environment": "minecraft",
        "required_provider_model": "claude-sonnet-4-5",
        "world_seed": 42,
        "spawn_position": None,
        "max_minutes": 5,
        "thinking_budget_tokens": 16000,
        "model_required": "claude-sonnet-4-5",
    }


# --- Pure-function unit tests -----------------------------------------------


def test_estimate_per_step_tokens_default_components() -> None:
    """Per-step tokens decompose into input + thinking + output cleanly."""
    est = estimate_per_step_tokens(instruction="x", thinking_budget=16_000)
    assert est.thinking_tokens == 16_000
    assert est.output_tokens == BASE_OUTPUT_TOKENS
    # Short instruction adds at most ~1 token.
    assert BASE_INPUT_TOKENS <= est.input_tokens <= BASE_INPUT_TOKENS + 2
    assert est.total_tokens == est.input_tokens + est.thinking_tokens + est.output_tokens


def test_estimate_per_step_tokens_long_instruction_inflates_input() -> None:
    """A long instruction should bump per-step input tokens (system prompt
    is replayed every step, so longer instruction → more tokens / step)."""
    long_instr = "x" * 1000  # ~270 tokens at 0.27 tok/char
    est = estimate_per_step_tokens(instruction=long_instr, thinking_budget=0)
    assert est.input_tokens > BASE_INPUT_TOKENS + 200
    assert est.thinking_tokens == 0


def test_estimate_trajectory_cost_zero_steps_is_zero() -> None:
    """Edge: 0 steps → 0 tokens, 0 dollars, but a coherent payload."""
    est = estimate_per_step_tokens(instruction="x", thinking_budget=16_000)
    cost = estimate_trajectory_cost(steps=0, per_step=est, pricing=get_pricing("claude-thinking"))
    assert cost.total_input_tokens == 0
    assert cost.total_output_tokens == 0
    assert cost.cost_low_usd == 0.0
    assert cost.cost_high_usd == 0.0
    assert cost.suggested_unit_price_usd == 0.0


def test_estimate_trajectory_cost_negative_steps_raises() -> None:
    est = estimate_per_step_tokens(instruction="x", thinking_budget=0)
    with pytest.raises(ValueError):
        estimate_trajectory_cost(steps=-1, per_step=est, pricing=get_pricing("claude-thinking"))


def test_estimate_trajectory_cost_math_matches_runbook() -> None:
    """Sanity: at 50 steps with 16K thinking, total cost should hit
    Engineer Y's ~$13 claim in `docs/PHASE1_RUNBOOK.md` (within 10%)."""
    est = estimate_per_step_tokens(instruction="short instruction", thinking_budget=16_000)
    cost = estimate_trajectory_cost(
        steps=50,
        per_step=est,
        pricing=get_pricing("claude-thinking"),
        margin_multiplier=DEFAULT_MARGIN_MULTIPLIER,
    )
    # Runbook predicts ~$13 nominal. Allow ±$2 margin for the
    # observation-token bump (we model 4K input vs runbook's 2K).
    assert 12.0 <= cost.cost_low_usd <= 16.0, cost
    # High = low × 1.25 by construction (allow loose tolerance because
    # both values are rounded to 4 decimals in the dataclass).
    assert math.isclose(cost.cost_high_usd, cost.cost_low_usd * 1.25, rel_tol=1e-3)
    # Suggested unit price = high × 4× margin (within rounding).
    assert math.isclose(
        cost.suggested_unit_price_usd,
        round(cost.cost_high_usd * DEFAULT_MARGIN_MULTIPLIER, 2),
        abs_tol=0.05,
    )


def test_pricing_table_has_required_providers() -> None:
    """The three providers the CLI advertises must all be in the table."""
    for key in ("claude-thinking", "claude-vision", "gpt-thinking-stub"):
        assert key in PROVIDER_PRICING
        record = PROVIDER_PRICING[key]
        assert record.input_price_per_mtok > 0
        assert record.output_price_per_mtok > 0


# --- build_quote (file IO) ---------------------------------------------------


def test_build_quote_strips_phase1_extras(tmp_path: Path) -> None:
    """`tasks/MC-tutorial-001.json` carries fields that `AgentTask` rejects
    under `extra='forbid'`. `build_quote` must strip them like `run-mc` does."""
    path = _write_task(tmp_path, _phase1_task_payload())
    quote = build_quote(
        task_path=path,
        steps=50,
        provider_key="claude-thinking",
    )
    assert quote.task_id == "test-task"
    assert quote.steps == 50
    assert quote.per_step.thinking_tokens == 16_000  # picked up from task JSON


def test_build_quote_explicit_thinking_budget_overrides_task(tmp_path: Path) -> None:
    """`--thinking-budget` flag wins over the task field."""
    path = _write_task(tmp_path, _phase1_task_payload())
    quote = build_quote(
        task_path=path,
        steps=10,
        provider_key="claude-thinking",
        thinking_budget=4_000,
    )
    assert quote.per_step.thinking_tokens == 4_000


def test_build_quote_max_tokens_must_exceed_thinking(tmp_path: Path) -> None:
    path = _write_task(tmp_path, _phase1_task_payload())
    with pytest.raises(ValueError):
        build_quote(
            task_path=path,
            steps=10,
            provider_key="claude-thinking",
            thinking_budget=8_000,
            max_tokens=8_000,
        )


def test_build_quote_unknown_provider_raises(tmp_path: Path) -> None:
    path = _write_task(tmp_path, _phase1_task_payload())
    with pytest.raises(KeyError):
        build_quote(task_path=path, steps=10, provider_key="bogus-provider")


def test_build_quote_zero_steps_marks_low_confidence(tmp_path: Path) -> None:
    path = _write_task(tmp_path, _phase1_task_payload())
    quote = build_quote(task_path=path, steps=0, provider_key="claude-thinking")
    assert quote.confidence == "low"
    assert quote.trajectory.cost_low_usd == 0.0


def test_build_quote_replay_cost_includes_note(tmp_path: Path) -> None:
    path = _write_task(tmp_path, _phase1_task_payload())
    quote = build_quote(
        task_path=path, steps=5, provider_key="claude-thinking", include_replay_cost=True
    )
    assert quote.include_replay_cost is True
    assert quote.replay_cost_usd == 0.0
    assert quote.replay_note is not None
    assert "no LLM call" in quote.replay_note  # honest about why it's $0


# --- Rendering ---------------------------------------------------------------


def test_render_text_contains_required_lines(tmp_path: Path) -> None:
    path = _write_task(tmp_path, _phase1_task_payload())
    quote = build_quote(task_path=path, steps=50, provider_key="claude-thinking")
    rendered = render_text(quote)
    assert "Task:" in rendered
    assert "Steps: 50" in rendered
    assert "Per-step token estimate" in rendered
    assert "Suggested unit price" in rendered
    assert "Confidence:" in rendered


def test_render_json_round_trips(tmp_path: Path) -> None:
    path = _write_task(tmp_path, _phase1_task_payload())
    quote = build_quote(task_path=path, steps=50, provider_key="claude-thinking")
    payload = json.loads(render_json(quote))
    assert payload["task_id"] == "test-task"
    assert payload["steps"] == 50
    assert payload["per_step"]["thinking_tokens"] == 16_000
    assert payload["trajectory"]["cost_low_usd"] > 0


# --- CLI smoke tests ---------------------------------------------------------


def test_cli_quote_default_args(tmp_path: Path) -> None:
    """Default invocation: `quote --task <path> --steps 50` succeeds."""
    path = _write_task(tmp_path, _phase1_task_payload())
    result = cli_runner.invoke(app, ["quote", "--task", str(path), "--steps", "50"])
    assert result.exit_code == 0, result.output
    assert "Per-step token estimate" in result.output
    assert "Sonnet 4.5" in result.output


def test_cli_quote_json_output(tmp_path: Path) -> None:
    """`--json` flag emits machine-readable JSON."""
    path = _write_task(tmp_path, _phase1_task_payload())
    result = cli_runner.invoke(
        app,
        ["quote", "--task", str(path), "--steps", "50", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["steps"] == 50
    assert payload["provider_key"] == "claude-thinking"


def test_cli_quote_missing_task_file_exits_nonzero(tmp_path: Path) -> None:
    result = cli_runner.invoke(
        app, ["quote", "--task", str(tmp_path / "missing.json"), "--steps", "10"]
    )
    assert result.exit_code != 0


def test_cli_quote_unknown_provider_exits_one(tmp_path: Path) -> None:
    path = _write_task(tmp_path, _phase1_task_payload())
    result = cli_runner.invoke(
        app,
        ["quote", "--task", str(path), "--steps", "10", "--provider", "bogus"],
    )
    assert result.exit_code == 1
    assert "unknown provider" in result.output


def test_cli_quote_zero_steps_succeeds_with_low_confidence(tmp_path: Path) -> None:
    """Edge case: --steps 0 should not crash (returns a coherent $0 quote)."""
    path = _write_task(tmp_path, _phase1_task_payload())
    result = cli_runner.invoke(app, ["quote", "--task", str(path), "--steps", "0", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["steps"] == 0
    assert payload["trajectory"]["cost_low_usd"] == 0.0
    assert payload["confidence"] == "low"


def test_cli_quote_include_replay_cost_appends_line(tmp_path: Path) -> None:
    path = _write_task(tmp_path, _phase1_task_payload())
    result = cli_runner.invoke(
        app,
        [
            "quote",
            "--task",
            str(path),
            "--steps",
            "10",
            "--include-replay-cost",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Replay --re-execute" in result.output


def test_cli_quote_matches_runbook_for_real_task() -> None:
    """End-to-end: quote for the actual `tasks/MC-tutorial-001.json` at 50
    steps + Sonnet thinking should match the runbook's ~$13 claim."""
    repo_root = Path(__file__).resolve().parent.parent
    real_task = repo_root / "tasks" / "MC-tutorial-001.json"
    assert real_task.exists(), real_task
    result = cli_runner.invoke(app, ["quote", "--task", str(real_task), "--steps", "50", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    cost_low = payload["trajectory"]["cost_low_usd"]
    # Runbook says ~$13. Our heuristic predicts $13.20 ± rounding.
    assert 12.0 <= cost_low <= 16.0, payload
