# Single-Player Games Catalog — 2026-05-05

> Howard's directive: "online 不行 看看 单机的一半都可以"
> Audit shows ~60-70% of major single-player titles have official or
> community-blessed extraction paths. This doc is the prioritized
> rollout plan with concrete SDK pins per game.

---

## TL;DR

| Tier | Count | What it means | Buyer-pitch line |
|---|---|---|---|
| **P0 — ship next 30 days** | 6 games | Mature SDK, big mod community, our team can integrate in <1 week each | "Six AAA + sandbox titles ready" |
| **P1 — ship next quarter** | 8 games | Working SDK, less mature integration | "RPG, sim, shooter, sandbox covered" |
| **P2 — opportunistic** | 7 games | Possible but vendor-effort > our-effort | Available on demand |
| **🔴 OUT** | online-only / kernel AC | Valorant, LoL, Genshin, online CS2 lobby etc. | Use replay-parser path or skip |

Worst-case fallback for **any** game: RGB video + Raw Input + DepthAnything V2 inference. Always available; degrades gracefully.

---

## P0 — Ship in next 30 days (6 games, high ROI)

### 1. Minecraft Java 1.20.4 ✅ PRODUCTION
- **Status**: shipped today (`environments/minecraft.py`, 588 LOC)
- **Extraction**: Mineflayer 4.20 official Java protocol
- **Coverage**: 20/20 action_camera fields + DepthAnything V2 depth
- **Vendor effort**: ~10 min install (per `docs/SOP_LAO_LIU.md`)

### 2. BeamNG.drive 🟡 SCAFFOLDED
- **Status**: `environments/factorio.py` parallel; `BEAMNG_RUNBOOK.md` ready
- **Extraction**: `pip install beamngpy>=1.27` (already in `pyproject.toml [beamng]`)
- **What it gives**: native depth sensor + camera intrinsics + vehicle pose/velocity at 60 Hz
- **Why P0**: only paid game in P0 list ($24.99 Steam) but **driving data is in Wayve / Tesla / Cosmos training corpora** — premium buyer demand
- **Integration estimate**: 2 days

### 3. Factorio 🟡 SCAFFOLDED
- **Status**: `environments/factorio.py` 148 LOC stub with RCON URI parser
- **Extraction**: official mod API + RCON; we ship a tiny observer mod
- **What it gives**: 2D-orthographic camera + player position + tile state
- **Why P0**: $30 Steam, very large modding community, Factorio devs blessed automation
- **Integration estimate**: 3 days (mod-side Lua + Python RCON glue)

### 4. Stardew Valley 🆕 NEW
- **Extraction**: SMAPI (Stardew Modding API) — official-blessed framework
- **Pin**: `SMAPI 4.x` from smapi.io
- **What it gives**: player position (x, y, mapName), facing direction, action key state, NPC positions
- **Camera**: 2D top-down; we map to virtual pinhole same as Factorio
- **Why P0**: huge audience, SMAPI is bulletproof (used by 1000+ mods)
- **Integration estimate**: 2 days

### 5. Cyberpunk 2077 🆕 NEW (single-player only)
- **Extraction**: CET (Cyber Engine Tweaks) — Lua scripting framework
- **Pin**: `CET 1.32+`
- **What it gives**: full 6-DoF camera + player pose, velocity, FOV, day-night, vehicle state, weather
- **Depth**: native G-buffer extraction via REDmod or DepthAnything V2 fallback
- **Why P0**: very popular single-player AAA, mature CET community, large modding scene
- **Integration estimate**: 4 days (CET Lua bridge + Python relay)

### 6. Cities Skylines (1 or 2) 🆕 NEW
- **Extraction**: official Mod API + Harmony patching (community standard)
- **Pin**: in-game "Workshop" mod ecosystem
- **What it gives**: camera position (3D), zoom level, simulation tick, citizen positions
- **Why P0**: city-builder = aerial trajectory data, useful for autonomous navigation training
- **Integration estimate**: 3 days

### P0 totals
- 6 games covering: open-world AAA, sandbox top-down, sim, RPG, sandbox 2D, city-builder
- Total integration: ~14-21 person-days
- Each one independently shippable

---

## P1 — Ship next quarter (8 games)

| # | Game | SDK / Path | Notes | Days |
|---|---|---|---|---|
| 7 | **Skyrim Special Edition** | SKSE64 + Papyrus scripts | most-modded game ever; need Papyrus extension to expose pose | 5 |
| 8 | **Fallout 4** | F4SE + Papyrus | same pattern as Skyrim | 4 |
| 9 | **Witcher 3 Next-Gen** | Wolvenkit + REDscript | smaller community than CET but functional | 5 |
| 10 | **Baldur's Gate 3** | script extender (`bg3-script-extender`) + console | Larian's modding tools formal-ish | 5 |
| 11 | **Microsoft Flight Simulator** | SimConnect SDK (official, free) | aviation telemetry — premium buyer category | 4 |
| 12 | **Garry's Mod** (single-player sandbox) | Lua hooks (the engine philosophy) | best sandbox to capture diverse interactions | 3 |
| 13 | **Half-Life 2 / Portal 2** | Source SDK | classic test data, low player count today | 4 |
| 14 | **Assetto Corsa** | Python plugin API (official) + shared-mem telemetry | racing telemetry, big sim-racing community | 3 |

