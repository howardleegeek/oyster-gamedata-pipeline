"""Sales-grade quote generator for an `AgentTask` trajectory.

Given a Phase 1 task definition + step count + provider, project the
token + dollar cost of running the trajectory and the suggested unit
price (default 4× cost margin). Output is human-readable by default and
machine-readable JSON via `--json` so the sales op can pipe it into a
spreadsheet.

The estimator is *heuristic but principled*:

  1. Per-step input tokens come from a fixed prompt-overhead constant
     (`BASE_INPUT_TOKENS`) plus any task-specific hint we can derive
     from the natural-language instruction (length × `TOKENS_PER_CHAR`).
  2. Per-step output tokens come from a fixed action-token estimate
     (`BASE_OUTPUT_TOKENS`).
  3. Per-step thinking tokens come from `--thinking-budget` (or 0 for
     non-thinking providers).
  4. Trajectory cost = steps × per_step_input × input_price + steps ×
     (per_step_output + per_step_thinking) × output_price.

The heuristic is calibrated against Engineer Y's $13 / 50-step claim in
`docs/PHASE1_RUNBOOK.md` — see `tests/test_quote.py::test_matches_runbook`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from oyster_agent_runner.pricing import (
    DEFAULT_MARGIN_MULTIPLIER,
    TOKENS_PER_CHAR,
    ProviderPricing,
    get_pricing,
)
from oyster_agent_runner.schema import AgentTask

# Per-step token heuristics. These match the spec output template so the
# default quote prints exactly the per-step numbers the sales sheet
# advertises:
#
#   Input (system + history + obs): ~4,000
#   Output (action + reply):        ~800
#   Thinking:                       16,000   (from --thinking-budget)
#   Total per-step:                 ~20,800
#
# Why these specific numbers:
#   - 4,000 input ≈ 1.5K system prompt + 1.5K rolling action history +
#     1K observation block. Empirically matches the runbook's "~2,000
#     input tokens" calibration plus an extra ~2K for the observation
#     formatting we add in the runner.
#   - 800 output ≈ short action block ("dig the_oak_log_at_(3,64,2)\n")
#     plus a brief commentary block. Matches the runbook estimate
#     within rounding.
BASE_INPUT_TOKENS: int = 4_000
BASE_OUTPUT_TOKENS: int = 800

# Module logger — used to surface non-fatal fall-throughs in this file
# (e.g. task JSON read failures) at DEBUG so production diagnostics can
# distinguish "thinking_budget = 0 because the provider doesn't support
# thinking" from "thinking_budget = 0 because the task file is busted".
_logger = logging.getLogger(__name__)

# Replay (--re-execute) re-issues the *recorded* actions against a fresh
# environment to detect drift — no fresh LLM call per step, so the
# Anthropic / OpenAI cost component is genuinely $0. We keep the
# include-replay-cost flag as a quote line so the sales sheet is
# explicit about that (rather than silently omitting it). When/if
# replay grows an "LLM re-execute" mode, recalibrate this number.
REPLAY_NOT_YET_CALIBRATED_NOTE: str = (
    "replay --re-execute re-uses recorded actions (no LLM call); "
    "API cost = $0. Compute / env cost not yet calibrated."
)


@dataclass(frozen=True)
class TokenEstimate:
    """Token breakdown for a single step.

    Frozen so the quote payload is safely shareable. `total` is derived
    but we materialize it so JSON consumers don't have to recompute.
    """

    input_tokens: int
    thinking_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class TrajectoryCost:
    """Dollar cost for the full trajectory at one provider's pricing."""

    total_input_tokens: int
    total_output_tokens: int  # output + thinking, billed at output rate
    total_tokens: int
    cost_low_usd: float
    cost_high_usd: float
    suggested_unit_price_usd: float


@dataclass(frozen=True)
class Quote:
    """Top-level quote payload returned by `build_quote(...)`.

    Fields are all primitives / nested dataclasses so `asdict(...)` gives
    a JSON-serializable dict directly.
    """

    task_path: str
    task_id: str
    provider_key: str
    model_label: str
    steps: int
    per_step: TokenEstimate
    trajectory: TrajectoryCost
    pricing_input_per_mtok: float
    pricing_output_per_mtok: float
    margin_multiplier: float
    confidence: str
    confidence_reason: str
    include_replay_cost: bool
    replay_cost_usd: float
    replay_note: str | None


# --- Estimation primitives ---------------------------------------------------


