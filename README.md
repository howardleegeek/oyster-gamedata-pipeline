# oyster-agent-runner — Oyster Labs Layer 4

**LLM-agent-driven gameplay data generation for world-model training.**

This is the structural moat. Chinese network-cafe commodity farms can deliver
passive human gameplay at scale. They **cannot** deliver task-labeled agent
trajectories, because building this pipeline requires:

- LLM API budget (geoblocked + expensive in China)
- Agent orchestration infrastructure
- Task-labeling + scenario-coverage planning
- Engineering throughput beyond the real-human-hours ceiling

When Cosmos / Genie / World Labs ask for *"20 hours of AI agents executing task
X in Minecraft at night in rain"*, the commodity supply literally cannot
fulfill the order. That's the premium tier.

---

## Position in the stack

| Layer | Scope                                  | Repo                                 |
|-------|----------------------------------------|--------------------------------------|
| L1    | Depth + pose enrichment pipeline       | `oyster-enrichment` (shipped)        |
| L2    | VR / OpenXR capture backend            | `gamedata-recorder` (committed)      |
| L3    | Cyberpunk depth-hook scaffold          | `gamedata-recorder` (committed)      |
| **L4**| **Agent-driven task-directed capture** | **this repo**                        |

L4 emits trajectories in a JSONL shape that ingests straight into L1 — same
`{timestamp, event_type, event_args}` envelope as `gamedata-recorder`'s
`inputs.jsonl`. Zero downstream schema changes.

---

## Red-line games list (HARD CONSTRAINT)

This runner targets ONLY:

| Environment           | Rationale                                               |
|-----------------------|---------------------------------------------------------|
| Minecraft             | Official modding API (Forge/Fabric). **Voyager precedent** (NVIDIA, 2023). |
| Factorio              | Official modding API + RCON; Wube endorses research use. |
| Skyrim SE             | SKSE modding, single-player, offline.                   |
| Single-player GTA V   | ScriptHookV, **offline only**. NEVER GTA Online.        |
| Civilization VI       | Official modding.                                       |
| OpenAI Gym / MineRL / Procgen | Open research environments.                     |

**NEVER touch:**

- Activision titles (CoD et al.) — **$14.5M precedent** (Activision v. EngineOwning).
- Riot Games titles (League, Valorant) — aggressive enforcement, Vanguard anti-cheat.
- Epic titles (Fortnite) — TOS forbids automation.
- Any live commercial online game.

The legal exposure of violating this list is catastrophic. This red line is
enforced at the environment-adapter level: adding a new adapter requires a
legal signoff that the game permits scripted agents under its EULA.

---

## Precedent: Voyager (NVIDIA, 2023)

