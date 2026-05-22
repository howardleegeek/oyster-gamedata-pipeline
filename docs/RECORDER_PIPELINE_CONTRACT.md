# Recorder Pipeline Contract

This repo is the integration hub between two real systems:

1. **OysterRecorder** (`vendor/recorder/` and the Windows installer release path) captures screen, input, logs, and raw session folders.
2. **oyster-gamedata-pipeline** normalizes those sessions into buyer-spec bundles, runs gates, signs provenance, and uploads/verifies distribution.

The product works only if both sides agree on the session bundle surface. The authoritative code contract is `src/oyster_agent_runner/session_contract.py`.

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

The release/distribution chain is now real: latest build is `v0.10.0`, the installer asset and SHA file exist, backend health/appcast are live, and smoke workflows are green. The remaining production risk is not basic shipping; it is data-contract drift while expanding from Minecraft into BeamNG, Factorio, Stardew Valley, and other single-player games.

Keep new game work thin:

1. add or update `src/oyster_agent_runner/game_plugins.py`;
2. implement the adapter under `src/oyster_agent_runner/environments/`;
3. prove it satisfies `session_contract.py`;
4. run targeted adapter tests plus real-session validator smoke.
