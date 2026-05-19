# Per-Game Data Source Strategy — 2026-05-05

> Howard's correct observation: "PRD requires data the game must export — pure
> OBS+RawInput cannot give us 9 of 20 action_camera fields + the depth track."
>
> Answer: every supported game has an **official-channel path** to that data
> that does NOT trigger anti-cheat. We use those, never memory reads.

---

## The 11 PRD fields that need game-internal state

From `docs/PRD_AUDIT_2026_05_04.md`:

| Field | Why OBS+RawInput can't give it |
|---|---|
| `camera_position` Vector3 | game world coordinates |
| `camera_rotation_oula` Vector3 | game's pitch/yaw/roll |
| `camera_rotation_quaternion` Vector4 | same in quaternion form |
| `camera_intrinsics` {fx, fy, Cx, Cy} | game's projection matrix |
| `camera_speed` Vector3 | game-internal velocity |
| `player_position` Vector3 | game world coordinates |
| `player_rotation_oula` Vector3 | game-internal rotation |
| `player_rotation_quaternion` Vector4 | same in quat |
| `player_speed` Vector3 | game-internal velocity |
| `metric_scale` float | game-unit-to-meter ratio |
| `depth/*.exr` per frame | rasterizer Z-buffer |

**The other 9 fields** (frame, time, fps, route_type, mouse_x/y/dx/dy, keyCode) come from RawInput + capture timestamps — game-agnostic.

---

## Per-game extraction strategy (all official channels, no anti-cheat issues)

### 🟢 Minecraft Java 1.20.4 (production today)

| Field group | Source |
|---|---|
| Camera + player position/rotation/velocity | **Mineflayer Java protocol client** — official, server admin–controlled |
| Camera intrinsics | derived from game's FOV (default 70°) + render resolution → fx, fy, Cx, Cy computed analytically (see `buyer_spec_adapter.py`) |
| metric_scale | 1.0 (1 MC block ≈ 1 m) |
| depth track | option A: **shader pack** (Iris+Sodium with depth export) on private server; option B: **DepthAnything V2 inference** from RGB video (`bin/synthesize_real_depth.py`); option C: integrated raycast queries via Mineflayer |

**Anti-cheat verdict**: zero risk — only runs on our private Paper server.

### 🟢 BeamNG.drive (scaffolded)

| Field group | Source |
|---|---|
| Camera/player position+rotation+speed | **BeamNGpy official Python API** — Lua bridge that BeamNG ships with |
| Camera intrinsics | BeamNG exposes the active camera's FOV + sensor resolution natively |
| metric_scale | 1.0 (BeamNG uses meters) |
| depth | BeamNG's **Camera sensor** has a built-in depth output mode (`CameraSensorMode.DEPTH`) |

**Anti-cheat verdict**: BeamNG has no anti-cheat. BeamNGpy is the developer-blessed automation API.

### 🟢 Factorio (scaffolded)

| Field group | Source |
|---|---|
| Player position+rotation+speed | **RCON + mod API** — both official |
| Camera intrinsics | Factorio is 2D-orthographic; we map to a virtual pinhole |
| metric_scale | 1.0 (1 tile = 1 m by convention) |
| depth | not applicable (2D); we ship a synthetic flat-Z plane |

**Anti-cheat verdict**: Factorio has no anti-cheat. RCON + mods are first-class.

### 🟡 CS2 (Counter-Strike 2)

| Field group | Source |
|---|---|
| Camera+player state | **`demoparser2`** on post-game `.dem` files (already pinned in `pyproject.toml [cs2]` extras) |
| Depth | DepthAnything V2 inference from spectator video (`.dem` → replay → OBS-record-spectator) |

**Anti-cheat verdict**: VAC is fine because we **never read live game memory**. `.dem` files are exported by the game itself after the match. Streamers process demos all the time.

### 🟡 Dota 2 / TF2 / other Source 2 games

Same `.dem` parser pattern as CS2. Source 2's demo format is a published spec.

### 🟡 Fortnite

`.replay` files are stored to disk by the game post-match. Community has reverse-engineered the format (see `replays.fortnite.com`-style tooling). Same pattern: **post-game replay → spectator render → OBS capture → DepthAnything**.

### 🟢 Single-player Skyrim / Fallout / Witcher 3 / RDR2

Console commands + script-extender frameworks (SKSE, F4SE, etc.) are **legitimate modding tools** the game's modding community uses. No anti-cheat. We can:
- Read player + camera state via console commands or scripts
- Extract depth via mod-injected shader OR DepthAnything inference

### 🔴 Valorant / LoL / Genshin / HSR / Wuthering Waves

**Not supported.** No official data export channel. The only way to get `player_position` etc. would be memory reads, which Vanguard / mhyprot / hyperion correctly classify as cheating.

If a buyer asks for these games specifically, the answer is **"no, we don't ship that vector — but we can ship RGB + RawInput data only, with depth via DepthAnything V2 inference from video, and pose annotations via a hand-labeled or VLM-derived pseudo-label."** That degrades to a less-rich tier.

---

## What goes wrong if we tried to "patch" a Valorant client

1. Vanguard kernel driver detects modified game.dll within seconds → permanent hardware ban
2. Vanguard's TPM-attestation also reports the modification to Riot's backend
3. Vendor's account is permanently banned across ALL Riot games
4. Possibly the vendor's PC is hardware-fingerprinted for 6 months

**This is why we never go that path.** It's not a technical limitation — it's a hard policy.

---

## How depth specifically works in our pipeline

| Path | When | Cost | Quality |
|---|---|---|---|
| **Game's depth API** (Iris/Sodium for MC, BeamNG sensor) | game has a depth-export mode | low | ground truth |
| **Shader pack** (e.g. SEUS PTGI on MC) | private server, vendor enabled | low | ground truth |
| **DepthAnything V2** (RGB → depth inference) | always available, no game cooperation needed | medium (one inference per frame) | ~85-95% accuracy on outdoor scenes, lower on textureless interior |
| **Stereo (game's native stereo render)** | unusual; some games support side-by-side stereo | medium | ground truth |

For Minecraft today we use **option C (DepthAnything V2)** at 6 fps because the inference is fast enough on consumer GPUs. This is the same model the Wayfarer / OWL Control fork uses.

---

## Summary for stakeholders

> "Our recorder is OBS + RawInput — those are passive and never trigger
> anti-cheat. To get the **PRD's required game-internal state** (player
> position, camera intrinsics, depth), we use each game's **official
> data-export channel**: Mineflayer for MC, BeamNGpy for driving, mod
> APIs for sandbox games, post-game replay parsers (`.dem`/`.replay`)
> for VAC/EAC games. We **never** read game memory. Kernel-AC games
> like Valorant get a depth-from-RGB fallback or are simply not
> supported — we don't compromise on the anti-cheat policy."

Stable URL: https://github.com/howardleegeek/oyster-gamedata-pipeline/blob/main/docs/PER_GAME_DATA_SOURCES.md
