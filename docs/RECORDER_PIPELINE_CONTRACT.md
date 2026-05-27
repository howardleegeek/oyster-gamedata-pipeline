# Recorder Pipeline Contract

This repo is the integration hub between two real systems:

1. **OysterRecorder** (`vendor/recorder/` and the Windows installer release path) captures screen, input, logs, and raw session folders.
2. **oyster-gamedata-pipeline** normalizes those sessions into buyer-spec bundles, runs gates, signs provenance, and uploads/verifies distribution.

The product works only if both sides agree on the session bundle surface. The authoritative code contract is `src/oyster_agent_runner/session_contract.py`.
The capture-vs-processing split is codified in
`src/oyster_agent_runner/capture_architecture.py`: clients capture raw evidence;
servers generate linear depth, OpenEXR, alternate depth encodings, and
buyer-specific packages.

## Version Alignment

There are two version tracks and they must not be confused:

| Component | Current release track | Current source track |
|---|---|---|
| `oyster-gamedata-pipeline` | Latest GitHub release remains `v0.13.2`; it carries the public Windows installer asset and checksum. | `main` continues from `v0.13.2` with architecture/CI contract fixes. Use `main` for development. |
| `gamedata-recorder` | Latest recorder release remains `v2.6.0`; the current public installer asset is `OysterRecorder-Setup-v0.13.2.exe`, rebuilt from `cddcad6` through the R05E bundled-installer pipeline. | `vendor/recorder` is pinned to release-buildable commit `e171f20` (`release/v2.6.0-buildable`), based on the last successful Windows-build source plus no-popup runtime guards and detected-game HWND stabilization. Arbitrary recorder `main` changes are not consumer-release source until tray/auth/updater/notify/libobs changes compile and pass Windows installer smoke. |

Operationally: distribute from the latest verified release, rebuild from the
release-buildable source pin, and keep newer recorder `main` work behind a
promotion gate. A new consumer release should only be cut after the recorder
source pin has passed Windows build/installer smoke.

Release assets have their own contract in
[`docs/RELEASE_CHANNELS.md`](RELEASE_CHANNELS.md). The short rule is: `v0.x`
consumer releases feed the public appcast, while the rc19 bundled recorder line
is a QA/reference fallback until it is rebuilt and promoted through the normal
consumer installer gates.

## Layouts

| Layout | Owner | Required paths | Consumer |
|---|---|---|---|
| `lem` | Windows recorder | `recordings/main_record.mp4`, `streams/states.jsonl` | `bin/real_session_validator.py` maps it into the legacy validation view. |
| `legacy_pipeline` | Python pipeline / game adapters | `recording.mp4`, `game_state.jsonl` | `bin/canonical_pipeline.py`, sync/input/quality gates, provenance scripts. |
| `phase1_agent` | Mineflayer/agent capture path | `manifest.json`, `cot.jsonl`, `metadata.jsonl`, `inputs.jsonl` | `src/oyster_agent_runner/buyer_spec_adapter.py`. |
| `buyer_prd` | Final buyer package | `video.mp4`, `systeminfo.json`, `action_camera.json`, `gameinfo.xlsx`, `depth/` | Vendors, buyers, final audits, manifests. |

## Flow

```text
Windows recorder raw session
  lem/
    recordings/main_record.mp4
    streams/states.jsonl
    streams/actions.jsonl
        |
        v
temporary validation view
  legacy_pipeline/
    recording.mp4
    game_state.jsonl
    inputs.jsonl
        |
        v
canonical pipeline + G-gates
        |
        v
buyer_prd/
  video.mp4
  systeminfo.json
  action_camera.json
  gameinfo.xlsx
  depth/*.exr
```

## Raw Capture / Server Postprocess Rule

The Windows client and Minecraft mod must stay on the raw-capture side of the
boundary. They may record video, camera/game/input telemetry, timestamps,
manifests, and optionally raw non-linear depth buffers. They must not perform
client-side linear depth conversion, OpenEXR float32 generation, or buyer-specific
depth packaging on normal tester machines.

Those heavier steps belong to the server post-processing tier. This lowers crash
risk across mixed AMD/NVIDIA GPUs, driver versions, missing dependencies, and low
VRAM contributor machines. It also lets us support different buyer depth specs
without rebuilding the recorder.

See [`docs/RAW_CAPTURE_POSTPROCESS_ARCHITECTURE.md`](RAW_CAPTURE_POSTPROCESS_ARCHITECTURE.md)
for the current Minecraft POC decision.

## Promotion Rule For New Games

A game profile can move to `smoke_ready` only after it emits either:

- a complete `legacy_pipeline` session, or
- a complete `lem` session that maps cleanly into `legacy_pipeline`.

It can move to `production` only after a clean Windows run proves:

1. latest GitHub release has the installer and `SHA256SUMS.txt`;
2. backend health and `/api/v1/updates/appcast.xml` are green;
3. real install, tray launch, gameplay capture, upload, validation, and uninstall pass on a clean Windows machine;
4. final output satisfies the `buyer_prd` contract.

## Current Architecture Read

The release/distribution chain is real: latest verified public release is
`v0.13.2`, the installer asset and SHA file exist, backend health/appcast are
live, and smoke workflows are green. The Windows installer is built from the
release-buildable source pin plus the verified x64 recorder runtime, with PE
architecture guards to prevent ARM64 DLLs from entering the setup payload. The
remaining production risk is
release/source drift plus data-contract drift while expanding from Minecraft
into BeamNG, Factorio, Stardew Valley, and other single-player games.

Keep new game work thin:

1. add or update `src/oyster_agent_runner/game_plugins.py`;
2. implement the adapter under `src/oyster_agent_runner/environments/`;
3. prove it satisfies `session_contract.py`;
4. run targeted adapter tests plus real-session validator smoke.
