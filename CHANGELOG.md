# Changelog

## [Unreleased]

### Initial scaffold (Layer 4 agent runner)
- Pydantic v2 schema with `extra="forbid"` — `AgentTask`, `TrajectoryEntry`,
  `TaskResult`, `TrajectoryEvent`
- `AgentRunner` orchestrator with state machine: INIT → RESET → (OBSERVE →
  REASON → ACT → LOG)* → TERMINATED
- `Environment` protocol + `MockEnvironment` (hand-rolled tiny-PNG emitter,
  no Pillow dep in core)
- `LLMProvider` protocol + Claude (`anthropic`) + OpenAI + Mock providers
  with retry/backoff on 429/5xx
- `TrajectoryLogger` emits JSONL in `{timestamp, event_type, event_args}`
  envelope — byte-identical to `gamedata-recorder/inputs.jsonl` so
  `oyster-enrichment` ingests agent trajectories without schema changes
- Typer CLI: `oyster-agent run | schema`
- Stub environments: `minecraft.py` (MineRL / Mineflayer TODO),
  `factorio.py` (RCON TODO), `gym_env.py` (gymnasium.make TODO)
- Red-line games list in README: ONLY Minecraft / Factorio / Skyrim SE /
  SP GTA V / Civ VI / gym / MineRL / Procgen. Activision / Riot / Epic
  explicitly forbidden with $14.5M precedent cited.
- 16 tests passing (schema + runner-mocked + trajectory-logger-compat)
