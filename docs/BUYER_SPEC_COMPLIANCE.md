# Buyer-Spec v1 Compliance — Phase 1 Trajectory Bundles

> **Audience:** Buyers and integrators evaluating Oyster Labs Phase 1
> Minecraft trajectory output against the buyer-spec v1 acceptance bundle
> (`oyster-enrichment/docs/BUYER_SPEC_v1.md`).
>
> **Mirror page:** This is the trajectory-side companion to the L1 site
> landing page (`oyster-enrichment/site/buyer-spec.html`). The L1 page
> covers `video.mp4` + `gameinfo.xlsx` + per-frame `depth/*.exr`; this
> page covers what Phase 1 ships *today* with no video and no engine
> sidecar.
>
> **Implementation:** All claims on this page are backed by
> [`src/oyster_agent_runner/buyer_spec_adapter.py`](../src/oyster_agent_runner/buyer_spec_adapter.py).
> The adapter is import-clean of `numpy` / `openpyxl` and runs on a stock
> Python 3.11+ install with no additional pip deps.

---

## Phase 1 trajectory ↔ buyer-spec mapping

A Phase 1 bundle (`MinecraftStreamWriter` output) is a four-file
directory: `manifest.json` + `cot.jsonl` + `metadata.jsonl` +
`inputs.jsonl`. The buyer-spec v1 acceptance bundle wants a different
four-file layout per recording:

| Buyer-spec deliverable | Phase 1 source | How |
|---|---|---|
| `action_camera.json` | `metadata.jsonl` (OBSERVATION + TICK events) | `_build_buyer_records()` walks the metadata stream and emits one record per observed tick — see `buyer_spec_adapter.py` |
| `systeminfo.json` | static (operator-set window geometry) | Defaults to `1920×1080` at `(0, 0)` with `recordDpi = 1.0` |
| `gameinfo.json` | `manifest.json` (Phase 1 carry-through) | JSON-equivalent of the buyer's `gameinfo.xlsx`; carries `task_id` / `model` / `provider` / `phase` |
| `manifest.json` | `manifest.json` | Verbatim pass-through; preserves provenance trail back to the LLM run |

The 20 buyer fields per `action_camera.json` row are the canonical
buyer-spec v1 list (see `BUYER_SPEC_FIELDS` in
`buyer_spec_adapter.py`). Every row is order-stable and matches the L1
converter (`oyster-enrichment/bin/convert_to_buyer_spec.py`) so a buyer
can ingest either output without code changes.

## What we have ground-truth for

Phase 1 is **Mineflayer-driven**, which means the bot's world state is
read from the in-engine entity rather than estimated from pixels. The
following buyer-spec fields are exact within Minecraft's tick model:

- **`player_position`** — `bot.entity.position` (`vec3.x|y|z`) is the
  authoritative position of the player entity. Mineflayer reads it
  directly from the chunked world state.
- **`player_rotation_oula`** and **`player_rotation_quaternion`** —
  derived from `bot.entity.yaw` (radians) and `bot.entity.pitch`
  (radians). These are the same values the vanilla client uses to render
  the camera; there is no hidden interpolation step.
- **`metric_scale`** — exactly `1.0`. One Minecraft block is, by
  contract, one metre. `MINECRAFT_METRIC_SCALE` in the adapter pins
  this so a buyer never has to ask.

Because these come straight from the bot, no vision model, depth
heuristic, or fitting step sits between the simulator and the buyer's
record. This is the strongest ground-truth claim Oyster Labs makes for
any Phase across the roadmap.

## What we synthesize

A handful of buyer fields have no Mineflayer-native source, so we fill
them deterministically from documented Minecraft conventions:

- **`camera_position`** — third-person follow only. Vanilla Minecraft's
  F5 view places the camera 4 blocks behind and ~1.6 blocks above the
  player. We use `DEFAULT_FOLLOW_OFFSET = (0.0, 1.6, -3.0)` in the
  buyer's left-hand frame (`+Y` up, `-Z` behind) and compute
  `camera_position = player_position + follow_offset`. Buyers wanting
  first-person can override the offset to `(0, 0, 0)`.
