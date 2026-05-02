# oyster-agent-runner — Production buyer-spec v1 pipeline

## What this is

oyster-agent-runner is a Layer 4 LLM-agent gameplay capture system that records, adapts, and packages player interactions across multiple game environments. It transforms raw gameplay telemetry — events, frame captures, and action sequences — into structured buyer-spec v1 deliverables ready for downstream consumption. The pipeline runs autonomously, enforcing linting, validation, and reproducibility at every stage so that every artifact is traceable and deterministic.

## Quick start

```bash
git clone https://github.com/howardleegeek/oyster-gamedata-pipeline
cd oyster-agent-runner
bash SOP.sh
```

That's it — the SOP orchestrator will bootstrap the environment, run the full capture pipeline, and emit buyer-spec v1 artifacts to `./output/`. No additional configuration is required for the default Minecraft profile.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     oyster-agent-runner                         │
├─────────────┬─────────────┬──────────────┬──────────────────────┤
│   CAPTURE   │   ADAPTER   │     LINT     │        PACK          │
│             │             │              │                      │
│  Raw game   │  Normalize   │  Validate    │  Bundle buyer-spec   │
│  telemetry  │  to v1 spec  │  schema &    │  v1 deliverables     │
│  (events,   │  format &    │  semantics   │  (tar.gz + manifest) │
│  frames,    │  enrich with │  checks      │                      │
│  actions)   │  metadata    │              │                      │
└──────┬──────┴──────┬──────┴──────┬───────┴──────────┬───────────┘
       │             │             │                  │
       ▼             ▼             ▼                  ▼
  .raw/          .adapted/     .lint/            ./output/
  (per-game)     (v1 JSON)     (reports)         (artifacts)
```

**Data flow:**

1. **Capture** ingests live gameplay telemetry from the target game process
2. **Adapter** normalizes raw data into the buyer-spec v1 JSON schema with enriched metadata
3. **Lint** validates structure, required fields, semantic constraints, and cross-reference integrity
4. **Pack** bundles final deliverables as compressed archives with SHA-256 checksums and a machine-readable manifest

## Three games supported

| Game | Status | Notes |
|------|--------|-------|
| **Minecraft** | ✅ Stable | Full capture pipeline operational; 100-iter validation passing at 98.7% |
| **BeamNG.drive** | 🟡 Beta | Adapter complete; lint rules in progress — see [BEAMNG_RUNBOOK.md](BEAMNG_RUNBOOK.md) |
| **Counter-Strike 2** | 🟡 Beta | Capture prototype running; adapter schema pending final review |

## Status

| Metric | Value |
|--------|-------|
| Validation pass rate (100-iter) | **98.7%** |
| Pipeline stages passing | 4 / 4 |
| Games in production | 1 / 3 |
| Mean pipeline duration | ~4.2 min per run |

📊 Full sprint metrics and historical trends: [SPRINT_REPORT](SPRINT_REPORT.md)

## Documentation

| Document | Description |
|----------|-------------|
| [SOP.md](SOP.md) | Standard operating procedure — end-to-end pipeline walkthrough |
| [QUICKSTART.md](QUICKSTART.md) | 5-minute setup guide for new contributors |
| [PRODUCTION_LINE.md](PRODUCTION_LINE.md) | Deep dive into each pipeline stage: capture → adapter → lint → pack |
| [BEAMNG_RUNBOOK.md](BEAMNG_RUNBOOK.md) | BeamNG.drive specific configuration, known issues, and troubleshooting |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | Changelog, version history, and migration notes |

## Contributing

All development happens on the `main` branch. Open a PR for any pipeline changes. Run `bash SOP.sh --dry-run` before committing to verify your changes pass lint and validation.

## Requirements

- Python 3.10+
- Bash 5.0+
- 8 GB RAM minimum (16 GB recommended for BeamNG)
- Network access to game telemetry endpoints

## License

**Internal Oyster Labs** — Proprietary and confidential. Not for external distribution.

© 2025 Oyster Labs. All rights reserved.

## GameData ecosystem (vendored as submodules)

This repo is the integration hub for the full GameData product line. Sister repos pinned as submodules:

| Path | Source repo | Role |
|---|---|---|
| `vendor/recorder/` | [gamedata-recorder](https://github.com/howardleegeek/gamedata-recorder) | Windows screen + input capture (Rust, OWL-Control fork) |
| `vendor/input-logger/` | [gamedata-input-logger](https://github.com/howardleegeek/gamedata-input-logger) | High-precision keyboard/mouse/gamepad logger |
| `vendor/enrichment/` | [oyster-enrichment](https://github.com/howardleegeek/oyster-enrichment) | Layer 1 ML enrichment (MASt3R-SLAM + UniDepth V2) + buyer-spec linter |

To clone with everything:
```bash
git clone --recursive https://github.com/howardleegeek/oyster-gamedata-pipeline
```

Or after a non-recursive clone:
```bash
git submodule update --init --recursive
```