[Voyager](https://arxiv.org/abs/2305.16291) proved an LLM agent can drive
Minecraft open-ended exploration and skill acquisition. It's the reference
implementation for this approach and lives in the Minecraft modding + MineRL
gym ecosystem. L4 generalizes the pattern across the red-line list above.

---

## Quick start

```bash
# Install dev deps
pip install -e '.[dev]'

# Run the scaffold smoke test — mock env + mock LLM, no API keys, no game
oyster-agent run --env mock --task "do ten noops" --provider mock --max-steps 20

# Inspect schema
oyster-agent schema
```

With Anthropic credentials (the recommended provider):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
oyster-agent run \
  --env mock \
  --task "reach the door at the north wall" \
  --provider claude \
  --model claude-sonnet-4-5 \
  --max-steps 200
```

---

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌──────────────────────┐
│   AgentTask     │    │   LLMProvider   │    │     Environment      │
│  (task spec)    │    │  (chat(...))    │    │ (reset/step/render)  │
└────────┬────────┘    └────────┬────────┘    └──────────┬───────────┘
         │                      │                        │
         └──────────────┬───────┴────────────────────────┘
                        ▼
                ┌──────────────────┐
                │  AgentRunner     │  ← single orchestrator
                │  (loop + parse)  │
                └────────┬─────────┘
                         │ TrajectoryEntry
                         ▼
                ┌──────────────────┐       ┌──────────────────┐
                │TrajectoryLogger  │──▶    │ trajectory.jsonl │  ← enrichment-compatible
                │                  │       │ frames/*.png     │
                └──────────────────┘       └──────────────────┘
```

- **`AgentRunner`** owns the loop: `reset → observe → reason → act → log`,
  terminating on env-signaled `done`, `max_steps`, or uncaught error.
- **`Environment`** is a protocol — pluggable. Minecraft / Factorio / gym
  are sub-modules that adapt external simulators; `MockEnvironment` lets
  CI drive the runner without any game installed.
- **`LLMProvider`** is a protocol — `ClaudeProvider`, `OpenAIProvider`,
  and `MockLLMProvider` are built-in. Adding a provider is < 80 LOC.
- **`TrajectoryLogger`** writes JSONL with `{timestamp, event_type,
  event_args}` — the exact shape `gamedata-recorder/check_input_log.py`
  validates, so the enrichment pipeline ingests agent trajectories
  without a schema bump.

---

## Trajectory format

Each line of `trajectory.jsonl` is one event:

```jsonl
{"timestamp": 0.0,   "event_type": "START",         "event_args": {"task_id": "...", "environment": "minecraft", "provider_model": "claude-sonnet-4-5"}}
{"timestamp": 0.12,  "event_type": "AGENT_STEP",    "event_args": {"step": 0, "success": false}}
{"timestamp": 0.12,  "event_type": "OBSERVATION",   "event_args": {"value": {"x": 0.0, "y": 64.0, "z": 0.0}}}
{"timestamp": 0.12,  "event_type": "LLM_REASONING", "event_args": {"text": "I should punch a tree..."}}
{"timestamp": 0.12,  "event_type": "ACTION",        "event_args": {"op": "attack", "target": "oak_log"}}
{"timestamp": 0.12,  "event_type": "REWARD",        "event_args": {"value": 0.0}}
{"timestamp": 0.12,  "event_type": "RENDER",        "event_args": {"path": "frames/000000.png", "sha256": "...", "bytes": 1024}}
...
{"timestamp": 0.0,   "event_type": "END",           "event_args": {"success": true, "total_steps": 42, "reason": "success"}}
```

Event types additive to the recorder's enum
(`MOUSE_MOVE`, `MOUSE_BUTTON`, `KEYBOARD`, `START`, `END`, `VIDEO_START`,
`VIDEO_END`):

| Event type      | Emitted per | Payload                                           |
|-----------------|-------------|---------------------------------------------------|
| `AGENT_STEP`    | step        | `{step: int, success: bool}`                      |
| `OBSERVATION`   | step        | `{value: str \| dict}`                            |
| `LLM_REASONING` | step        | `{text: str}`                                     |
| `ACTION`        | step        | env-specific JSON dict                            |
| `REWARD`        | step (opt)  | `{value: float}`                                  |
| `RENDER`        | step (opt)  | `{path: str, sha256: str, bytes: int}`            |

---

## Integration with `oyster-enrichment`

The enrichment pipeline's input log parser (`check_input_log.py`) requires
every JSONL line to be a dict with `timestamp` (float), `event_type` (str),
and `event_args` (any). L4 trajectories pass that validator unchanged.

To attach depth + pose to agent-generated frames:

```bash
# L4 writes: runs/<run_id>/trajectory.jsonl + runs/<run_id>/frames/*.png
# L1 ingests:
cd /path/to/oyster-enrichment
python -m oyster_enrichment.cli enrich \
  --frames-dir /path/to/runs/<run_id>/frames \
  --output /path/to/runs/<run_id>/enriched
```

The enriched output lines up with the original trajectory by `step` index,
giving downstream consumers `{observation, reasoning, action, reward,
depth, pose}` per step — exactly the premium-tier "task-directed enriched"
record that commodity farms can't produce.

---

## Status

| Component              | Status        |
|------------------------|---------------|
| Schema / runner / logger | **Done** — covered by tests |
| `MockEnvironment` / `MockLLMProvider` | **Done** |
| `ClaudeProvider` / `OpenAIProvider` | **Done** (requires API keys) |
| `MinecraftEnvironment` | Scaffold stub — needs MineRL / Mineflayer wire-up on a Windows/Linux box |
| `FactorioEnvironment`  | Scaffold stub — needs RCON client + mod           |
| `GymEnvironment`       | Scaffold stub — drop-in `gymnasium.make` wrapper  |

Real environment integrations are follow-up work that needs Windows or Linux
machines and game licenses — **out of scope for this scaffold**.

---

## Testing

```bash
pytest                          # run all
pytest -m unit                  # fast tests only
pytest --cov=src/oyster_agent_runner --cov-report=term-missing
```

Three acceptance tests must always pass:

1. `test_schema_roundtrip` — Pydantic models survive JSON round-trip.
2. `test_runner_with_mock_env_completes_10_steps` — full loop runs 10 steps
   against `MockEnvironment` + `MockLLMProvider` and writes a well-formed
   trajectory.
3. `test_trajectory_logger_compatible_with_enrichment_schema` — every line
   the logger emits passes the recorder's input-log validator byte-for-byte.

---

## License

Proprietary. Oyster Labs internal use.
