# Phase 2 — Task Library Foundation

**Status:** Phase 2 (foundation landed; runtime evaluator outstanding).
**Date:** 2026-04-28
**Owner:** Howard Li (`howard@oysterlabs.ai`)
**Source spec:** [`MINECRAFT_TRAJECTORY_SPEC.md`](MINECRAFT_TRAJECTORY_SPEC.md) §4.

Phase 1 shipped a single tutorial (`MC-tutorial-001`). Phase 2 promotes the
pitch from *"one toy tutorial"* to *"a library of N tasks with measured
difficulty + cost"*. This document is the inventory + contributor guide.

---

## 1. Library inventory

Six tasks ship in `tasks/` after Phase 2 lands. The "Target success rate"
column is **HONEST: TBD** for every new task — we calibrate after the first
~10 unattended runs per task. Until then we publish "TBD — needs first 10
runs to calibrate."

| task_id                          | Description                                                                                  | Est. steps | Est. cost @ Sonnet thinking (16K budget)                | Primary skill tested                  | Target success rate |
|----------------------------------|----------------------------------------------------------------------------------------------|-----------:|---------------------------------------------------------|---------------------------------------|---------------------|
| `MC-tutorial-001`                | Spawn, walk to a tree, break exactly one log block. Survive.                                 |        ~50 | ~$0.50–$1.50 / run                                      | Locomotion + targeted dig             | TBD — needs first 10 runs to calibrate |
| `MC-tutorial-002-shelter`        | Gather wood, craft 16 planks, build a 1-block-thick enclosed shelter before nightfall.       |        ~30 | ~$0.30–$1.00 / run                                      | Resource→craft→place utility loop     | TBD — needs first 10 runs to calibrate |
| `MC-tutorial-003-tool-tree`      | Wood pickaxe → stone pickaxe → iron pickaxe progression (mine + craft + smelt).              |        ~80 | ~$1.50–$4.00 / run                                      | Goal-directed multi-step crafting     | TBD — needs first 10 runs to calibrate |
| `MC-build-001-bridge`            | Bridge a 10-block gap with placed blocks; cross to the far side without falling.             |        ~25 | ~$0.20–$0.80 / run                                      | Spatial precision + block placement   | TBD — needs first 10 runs to calibrate |
| `MC-mine-001-coal`               | Find and collect 5 coal ore via surface + cave exploration; return to surface alive.         |        ~40 | ~$0.50–$1.50 / run                                      | Exploration + targeted mining         | TBD — needs first 10 runs to calibrate |
| `MC-craft-001-furnace-and-bake`  | Craft furnace, place it, smelt 1 raw_iron into iron_ingot using coal as fuel.                |        ~60 | ~$1.00–$3.00 / run                                      | Multi-step crafting + smelting chain  | TBD — needs first 10 runs to calibrate |

**Total post-Phase 2 task count:** 6.

Cost estimates above assume Claude Sonnet 4.5 with a 16 K thinking budget,
~50 messages/min, and the per-step token usage observed in Phase 1 dry runs
(~1.5 K input, ~3.5 K output, ~6 K thinking per step). Real costs will be
re-published in `docs/PHASE2_CALIBRATION_REPORT.md` once 10 runs per task
are logged.

---

## 2. Difficulty ladder

The library is intentionally laddered so the same model–prompt stack can be
benchmarked across increasing reasoning load:

```
tutorial          →  utility           →  goal-directed         →  exploratory       →  multi-step
MC-tutorial-001      MC-build-001-bridge   MC-tutorial-003-tool-tree   MC-mine-001-coal     MC-craft-001-furnace-and-bake
MC-tutorial-002-shelter
                                                                                          (Multi-step = full chain:
                                                                                           gather → craft → place →
                                                                                           smelt → end-state)
```

