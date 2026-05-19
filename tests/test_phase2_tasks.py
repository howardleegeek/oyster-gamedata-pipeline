"""Phase 2 task-library validation.

Verifies every JSON file in `tasks/` (six tasks post-Phase-2) loads through
the `AgentTask` pydantic schema, has a sane `max_steps`, and that every
`success_criteria` entry is well-formed (non-empty string; structured
assertion entries are valid JSON dicts with a `type` discriminator).

The CLI strips a known set of extra keys (`world_seed`, `spawn_position`,
`max_minutes`, `thinking_budget_tokens`, `model_required`) before handing
the dict to pydantic — see `cli.run_mc_cmd`. We mirror that behavior here
so test failures point at real schema problems, not at the deliberate
extra-field carve-outs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from oyster_agent_runner.schema import AgentTask

# --- Paths --------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "tasks"

# Task IDs we expect to exist once Phase 2 has landed. Filename equals
# `task_id` plus `.json` — the convention is documented in
# docs/PHASE2_TASK_LIBRARY.md §4.
PHASE2_TASK_IDS: tuple[str, ...] = (
    "MC-tutorial-001",
    "MC-tutorial-002-shelter",
    "MC-tutorial-003-tool-tree",
    "MC-build-001-bridge",
    "MC-mine-001-coal",
    "MC-craft-001-furnace-and-bake",
)

# Extras the CLI knows to pop before validation. Kept in sync with
# `oyster_agent_runner.cli.run_mc_cmd`. Drift in either direction breaks
# the runner, so the test surfaces it loudly.
CLI_POPPED_EXTRAS: frozenset[str] = frozenset(
    {
        "world_seed",
        "spawn_position",
        "max_minutes",
        "thinking_budget_tokens",
        "model_required",
    }
)

MIN_STEPS = 5
MAX_STEPS = 200

# Structured-assertion vocabulary — see PHASE2_TASK_LIBRARY.md §5. Tests
# only check that entries with a `type` key use a known type; deeper schema
# validation will land with the runtime evaluator (Phase 2.5, out of scope).
KNOWN_ASSERTION_TYPES: frozenset[str] = frozenset(
    {
        "inventory_contains",
        "inventory_used",
        "blocks_placed_count",
        "blocks_mined_count",
        "block_placed_in_world",
        "position_delta",
        "position_at_end",
        "world_time",
        "crafting_history_includes",
        "smelting_history_includes",
    }
)


# --- Helpers ------------------------------------------------------------------


def _load_task(task_id: str) -> tuple[AgentTask, dict[str, Any]]:
    """Load a task JSON, mirror the CLI's extras-pop, validate via pydantic.

    Returns (validated AgentTask, raw_dict_after_pop) so individual tests can
    assert on either the schema-typed view or the raw-dict view.
    """
    path = TASKS_DIR / f"{task_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{path} must be a JSON object, got {type(raw).__name__}"
    for key in CLI_POPPED_EXTRAS:
        raw.pop(key, None)
    task = AgentTask.model_validate(raw)
    return task, raw


def _is_structured_assertion(criterion: str) -> bool:
    """Heuristic: structured assertions are JSON objects (start with `{`)."""
    return criterion.lstrip().startswith("{")


def _parse_structured_assertion(criterion: str) -> dict[str, Any]:
    obj = json.loads(criterion)
    assert isinstance(obj, dict), "structured assertion must decode to an object"
    return obj


# --- Tests --------------------------------------------------------------------


@pytest.mark.unit
def test_phase2_task_library_inventory_count() -> None:
    """The library has exactly the six Phase 2 tasks; nothing missing, nothing extra."""
    on_disk = sorted(p.stem for p in TASKS_DIR.glob("*.json"))
    expected = sorted(PHASE2_TASK_IDS)
    assert on_disk == expected, (
        f"task library drift: on_disk={on_disk}, expected={expected}. "
        "Update PHASE2_TASK_IDS and PHASE2_TASK_LIBRARY.md §1 together."
    )


@pytest.mark.unit
@pytest.mark.parametrize("task_id", PHASE2_TASK_IDS)
def test_task_loads_through_schema(task_id: str) -> None:
    """Every task JSON must validate as `AgentTask` after the CLI extras pop."""
    task, _ = _load_task(task_id)
    assert task.task_id == task_id, (
        f"file <{task_id}.json> declares task_id={task.task_id!r}; "
        "filename must equal task_id (PHASE2_TASK_LIBRARY.md §4)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("task_id", PHASE2_TASK_IDS)
def test_task_max_steps_in_sane_range(task_id: str) -> None:
    """`max_steps` must land in [5, 200] — outside that range signals a typo."""
    task, _ = _load_task(task_id)
    assert MIN_STEPS <= task.max_steps <= MAX_STEPS, (
        f"{task_id}: max_steps={task.max_steps} outside the sane range "
        f"[{MIN_STEPS}, {MAX_STEPS}]. Update the JSON or widen this bound "
        "with justification."
    )


@pytest.mark.unit
@pytest.mark.parametrize("task_id", PHASE2_TASK_IDS)
def test_task_has_nonempty_instruction_and_environment(task_id: str) -> None:
    """Defensive: pydantic enforces min_length=1 already, but be explicit."""
    task, _ = _load_task(task_id)
    assert task.natural_language_instruction.strip(), f"{task_id}: empty instruction"
    assert task.environment.strip(), f"{task_id}: empty environment"
    assert task.required_provider_model.strip(), f"{task_id}: empty required_provider_model"


@pytest.mark.unit
@pytest.mark.parametrize("task_id", PHASE2_TASK_IDS)
def test_success_criteria_well_formed(task_id: str) -> None:
    """Each success_criteria entry is a non-empty string. Structured-assertion
    entries (those starting with `{`) must parse as JSON dicts with a known
    `type` discriminator.
    """
    task, _ = _load_task(task_id)
    assert task.success_criteria, f"{task_id}: empty success_criteria"

    for idx, criterion in enumerate(task.success_criteria):
        assert isinstance(criterion, str), f"{task_id}: success_criteria[{idx}] is not a string"
        assert criterion.strip(), f"{task_id}: success_criteria[{idx}] is whitespace-only"
        if _is_structured_assertion(criterion):
            try:
                obj = _parse_structured_assertion(criterion)
            except json.JSONDecodeError as exc:
                pytest.fail(
                    f"{task_id}: success_criteria[{idx}] looks structured "
                    f"(starts with '{{') but fails JSON parse: {exc}"
                )
            assert "type" in obj, (
                f"{task_id}: success_criteria[{idx}] missing required `type` key. "
                f"Got keys: {sorted(obj.keys())}"
            )
            assert obj["type"] in KNOWN_ASSERTION_TYPES, (
                f"{task_id}: success_criteria[{idx}] uses unknown assertion "
                f"type {obj['type']!r}. Known types: "
                f"{sorted(KNOWN_ASSERTION_TYPES)}. Update the test or the "
                "PHASE2_TASK_LIBRARY.md §5 vocabulary together."
            )


@pytest.mark.unit
@pytest.mark.parametrize("task_id", PHASE2_TASK_IDS)
def test_target_hours_present_and_positive(task_id: str) -> None:
    """`target_hours` is optional in the schema but every Phase 2 task sets it
    so cost estimation has a denominator. Catch missing/zero values early.
    """
    task, _ = _load_task(task_id)
    assert task.target_hours is not None, (
        f"{task_id}: target_hours unset. Phase 2 expects every task to "
        "declare a wall-clock target so cost-per-trajectory can be derived."
    )
    assert task.target_hours > 0.0, f"{task_id}: target_hours must be > 0"


@pytest.mark.unit
@pytest.mark.parametrize(
    "task_id",
    [tid for tid in PHASE2_TASK_IDS if tid != "MC-tutorial-001"],
)
def test_phase2_tasks_have_at_least_one_structured_assertion(task_id: str) -> None:
    """Phase 2 tasks (everything except the legacy tutorial) must include at
    least one structured-assertion stub. The legacy `MC-tutorial-001` predates
    the convention and is grandfathered.
    """
    task, _ = _load_task(task_id)
    structured = [c for c in task.success_criteria if _is_structured_assertion(c)]
    assert structured, (
        f"{task_id}: no structured-assertion entries in success_criteria. "
        "Phase 2 tasks must include at least one machine-checkable predicate."
    )


@pytest.mark.unit
def test_filename_matches_task_id_for_all_tasks() -> None:
    """Every JSON's filename stem must equal its declared task_id."""
    for path in TASKS_DIR.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw.get("task_id") == path.stem, (
            f"{path.name}: filename stem != task_id ({raw.get('task_id')!r}). "
            "PHASE2_TASK_LIBRARY.md §4 requires filename === task_id."
        )
