# Oyster Recorder — Minecraft Fabric Mod

Closes the **last placeholder gap** in OysterRecorder.exe. When a human
plays Minecraft and uses the recorder, the tarball's
`action_camera.json` ships REAL `camera_position`, `player_position`,
`*_rotation_oula`, `*_rotation_quaternion`, and `*_speed` fields instead
of the constant `[0.0, 64.0, 0.0]` placeholder.

## Architecture

```
Minecraft (with mod loaded)
        │ ClientTickEvents.END_CLIENT_TICK (~20 Hz)
        ▼
GameStateCapture.tick()
        │ position, rotation, velocity, dimension, gamemode
        ▼
JsonlWriter.append() ─────► ~/Documents/OysterClips/active_session/game_state.jsonl
                                                    │
                                                    │ at packaging time
                                                    ▼
                            OysterRecorder.exe ◀── reads JSONL
                                  │
                                  │ overlays real values
                                  ▼
                            action_camera.json (REAL fields)
                                  │
                                  ▼
                              tarball ───► tester sends back
```

**Fail-soft**: if the mod isn't installed, the recorder falls back to
its existing placeholder behaviour and tags the README accordingly.

## Build

```bash
cd mc-mod
./gradlew build
# output: build/libs/oyster-recorder-mod-X.Y.Z.jar
```

Requires Java 21 (matches Minecraft 1.21+ requirements).

## Install (tester instructions, future)

1. Download `oyster-recorder-mod-X.Y.Z.jar` from the GitHub release
2. Drop into `%APPDATA%\.minecraft\mods\` (Windows) or
   `~/Library/Application Support/minecraft/mods/` (macOS)
3. Make sure your Minecraft launcher uses the **Fabric loader** profile
   matching the mod's MC version (1.21.4 currently)
4. Launch Minecraft and play normally
5. The recorder picks up real game state automatically

## JSONL schema

Single-line JSON per tick. Fields:

| field | type | description |
|-------|------|-------------|
| tick | int | monotonic counter from mod load |
| timestamp_ms | int | ms-since-epoch (UTC) |
| x, y, z | float | player feet position (blocks) |
| yaw_deg | float | -180..180, 0 = south |
| pitch_deg | float | -90..90, negative = up |
| look_x, look_y, look_z | float | unit look-vector |
| velocity_x, velocity_y, velocity_z | float | blocks per tick |
| on_ground | bool | true if standing/walking |
| sneaking, sprinting | bool | |
| dimension | string | e.g. `minecraft:overworld` |
| game_mode | string | `SURVIVAL` / `CREATIVE` / etc. |

**Schema is the canonical contract** between mod and recorder. Changing
it requires updating both `mc-mod/src/main/java/world/oyster/recorder/GameStateSample.java`
and `bin/game_state_overlay.py`. CI should add a test that asserts the
field set on both sides matches.

## Why Fabric over Forge

- Lighter weight build (no IDE plugin reqs for testers)
- Faster MC version updates (Fabric ships day-1 for new MC)
- We don't need any block/item/entity registration — just one tick hook
- ~50 line dependency footprint via fabric-api

## Known limitations

- Only client-side (single-player or LAN). Server-side capture would
  need a separate server-mod entry point — out of scope for v0.1.0.
- Yaw/pitch → quaternion conversion in the recorder assumes zero roll
  (MC's vanilla camera doesn't roll). If a future mod adds roll, the
  conversion needs updating.
- Spectator/creative mode is captured but not flagged. The
  `game_mode` field lets D5 filter if needed.