def estimate_per_step_tokens(
    *,
    instruction: str,
    thinking_budget: int,
    base_input_tokens: int = BASE_INPUT_TOKENS,
    base_output_tokens: int = BASE_OUTPUT_TOKENS,
) -> TokenEstimate:
    """Project per-step token usage.

    The instruction itself only enters the prompt once (not per step), so
    we don't multiply its length by step count — we add it as a one-time
    delta to `base_input_tokens` to nudge the per-step input estimate
    when the task description is unusually long. This is principled:
    the instruction sits in the system prompt, which is replayed each
    step, so longer instructions DO inflate every step's input tokens.
    """
    instruction_tokens = max(0, int(round(len(instruction) * TOKENS_PER_CHAR)))
    input_tokens = base_input_tokens + instruction_tokens
    output_tokens = base_output_tokens
    total = input_tokens + max(0, thinking_budget) + output_tokens
    return TokenEstimate(
        input_tokens=input_tokens,
        thinking_tokens=max(0, thinking_budget),
        output_tokens=output_tokens,
        total_tokens=total,
    )


def estimate_trajectory_cost(
    *,
    steps: int,
    per_step: TokenEstimate,
    pricing: ProviderPricing,
    margin_multiplier: float = DEFAULT_MARGIN_MULTIPLIER,
) -> TrajectoryCost:
    """Project the total token + dollar cost for `steps` agent steps.

    Cost low = nominal estimate. Cost high = nominal × 1.25 to bracket
    the variance from longer-than-expected observations / actions /
    thinking. The 25% bracket matches what we've seen empirically across
    short Phase 1 runs — anything outside that is a methodology miss
    worth investigating, not a normal-case variance.
    """
    if steps < 0:
        raise ValueError(f"steps must be >= 0, got {steps}")
    total_input = per_step.input_tokens * steps
    # Thinking is billed at the *output* rate per Anthropic's pricing
    # docs — collapse thinking + output into one bucket so the math is
    # transparent on the quote sheet.
    total_output_inclusive = (per_step.output_tokens + per_step.thinking_tokens) * steps
    total_tokens = total_input + total_output_inclusive

    cost_low = (total_input / 1_000_000) * pricing.input_price_per_mtok + (
        total_output_inclusive / 1_000_000
    ) * pricing.output_price_per_mtok
    cost_high = cost_low * 1.25
    suggested_unit_price = cost_high * margin_multiplier

    return TrajectoryCost(
        total_input_tokens=total_input,
        total_output_tokens=total_output_inclusive,
        total_tokens=total_tokens,
        cost_low_usd=round(cost_low, 4),
        cost_high_usd=round(cost_high, 4),
        suggested_unit_price_usd=round(suggested_unit_price, 2),
    )


# --- Top-level builder -------------------------------------------------------


def load_task(task_path: Path) -> AgentTask:
    """Load + validate an AgentTask JSON file.

    Tolerates the Phase-1 `run-mc` extras (`world_seed`, `spawn_position`,
    `max_minutes`, `thinking_budget_tokens`, `model_required`) by
    stripping them before pydantic validation — same trick `run-mc` uses
    in `cli.py`. Without this, `tasks/MC-tutorial-001.json` fails
    `extra='forbid'` validation and the operator can't quote the task
    they actually run.
    """
    raw = task_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    for k in (
        "world_seed",
        "spawn_position",
        "max_minutes",
        "thinking_budget_tokens",
        "model_required",
    ):
        data.pop(k, None)
    return AgentTask.model_validate(data)


def derive_thinking_budget(
    task_path: Path,
    *,
    explicit_budget: int | None,
    pricing: ProviderPricing,
) -> int:
    """Pick the thinking budget for the quote.

    Order of preference:
      1. `--thinking-budget` flag, if the user passed one.
      2. `thinking_budget_tokens` field in the task JSON (if present).
      3. 16,000 if the provider supports thinking (the spec default).
      4. 0 otherwise.

    Returning 0 for non-thinking providers keeps the math correct
    without requiring `quote.py` callers to know which providers
    support thinking.
    """
    if explicit_budget is not None:
        return max(0, explicit_budget)
    try:
        raw = json.loads(task_path.read_text(encoding="utf-8"))
        from_task = raw.get("thinking_budget_tokens")
        if isinstance(from_task, int) and from_task >= 0:
            return from_task
    except (OSError, json.JSONDecodeError) as exc:
        # Fall through; load_task() will raise the canonical error if
        # the file is unreadable. Log at DEBUG so operators can
        # distinguish "default because provider doesn't support
        # thinking" from "default because task JSON couldn't be read".
        _logger.debug(
            "could not read thinking_budget_tokens from %s (%s: %s); "
            "falling back to default",
            task_path,
            type(exc).__name__,
            exc,
        )
    return 16_000 if pricing.supports_thinking else 0