| Tier              | What it tests                                                  | Steps    | Tasks |
|-------------------|----------------------------------------------------------------|----------|-------|
| **Tutorial**      | Single-skill smoke test. Pipeline + first action verified.     | ≤50      | `MC-tutorial-001`, `MC-tutorial-002-shelter` |
| **Utility**       | One concrete sub-skill in isolation (place / pathfind).        | ≤30      | `MC-build-001-bridge` |
| **Goal-directed** | Multi-resource gather → craft progression with a hard target.  | ≤80      | `MC-tutorial-003-tool-tree` |
| **Exploratory**   | Open-ended search over the world; no hand-fed coordinates.     | ≤40      | `MC-mine-001-coal` |
| **Multi-step**    | Chain of 4+ heterogeneous sub-tasks with state across steps.   | ≤60      | `MC-craft-001-furnace-and-bake` |

Each rung surfaces a different failure mode of LLM-driven play (locomotion,
spatial reasoning, planning depth, exploration, working memory across
tool-use turns), giving buyers a richer signal than any one rung in
isolation.

---

## 3. Adding a new task

A 5-step contributor guide. The runner does not yet auto-discover tasks —
adding a file is enough; the CLI takes `--task-file` directly.

1. **Pick a `task_id`** following [§4 Task ID convention](#4-task-id-convention).
   Reserve the slug in this doc's inventory table (§1) before opening a PR
   — keeps numbering monotonic per category.
2. **Author a JSON file** in `tasks/` that conforms to the `AgentTask`
   pydantic schema (`src/oyster_agent_runner/schema.py`). Required fields:
   `task_id`, `natural_language_instruction`, `success_criteria` (list of
   strings — see §5 for structured-assertion encoding), `max_steps`,
   `target_hours`, `environment`, `required_provider_model`. The CLI
   additionally pops these well-known extras: `world_seed`, `spawn_position`,
   `max_minutes`, `thinking_budget_tokens`, `model_required`.
3. **Cover it with a test** in `tests/test_phase2_tasks.py`. Bare minimum:
   the JSON loads, the ID matches the filename, `max_steps` lands in the
   sane range (5–200), every `success_criteria` entry is well-formed.
4. **Run `pytest tests/test_phase2_tasks.py -v`** and confirm green before
   pushing. Black + ruff must also be clean (`make check` once the Phase 2
   Makefile target exists; `black --check . && ruff check .` until then).
5. **Update §1 in this doc** with the task row (description, est. steps,
   est. cost, primary skill, target success rate = `TBD — needs first 10
   runs to calibrate`). Honesty over wishful thinking.

After ~10 unattended runs land, replace the `TBD` cells with measured
numbers and append a one-paragraph "lessons learned" note to
`docs/PHASE2_CALIBRATION_REPORT.md`.

---

## 4. Task ID convention

```
MC-{category}-{NNN}-{slug}.json
```

| Token        | Definition                                                                          | Example                  |
|--------------|-------------------------------------------------------------------------------------|--------------------------|
| `MC-`        | Fixed prefix. `MC` = Minecraft. Other engines pick their own (`FA-` for Factorio). | `MC-`                    |
| `{category}` | One of `tutorial`, `build`, `mine`, `craft`, `combat`, `explore`, `trade`. Lowercase. | `craft`                  |
| `{NNN}`      | Three-digit zero-padded ordinal within the category. Monotonic; never reused.       | `001`                    |
| `{slug}`     | `kebab-case` short description, ≤4 words, ASCII only.                               | `furnace-and-bake`       |

**Full example:** `MC-craft-001-furnace-and-bake.json` →
`task_id = "MC-craft-001-furnace-and-bake"`.

The legacy tutorial (`MC-tutorial-001.json`) predates the slug requirement;
new tutorial tasks include the slug (`MC-tutorial-002-shelter`,
`MC-tutorial-003-tool-tree`).

**Filename === `task_id`** (minus the `.json` extension). The Phase 2 test
suite enforces this so the on-disk discoverability matches the manifest.

---

## 5. `success_criteria` format and the runtime evaluator gap

The pydantic schema fixes `success_criteria: list[str]`. Phase 2 tasks
encode two flavors of entries inside that list:

1. **Human-readable strings** — for buyer-facing manifests and pitch
   collateral. Example: `"inventory contains >= 5 coal"`.
2. **Structured assertion JSON-strings** — machine-checkable predicates
   serialized as JSON, identifiable by a leading `{`. Example:
   `'{"type": "inventory_contains", "item": "coal", "min_count": 5}'`.

We mirror both flavors in every Phase 2 task so the tests can verify the
JSON-encoded ones parse as dicts with a `type` key without losing the
human-readable narration.

**Structured assertion vocabulary** (current Phase 2 stubs):

| `type`                        | Required keys                             | Meaning                                                              |
|-------------------------------|-------------------------------------------|----------------------------------------------------------------------|
| `inventory_contains`          | `item`, `min_count`                       | Bot's inventory has ≥ N of item at end-of-run                        |
| `inventory_used`              | `item`, `min_count`                       | Bot held ≥ N of item at any point during the run                     |
| `blocks_placed_count`         | `item`, `min_count` (opt: `max_count`)    | Number of blocks of this type the bot placed                         |
| `blocks_mined_count`          | `block`, `min_count`                      | Number of blocks of this type the bot mined                          |
| `block_placed_in_world`       | `block`, `radius_from_bot`                | A block of this type exists in the world within radius of bot        |
| `position_delta`              | `axis`, `min_distance`                    | Bot moved ≥ N units along axis (`horizontal`/`vertical`)             |
| `position_at_end`             | `axis`, `min` (opt: `max`)                | Bot's final coordinate on axis is in range                            |
| `world_time`                  | `phase`                                   | World time at run end matches phase (`day_or_dusk_at_completion`)    |
| `crafting_history_includes`   | `recipes` (list[str])                     | Bot crafted each named recipe at least once during the run           |
| `smelting_history_includes`   | `item`, `min_count`                       | Bot smelted ≥ N of item via furnace during the run                   |

### Runtime evaluator: required, out of scope here

These structured assertions are **stubs** — the strings are well-formed JSON
with a `type` discriminator, but **no module evaluates them yet**. Phase 2
explicitly defers the evaluator implementation. A follow-up spec will land
as `src/oyster_agent_runner/assertion_eval.py` with this shape:

```python
def evaluate(
    assertion: dict[str, Any],
    *,
    final_metadata: dict[str, Any],     # final tick from metadata.jsonl
    history: HistoryView,                # rolling view over metadata.jsonl + inputs.jsonl
) -> AssertionResult:
    """Return pass/fail + human-readable explanation per structured assertion."""
```

`HistoryView` aggregates per-tick events into the shapes the assertion types
need (`inventory_used` walks every `INVENTORY` event, `blocks_placed_count`
walks `inputs.jsonl` for `place_block` ops, etc).

Until that lands, success/failure is decided post-hoc by an operator
inspecting the trajectory dir against the human-readable bullets in
`success_criteria`. That's acceptable for the pilot bundle to Decart but
not for unattended scale; getting the evaluator out is the top Phase 2.5
priority.

---

## 6. Cost & throughput planning

For the post-Phase-2 "20-hour pilot bundle" target:

- 6 task types × ~7 trajectories each = ~40 trajectories
- Average ~$1.50 / run @ Sonnet thinking → ~**$60 LLM cost** for the pilot
- Wall-clock at 8 parallel generators on the 16 GB Mac → ~3 hours

That keeps the §5.2 economics in `MINECRAFT_TRAJECTORY_SPEC.md` intact:
~93–98% gross margin at the $450/hr price point.

---

## 7. Open questions for next iteration

1. **Calibration cadence:** how often do we re-run the 10-trial calibration
   per task once Sonnet revisions ship? Probably at every minor model bump.
2. **Categories beyond `tutorial`/`build`/`mine`/`craft`:** combat, explore,
   trade, villager — slot in next batch of 5.
3. **Cross-task metadata in the bundle manifest:** tag each trajectory
   bundle with its `task_id` + difficulty tier + measured success so buyers
   can slice by skill tested.
4. **Failure-mode taxonomy:** when a run fails, the manifest currently logs
   `termination_reason` as `max_steps` / `error` / `timeout`. Phase 2.5
   should add a `failure_category` (`navigation`, `crafting_dead_end`,
   `mob_death`, …) so the buyer can see *why* the agent failed, not just
   *that* it did. Tied to the assertion evaluator above.
