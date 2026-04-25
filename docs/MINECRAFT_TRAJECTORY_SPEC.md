# Product Spec — Minecraft AI Agent Trajectory Data

**Status:** Specification (no code yet — scaffold builds against this).
**Date:** 2026-04-25
**Owner:** Howard Li (`howard@oysterlabs.ai`)
**Source brief (from Howard):** Online 海外模型 (Claude / GPT thinking) 玩 Minecraft 的轨迹
数据 — collect environment-interaction + Action full trajectories for AI-game
decision/execution training.

This is the **concrete L4 product** referenced in the moat-stack PRD —
replaces the previous generic "agent runs in mod-friendly games" framing
with a tightly-scoped saleable bundle.

---

## 1. Product definition

A **trajectory bundle** = one continuous Minecraft session where a
foreign-API LLM (Claude with `thinking` mode, or GPT-5 with reasoning
mode) plays the game from a defined starting state, attempting a
defined task, with **three time-aligned streams** captured:

```
trajectory_<id>/
├── manifest.json          ← session metadata, task, model, alignment anchors
├── cot.jsonl              ← LLM reasoning (thinking blocks) + actions
├── video.mp4              ← H.264 / H.265 capture of bot's first-person view
├── frames.jsonl           ← per-frame timing + sha256 (alignment baseline)
├── metadata.jsonl         ← per-tick Minecraft world state from bot API
├── inputs.jsonl           ← per-step bot actions (move / mine / craft / chat)
└── provenance.json        ← uploader pseudonym + ToS chain + license
```

All streams share **session_id** + **session_start_wall_clock_utc** so any
two events from any two streams can be temporally aligned.

---

## 2. The three streams (体系)

### 2.1 CoT stream — `cot.jsonl`

Captures the LLM's full reasoning chain on every decision. One JSONL
line per LLM event:

```jsonl
{"timestamp": 12.473, "event_type": "LLM_PROMPT", "event_args": {"step": 5, "model": "claude-sonnet-4-5", "thinking_budget": 16000, "messages_tail": "..."}}
{"timestamp": 12.890, "event_type": "LLM_THINKING", "event_args": {"step": 5, "thinking_text": "I need to find wood. There's an oak tree at coords... I should mine it before the creeper..."}}
{"timestamp": 12.917, "event_type": "LLM_ACTION", "event_args": {"step": 5, "action": {"op": "move_to", "target": [124, 64, -82]}, "rationale_summary": "head to oak tree"}}
```

**Provider integration**:
- **Claude**: use `messages.create(thinking={"type": "enabled", "budget_tokens": 16000})` → response `content` includes `type: "thinking"` blocks
- **GPT-5**: use `reasoning_effort: "high"` → response includes reasoning blocks under `output_reasoning`
- Both providers: persist the **full** thinking text, not a summary

**Why CoT matters to buyers**: world-model labs (Decart, DeepMind Genie,
Physical Intelligence) want decision-process supervision, not just
input→output mappings. CoT is the differentiator.

### 2.2 Video stream — `video.mp4` + `frames.jsonl`

H.264 or H.265 capture of the **bot's first-person view** during the
entire session. Recorded by an OBS-style capture from a separate
spectator client tethered to the bot.

`frames.jsonl` provides per-frame timing alignment:
```jsonl
{"timestamp": 0.000, "event_type": "VIDEO_START", "event_args": {"width": 1920, "height": 1080, "fps": 30}}
{"timestamp": 0.0333, "event_type": "FRAME", "event_args": {"frame_index": 1, "rgb_sha256": "abc..."}}
```

**Why a separate spectator client**: Mineflayer bots don't render —
they're protocol-level clients with no graphics pipeline. To capture
video, we run a real Minecraft client in spectator mode that follows
the bot. This client does NOT participate in gameplay.

### 2.3 Metadata stream — `metadata.jsonl`