- **`camera_intrinsics`** — Minecraft default FOV is 70°. At 1920×1080
  the pinhole model gives `fx = fy ≈ 685.61`, `cx = 960`, `cy = 540`
  via `camera_intrinsics_for_minecraft()`. Same model as
  `convert_to_buyer_spec._intrinsics_from_fov` so values match the L1
  pipeline.
- **`camera_rotation_*`** — equal to `player_rotation_*` because Phase 1
  has no independent camera yaw. (When Phase 2 lands video capture from
  a spectator client, this field becomes independently tracked.)
- **`camera_speed` / `player_speed`** — finite-differenced from the
  previous frame's `camera_position`. Frame 0 is `0.0`.

## What's NULL

Phase 1 deliberately omits the keyboard / mouse layer. The LLM agent
dispatches *high-level* Mineflayer actions (`bot.dig`, `bot.lookAt`,
`bot.setControlState`) — there is no synthetic mouse motion or virtual
key press to record. The following buyer-spec fields are emitted as
explicit `null`:

- **`mouse_x` / `mouse_y`** — no on-screen cursor; Phase 1 is
  headless.
- **`mouse_dx` / `mouse_dy`** — no per-tick mouse delta; bot orientation
  is set as `lookAt(target)` rather than mouse-style integration.
- **`keyCode`** — no virtualised keystroke layer; control state is set
  via Mineflayer's `setControlState` API.

Buyers who require a keyboard / mouse trace should consume the L1
desktop-recording bundle instead, which captures the human / OS input
layer directly.

## Coordinate conversion

Mineflayer reports world state in Minecraft's right-handed frame:

- `+X` = east
- `+Y` = up
- `+Z` = south
- yaw radians, `0` = facing south, CCW positive viewed from above
- pitch radians, `0` = level, positive = look down

The buyer-spec target frame is left-handed:

- `+X` = right
- `+Y` = up
- `+Z` = front (into the screen)
- pitch degrees, positive = look up
- yaw degrees, positive = turn right (CW from above)

Right-hand → left-hand conversion negates exactly one axis. We negate
`X` so `+Z` continues to mean "forward" for a south-facing observer
and `+Y` continues to mean "up". The full conversion table is:

```
buyer_x          = -mc_x
buyer_y          =  mc_y
buyer_z          =  mc_z
buyer_pitch_deg  = -degrees(mc_pitch_rad)
buyer_yaw_deg    =  degrees(mc_yaw_rad)
buyer_roll_deg   =  0.0      # Minecraft has no head-tilt action
```

Quaternion construction uses the same Y-X-Z extrinsic ordering
(`q = q_roll * q_pitch * q_yaw`) as
`oyster_enrichment.quaternion_utils.euler_to_quat_xyzw`, with a
pure-stdlib fallback when that module is not on the import path.
Values are bit-for-bit identical (within float64 precision) regardless
of which path is taken.

For the full per-engine convention table (Cyberpunk, GTA V, BeamNG,
Source 2, Unreal, Unity), see
[`oyster-enrichment/docs/COORDINATE_SYSTEMS_GUIDE.md`](../../oyster-enrichment/docs/COORDINATE_SYSTEMS_GUIDE.md).
The Minecraft row in that table matches this page line-for-line.

## Usage

Adapt a Phase 1 bundle into a buyer-spec v1 four-deliverable layout
with one CLI command:

```bash
python -m oyster_agent_runner.cli adapt-buyer-spec \
    --bundle  trajectories/run-001/ \
    --output  out/buyer-spec/run-001/
```

This writes:

```
out/buyer-spec/run-001/
├── action_camera.json   # 20-field per-tick records
├── systeminfo.json      # window geometry stub (1920×1080)
├── gameinfo.json        # JSON-equivalent of gameinfo.xlsx
└── manifest.json        # Phase 1 manifest, verbatim
```

The CLI command surface is implemented in
[`src/oyster_agent_runner/cli.py`](../src/oyster_agent_runner/cli.py)
under the `adapt-buyer-spec` Typer command, which delegates to
`buyer_spec_adapter.adapt_phase1_to_buyer_spec()`. Empty bundles (no
observations) yield an empty `action_camera.json` (`[]`) but still
emit the other three deliverables, so callers can treat the layout as
fixed.
