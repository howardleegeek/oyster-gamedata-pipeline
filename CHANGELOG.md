# Changelog

## [Unreleased]

### Vision-capable LLM providers
- `ClaudeVisionProvider` — Anthropic SDK wrapper that injects PNG frames
  as `image`-type content blocks on every user turn
- `OpenAIVisionProvider` — OpenAI SDK wrapper that injects frames as
  `image_url` blocks using a `data:image/png;base64,...` URI
- Both providers declare `wants_vision = True` and expose a
  single-use `set_next_frame(bytes)` setter; the runner feature-detects
  both the provider and the env and threads the current frame through
  on each step (zero change for text-only providers)
- CLI gains `claude-vision` / `openai-vision` provider keys
- 10 new tests for vision providers (includes runner-level wiring)

### Environment primitives + runner fail-safe
- `VisionCapableEnvironment` protocol + `has_vision(env)` helper; envs
  may expose `last_frame()` returning the most recent PNG bytes
- `MockEnvironment` now caches and returns its last rendered frame, and
  clears it on `reset()`
- `GymEnvironment` conditional implementation — delegates to real
  `gymnasium` when the package is importable, stubs cleanly otherwise
  (exposed via `is_stub` property); PNG encoding via Pillow if present
- `FactorioEnvironment` accepts an `rcon_uri` (`rcon://[pw@]host[:port]`);
  shipped as a standalone `RconConnection` dataclass + documented Lua
  mod + action-dispatch contract
- `MinecraftEnvironment` documents MineRL (pixel / research) vs
  Mineflayer (symbolic / headless) tradeoff and stores a `path` attr so
  downstream wrappers branch cleanly
- `RunnerConfig.max_consecutive_errors` fail-safe: soft-skip individual
  step failures, abort the run only after N consecutive errors
  (default 5, `None` disables); prevents runaway token burn on persistent
  env/provider outages
- 22 new tests (env adapters + fail-safe semantics)

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