Minecraft world state at every server tick (20 Hz typical) or every
N ticks (configurable). Captured directly from Mineflayer bot's API:

```jsonl
{"timestamp": 0.050, "event_type": "TICK", "event_args": {"tick": 1, "bot_pos": [123.5, 64.0, -80.2], "yaw": 87.3, "pitch": -5.1, "health": 20, "food": 20, "xp": 0}}
{"timestamp": 0.050, "event_type": "INVENTORY", "event_args": {"tick": 1, "slots": {"0": {"name": "oak_log", "count": 4}, "1": null, ...}}}
{"timestamp": 0.050, "event_type": "BLOCKS_NEAR", "event_args": {"tick": 1, "radius": 8, "blocks": [{"pos": [124, 64, -82], "name": "oak_log"}, ...]}}
{"timestamp": 0.050, "event_type": "ENTITIES_NEAR", "event_args": {"tick": 1, "radius": 16, "entities": [{"id": 7, "type": "creeper", "pos": [...], "health": 20}]}}
{"timestamp": 0.050, "event_type": "TASK_STATE", "event_args": {"tick": 1, "active_goal": "collect_wood", "progress": {"oak_log": 4, "target": 16}}}
```

**Mineflayer API surface used**: `bot.entity.position`, `bot.inventory`,
`bot.findBlock(...)`, `bot.entities`, `bot.health`, `bot.food`,
`bot.experience`, custom task tracker maintained by us.

**Why metadata matters to buyers**: a CoT trace claiming "I see a
creeper, retreating" is only meaningful if the dataset **proves** there
was a creeper. Metadata is the ground truth.

---

## 3. Architecture

### 3.1 Recommended path: Mineflayer + OBS Spectator (架构 A)

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│   ┌──────────────────────────────────────────────┐               │
│   │  Spigot/Paper Minecraft server (local)        │               │
│   │  vanilla 1.20.x, no online-mode auth          │               │
│   │  optional plugin: state-snapshot exporter     │               │
│   └──────────────┬─────────────────────┬─────────┘               │
│                  │                     │                         │
│      protocol    │                     │  protocol               │
│                  │                     │                         │
│   ┌──────────────▼─────┐    ┌──────────▼──────────────┐          │
│   │  Mineflayer bot    │    │  Vanilla Minecraft       │          │
│   │  (Node.js)         │    │  client in SPECTATOR     │          │
│   │                    │    │  mode, follows bot       │          │
│   │  ─ exposes API ─►  │    │                          │          │
│   │  pos / inv /       │    │  windowed 1920×1080      │          │
│   │  blocks / mobs     │    │  rendering               │          │
│   │                    │    │                          │          │
│   │  ─ acts ─►         │    │     ┌──────────────────┐ │          │
│   │  movement /        │    │     │ OBS / ffmpeg     │ │          │
│   │  mine / craft /    │    │     │ window capture   │ │          │
│   │  chat              │    │     └──────┬───────────┘ │          │
│   └────────┬───────────┘    └────────────┼─────────────┘          │
│            │                              │                       │
│            │  Python LLM driver (our      │                       │
│            │  oyster-agent-runner code,   │                       │
│            │  via WebSocket bridge to     │                       │
│            │  Mineflayer)                 │                       │
│            │                              │                       │
│   ┌────────▼──────────────┐               │                       │
│   │  AgentRunner          │               │                       │
│   │  + Claude provider    │               │                       │
│   │    (thinking mode)    │               │                       │
│   │  + tool-use protocol  │               │                       │
│   │    (move/mine/craft)  │               │                       │
│   │  + memory (RAG)       │               │                       │
│   └────────┬──────────────┘               │                       │
│            │                              │                       │
└────────────┼──────────────────────────────┼───────────────────────┘
             │                              │
             │ writes:                      │ writes:
             ▼                              ▼
   trajectory_<id>/                trajectory_<id>/
     ├── cot.jsonl                    └── video.mp4
     ├── metadata.jsonl                   + frames.jsonl
     ├── inputs.jsonl
     └── manifest.json