def build_quote(
    *,
    task_path: Path,
    steps: int,
    provider_key: str,
    thinking_budget: int | None = None,
    max_tokens: int | None = None,
    margin_multiplier: float = DEFAULT_MARGIN_MULTIPLIER,
    include_replay_cost: bool = False,
) -> Quote:
    """Build a full Quote object for the given task + provider + step count.

    `max_tokens` is accepted for parity with the provider class but only
    used for sanity validation: if a caller passes max_tokens <=
    thinking_budget the quote is meaningless because the API would
    reject the call.
    """
    if steps < 0:
        raise ValueError(f"steps must be >= 0, got {steps}")
    pricing = get_pricing(provider_key)
    task = load_task(task_path)
    resolved_budget = derive_thinking_budget(
        task_path, explicit_budget=thinking_budget, pricing=pricing
    )
    if max_tokens is not None and max_tokens <= resolved_budget:
        raise ValueError(
            f"max_tokens ({max_tokens}) must exceed thinking budget ({resolved_budget})"
        )

    per_step = estimate_per_step_tokens(
        instruction=task.natural_language_instruction,
        thinking_budget=resolved_budget,
    )
    trajectory = estimate_trajectory_cost(
        steps=steps,
        per_step=per_step,
        pricing=pricing,
        margin_multiplier=margin_multiplier,
    )

    # Confidence narrative. We only ever say "high" when the run has
    # already been calibrated against real trajectories — that hasn't
    # happened on Phase 1 yet, so "medium" is the honest top end.
    confidence = "medium"
    confidence_reason = "heuristic; calibrate with first 5 real runs"
    if steps == 0:
        confidence = "low"
        confidence_reason = "zero-step quote: pricing math is 0; sanity-check input"

    replay_cost = 0.0
    replay_note: str | None = None
    if include_replay_cost:
        replay_note = REPLAY_NOT_YET_CALIBRATED_NOTE

    return Quote(
        task_path=str(task_path),
        task_id=task.task_id,
        provider_key=provider_key,
        model_label=pricing.model_label,
        steps=steps,
        per_step=per_step,
        trajectory=trajectory,
        pricing_input_per_mtok=pricing.input_price_per_mtok,
        pricing_output_per_mtok=pricing.output_price_per_mtok,
        margin_multiplier=margin_multiplier,
        confidence=confidence,
        confidence_reason=confidence_reason,
        include_replay_cost=include_replay_cost,
        replay_cost_usd=replay_cost,
        replay_note=replay_note,
    )


# --- Rendering ---------------------------------------------------------------


def render_text(quote: Quote) -> str:
    """Render a Quote as the human-readable sales sheet block.

    The format matches the spec's example output verbatim so the sales
    op can paste it into Notion / email without reformatting.
    """
    p = quote.per_step
    t = quote.trajectory
    lines = [
        f"Task: {quote.task_path}",
        f"Provider: {quote.provider_key} ({quote.model_label})",
        f"Steps: {quote.steps}",
        "Per-step token estimate:",
        f"  Input (system + history + obs): ~{p.input_tokens:,}",
        f"  Thinking budget: {p.thinking_tokens:,}",
        f"  Output (action + reply): ~{p.output_tokens:,}",
        f"  Total per-step: ~{p.total_tokens:,}",
        f"Trajectory cost ({quote.steps} steps):",
        f"  Tokens: ~{t.total_tokens:,}",
        (
            f"  Anthropic API @ {quote.model_label} "
            f"(${quote.pricing_input_per_mtok:.0f} in / "
            f"${quote.pricing_output_per_mtok:.0f} out): "
            f"~${t.cost_low_usd:.2f} low ~${t.cost_high_usd:.2f} high"
        ),
        f"Suggested unit price ({quote.margin_multiplier:g}× margin): "
        f"${t.suggested_unit_price_usd:.2f}",
        f"Confidence: {quote.confidence} ({quote.confidence_reason})",
    ]
    if quote.include_replay_cost:
        lines.append(f"Replay --re-execute: ${quote.replay_cost_usd:.2f} ({quote.replay_note})")
    return "\n".join(lines)


def render_json(quote: Quote) -> str:
    """Render a Quote as pretty-printed JSON."""
    payload: dict[str, Any] = asdict(quote)
    return json.dumps(payload, indent=2)


__all__ = [
    "BASE_INPUT_TOKENS",
    "BASE_OUTPUT_TOKENS",
    "Quote",
    "REPLAY_NOT_YET_CALIBRATED_NOTE",
    "TokenEstimate",
    "TrajectoryCost",
    "build_quote",
    "derive_thinking_budget",
    "estimate_per_step_tokens",
    "estimate_trajectory_cost",
    "load_task",
    "render_json",
    "render_text",
]