**P1 total**: 8 games, ~33 person-days

---

## P2 — Opportunistic (7 games, ship on buyer request)

| # | Game | Path | Why P2 |
|---|---|---|---|
| 15 | DCS World | SimScript + LoFiSim | flight-sim niche |
| 16 | KSP / KSP 2 | Telemachus mod | space-sim niche |
| 17 | RimWorld | Harmony patching | top-down 2D, low buyer demand |
| 18 | Terraria | tModLoader | 2D, low buyer demand |
| 19 | Don't Starve | official mod API | 2D, low buyer demand |
| 20 | Valheim | community mods | survival, mid demand |
| 21 | ATS / ETS2 | telemetry SDK + workshop mods | trucking sim, niche |

---

## 🟡 Single-player MODE only (online would ban)

| Game | What works in SP | What breaks online |
|---|---|---|
| **GTA V** | Script Hook V | Online lobby requires uninstall, else BattlEye permanent ban |
| **RDR2** | Script Hook RDR2 | Online same — must uninstall first |
| **Forza Horizon 5** | limited SP mods | Online flags overlays |

Vendor agreement: **don't connect online with our recorder/scripts active.**

---

## 🔴 OUT — kernel AC OR no extraction path

- Valorant / LoL / TFT (Vanguard kernel)
- Genshin Impact / HSR / Wuthering Waves (mhyprot kernel)
- Most JRPGs with no scripting (Persona 5, FFVII Remake)
- Recent Sony exclusives (HZD, GoT — extremely limited modding)
- Death Stranding / Death Stranding 2 (no mod scene)

For these, we either skip OR ship a degraded "RGB + RawInput + DepthAnything inference" tier. Buyer chooses.

---

## Concrete pinned SDK matrix

| Game | Required SW | Pin | Status |
|---|---|---|---|
| Minecraft Java | Mineflayer | `^4.20.0` | ✅ in `mineflayer/package.json` |
| BeamNG | beamngpy | `>=1.27` | ✅ in `pyproject.toml [beamng]` |
| Factorio | RCON + observer mod | RCON 25575, mod TBD | 🟡 stub in `environments/factorio.py` |
| CS2 (post-game) | demoparser2 | included | ✅ in `pyproject.toml [cs2]` |
| Stardew Valley | SMAPI | `4.x` from smapi.io | ⚪ not yet integrated |
| Cyberpunk 2077 | CET | `1.32+` Lua | ⚪ not yet integrated |
| Cities Skylines | Mod API | Workshop | ⚪ not yet integrated |
| Skyrim SE | SKSE64 | latest | ⚪ not yet integrated |
| Fallout 4 | F4SE | latest | ⚪ not yet integrated |
| Microsoft FS | SimConnect | bundled with game | ⚪ not yet integrated |
| Assetto Corsa | shared-mem + Python plugin | official | ⚪ not yet integrated |

---

## Architecture re-use across games

```
┌──────────────────────────────────────────────────┐
│  Game-specific extractor (small Python module)  │
│  - Mineflayer.js → JSON over stdio (MC)         │
│  - BeamNGpy.connect() (BeamNG)                  │
│  - SMAPI.event-bus → REST (Stardew)             │
│  - CET Lua → websocket (CP2077)                 │
│  - SimConnect → Python (MFS)                    │
│  - shared_mem.read() (AC)                       │
│  → outputs `metadata.jsonl` + `inputs.jsonl`    │
└──────────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────┐
│  buyer_spec_adapter.py (game-agnostic core)      │
│  - normalizes coords (left-hand, meters)         │
│  - computes camera intrinsics (fx, fy, Cx, Cy)   │
│  - emits action_camera.json (20 fields)          │
└──────────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────┐
│  OBS recorder (game-agnostic) + DepthAnything    │
│  - video.mp4 + depth/*.exr                       │
└──────────────────────────────────────────────────┘
                      │
                      ▼
                  tarball + lint
```

**Game-specific code is the small box at top.** Everything below is reused. Adding a new game = writing one extractor module (~200-400 LOC) + a runbook. That's the unit of work.

---

## Per-stakeholder one-liner

> **Vendor**: "Pick a single-player game from our list — Minecraft, BeamNG, Stardew, Cyberpunk, Cities Skylines, etc. Run our recorder + the game's official mod for that title. Ship clips, get paid."

> **Buyer**: "Day 1 we deliver Minecraft Java. Within 30 days: BeamNG driving, Factorio sandbox, Stardew, Cyberpunk, Cities Skylines. Within 90 days: 14 single-player titles spanning RPG, FPS, racing, flight-sim, and sandbox."

> **Investor**: "Single-player coverage is ~70% of major modern AAA + sandbox + sim. Each new game is a ~3-5 day integration. Pipeline architecture is game-agnostic core + thin per-game extractor — repeatable scale-out."

Stable URL: https://github.com/howardleegeek/oyster-gamedata-pipeline/blob/main/docs/SINGLE_PLAYER_GAMES.md