```

**Synchronization mechanism**:
- A single coordinator process (Python) holds `session_start_wall_clock_utc`
- Spawns the Mineflayer bot via Node.js subprocess; subprocess sends
  ticks back to coordinator via stdin/stdout JSON line protocol
- Coordinator writes `metadata.jsonl` directly from bot ticks
- Coordinator drives the LLM and writes `cot.jsonl` (with thinking + action)
- Coordinator launches OBS-CLI / ffmpeg window-capture against the
  spectator client window; emits `frames.jsonl` from ffmpeg PTS metadata
- All four files use the same wall-clock anchor → time-aligned

**Why A wins over alternatives**:

| | A. Mineflayer + OBS | B. MineRL Gym | C. Custom Forge mod |
|---|---|---|---|
| Dev velocity to first bundle | 🟢 1-2 weeks | 🟡 3-4 weeks (Java 8 install hell) | 🔴 4-6 weeks |
| Modern Minecraft (1.20+) | 🟢 yes | 🔴 stuck on 1.11.2 | 🟢 yes |
| Metadata richness | 🟢 full Mineflayer API | 🟡 Gym observation space (subset) | 🟢 server-side total |
| Video render quality | 🟢 vanilla client = native | 🟡 headless rendering | 🟢 vanilla |
| Anti-cheat / multiplayer | 🟢 N/A — local server | 🟢 N/A | 🟢 N/A |
| Reproducibility (paper-friendly) | 🟡 ours-stack | 🟢 standard research env | 🟡 ours-stack |
| First-pilot friendly to Decart | 🟢 vanilla MC visuals match their Oasis VPT lineage | 🟡 old visuals | 🟢 |

A wins on dev velocity + modern visuals + metadata richness for the
common case. We can add MineRL as a B-tier offering later if a buyer
specifically wants it.

### 3.2 Time-alignment contract

All four streams use:
- **Wall-clock anchor**: `session_start_wall_clock_utc` ISO 8601 string in `manifest.json`
- **Relative timestamps**: `timestamp` field in each JSONL entry = seconds since session start (float)
- **Cross-stream join key**: any two events with `|t_a - t_b| < 1/fps` are considered aligned

A **buyer-facing alignment proof** ships with each bundle: `manifest.json`
includes:
```json
{
  "alignment": {
    "anchor_utc": "2026-04-25T18:30:00.000Z",
    "video_fps": 30,
    "metadata_tick_hz": 20,
    "cot_event_count": 412,
    "video_frame_count": 5400,
    "metadata_tick_count": 3600,
    "max_observed_drift_ms": 8.3
  }
}
```

The drift number is computed at ingest (we test cross-stream alignment
on a few sentinel events: bot says "/say hello" → server logs +
chat message in metadata + bot inventory action + audio in video).

---

## 4. Task / scenario definition

A **task** parameterizes what the agent attempts:

```json
{
  "task_id": "MC-survival-wood-stone-iron-001",
  "natural_language_instruction": "Spawn in survival mode, in 30 minutes collect at least 16 oak logs, 16 cobblestone, and 8 raw iron. Avoid combat with hostile mobs.",
  "success_criteria": [
    "inventory contains >= 16 oak_log",
    "inventory contains >= 16 cobblestone",
    "inventory contains >= 8 raw_iron",
    "bot.health > 0"
  ],
  "world_seed": 42,
  "spawn_position": [0, 64, 0],
  "max_minutes": 30,
  "max_steps": 1800,
  "model_required": "claude-sonnet-4-5",
  "thinking_budget_tokens": 16000
}
```

A **task library** (`tasks/`) ships with the agent runner:
- `MC-tutorial-001` — Punch a tree, collect 1 log
- `MC-survival-wood-stone-iron-001` — Standard early-game progression
- `MC-build-shelter-002` — Build a 4×4 enclosed structure before night
- `MC-fight-zombie-003` — Defeat a zombie with a wooden sword
- `MC-explore-cave-004` — Find and enter a cave system
- `MC-villager-trade-005` — Find a village, trade with a villager
- ...

Tasks can be parameterized (`world_seed`, `spawn_position`,
`max_minutes`) so the same task definition produces a diverse set of
bundles.

---

## 5. Scale + economics

### 5.1 Throughput target (per spec discussion)

For a pilot bundle to Decart: **20 hours of trajectory data** across
3-5 task types and 2-3 LLM models = ~40 trajectories of 30-60 minutes
each.

### 5.2 Per-trajectory cost (rough)

Cost components:
- **Compute**: local Mac mini / Linux box runs the server + bot + 1 spectator client → free (already-owned hardware)
- **LLM API tokens**:
  - Claude with thinking, budget 16K, ~50 messages/min × 60min = 3000 messages/session
  - At ~$3/MTok input + $15/MTok output for Claude Sonnet 4.5: ~$5-15 per 30-60min session
  - Per 20-hour bundle (40 sessions): **~$200-600 in LLM tokens**
- **Storage**: 20 hours × 1080p H.264 ≈ 36 GB. Cloudflare R2 at $0.015/GB-month + $0/egress = trivial

**Sell price target**: $450/hr enriched-with-CoT (premium tier).
**Margin**: per 20-hour pilot = $9,000 revenue, $200-600 token cost = ~93-98% gross margin.

### 5.3 Concurrency

Each "trajectory generator" is one LLM-driven bot + one spectator
client + one local server. Memory footprint: ~2 GB RAM per instance.

A 16 GB Mac can run **6-8 generators in parallel** if we budget cores
and GPU/render correctly. Linux box with 32 GB and 1 GPU: 12-16
parallel.

A 20-hour bundle on 8 parallel generators = ~3 hours wall-clock to
produce. **Day-scale turnaround** instead of weeks.

---

## 6. Data sheet for buyers

What ships in each bundle (vs. our current generic-enrichment offer):

| Component | Generic L1 (current) | **L4 Minecraft (this product)** |
|---|---|---|
| RGB video | ✅ | ✅ (1080p, 30fps, H.264) |
| Per-frame ML depth | ✅ relative (DA-V2) | ❌ (not relevant for Minecraft) |
| Per-frame ML pose | ✅ OpenCV VO | ❌ (server tells us bot pos directly) |
| **Per-frame metadata (true game state)** | ❌ | ✅ ⭐ pos / inv / blocks / mobs / health |
| **CoT (full LLM reasoning trace)** | ❌ | ✅ ⭐⭐ thinking + actions per step |
| Per-step inputs/actions | partial | ✅ structured ops |
| Provenance + consent | ✅ | ✅ |

The **two ⭐⭐ rows are the differentiation**: CoT + ground-truth state.
This is data Chinese commodity farms cannot produce (no LLM API
budget, no engine modding capability). Even Western competitors
(General Intuition, Medal) don't ship CoT.

---

## 7. Build path

### Phase 1 — Single-bot prototype (Week 1)

- [ ] Spin up local Paper 1.20.x Minecraft server (no auth, fixed seed)
- [ ] Mineflayer Node.js bot connects, basic movement smoke-tested
- [ ] Python coordinator ↔ Node bot subprocess via JSON-line protocol
- [ ] Coordinator drives Claude via `oyster_agent_runner` ClaudeProvider with `thinking={"type":"enabled"}` mode
- [ ] Action output parsed, dispatched to Mineflayer (move / dig / look / chat — start with 4 actions)
- [ ] `cot.jsonl` + `metadata.jsonl` + `inputs.jsonl` write end-to-end
- [ ] First 5-minute trajectory generated for `MC-tutorial-001` (punch a tree)

DoD: open the trajectory dir, see 3 files, manually inspect that
timestamps line up.

### Phase 2 — Video stream (Week 2)

- [ ] Vanilla Minecraft client launched in spectator mode (or dedicated chase camera) tracking bot
- [ ] OBS-CLI / ffmpeg window-capture targeting the client window
- [ ] `frames.jsonl` emitter from ffmpeg PTS
- [ ] Drift measurement via `/say` chat sentinel — establish < 50 ms drift target

DoD: video plays, at second 60 the chat message in video matches
chat event in metadata.jsonl within 50 ms.

### Phase 3 — Task library + scale (Week 3)

- [ ] Implement 5 task types per § 4
- [ ] Per-task success-criteria evaluator (post-session, marks
      `task_completed: bool` in manifest)
- [ ] Concurrency: run 4 generators in parallel on Mac mini, then 8
- [ ] Per-bundle packager → zip with provenance + alignment proof

DoD: 20-hour bundle generated unattended in < 4 hours.

### Phase 4 — Pitch + delivery (Week 4)

- [ ] Bundle uploaded to Cloudflare R2, presigned URL
- [ ] Decart-specific pitch addendum (this CoT data will help Oasis v2
      do better tool use)
- [ ] First send to Decart ML lead

DoD: meeting on calendar.

---

## 8. Open decisions Howard needs to make

| # | Decision | Options | My recommendation |
|---|---|---|---|
| **D1** | Architecture | A (Mineflayer + OBS) / B (MineRL) / C (Forge mod) | **A** — fastest to first bundle, modern Minecraft visuals, full metadata |
| **D2** | First task | tutorial / survival / build / combat / explore / trade | Start with **tutorial** + **survival-wood-stone-iron**. Simple to validate, proves the pipeline |
| **D3** | Models in scope | Claude only / GPT only / both | **Both** for differentiation — buyers can compare reasoning styles. Adds ~30% engineering. |
| **D4** | Server location | Local Mac mini / Linux box / Cloud VM | Local for prototype. Cloud (RunPod CPU) when we scale |
| **D5** | Bundle naming | per-trajectory / per-batch | Per-trajectory zips, batched-ship in a manifest-of-bundles |
| **D6** | Pricing | $450/hr standard / premium tier for CoT | **Premium $600/hr** — CoT is unique in market |
| **D7** | License terms | Buyer trains-only / can-redistribute | trains-only; raw bundle non-redistributable |

---

## 9. Risks

1. **Mineflayer API drift**: Mineflayer maintains coverage as Minecraft updates. If we lock to 1.20.x, library is well-supported.
2. **Spectator chase-cam smoothness**: standard Minecraft spectator follow can lag. May need a custom camera control plugin for smooth video.
3. **LLM action latency**: Claude with thinking 16K budget can take 5-15 s per turn. Bot must wait — we can't get >10 actions/min. Acceptable for dataset purposes but need to document.
4. **Token cost surprise**: thinking-mode is 2-5× more expensive than non-thinking. Watch the bills closely in Phase 1.
5. **Alignment drift on long sessions**: > 1 hour, we may see clock drift. Implement periodic re-anchor via `/say` sentinels every 5 min.

---

## 10. Why this is the saleable L4

The previous L4 framing (`agent-runner` scaffolding) was generic. This
spec is **a specific data product with a price**:

> 20-hour Minecraft trajectory bundle, CoT + video + metadata, sourced
> from Claude/GPT thinking-mode play. $9 K pilot, deliverable in 1-2
> weeks of wall-clock after Phase 1-2 land.

Decart trained Oasis on Minecraft VPT. **CoT-supervised Minecraft
trajectories are exactly the next layer above VPT** — not just "what
the human did" but "why the agent thought that move was right". That's
the conditioning data Genie / Oasis-class world models claim they
need.

The fact that Chinese commodity farms can't produce this (no Claude
API access in mainland China for routine commercial use, no LLM-driven
agent infrastructure, no Western IP path) is the structural moat.
