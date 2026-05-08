## Web Portals — local dev + production launch

Run the **tester** (`:3000`) and **buyer** (`:3001`) portals locally with one command.

| Doc | Use |
|---|---|
| [`LOCAL_DEV.md`](LOCAL_DEV.md) | Prereqs + bootstrap + smoke + troubleshooting |
| [`PRODUCTION_LAUNCH_SOP.md`](PRODUCTION_LAUNCH_SOP.md) | Stage 0–6 operational playbook (clone → live URLs → real users → incident response → release cadence) |
| [`PRODUCTION_GAPS.md`](PRODUCTION_GAPS.md) | Audit of items still requiring credentials/decisions |

---

# oyster-gamedata-pipeline — GameData v1 production line

> **🎯 Vendor / Partner: 直接看 [`docs/PRD.md`](docs/PRD.md) — 完整 PRD,拿到即可报价 + 启动第一批**
>
> | 文档 | 用途 |
> |---|---|
> | [`docs/PRD.md`](docs/PRD.md) | **整体 PRD** — 业务背景 + SOW + 单价方向 + 联络 |
> | [`docs/VENDOR_ONBOARDING.md`](docs/VENDOR_ONBOARDING.md) | **8 步 onboarding** — 60 分钟产出第一个合格 clip |
> | [`docs/SUBMISSION_FORMAT.md`](docs/SUBMISSION_FORMAT.md) | **提交格式** — tarball 命名 / 上传方式 / 自助验收 |
> | [`docs/BUYER_SPEC_V1.md`](docs/BUYER_SPEC_V1.md) | 字段级技术规格(20 字段 schema) |
>
> **报价请发 → howard.linra@gmail.com** · WhatsApp +1 (341) 250-6526

---

## What this is

oyster-agent-runner is a Layer 4 LLM-agent gameplay capture system that records, adapts, and packages player interactions across multiple game environments. It transforms raw gameplay telemetry — events, frame captures, and action sequences — into structured buyer-spec v1 deliverables ready for downstream consumption. The pipeline runs autonomously, enforcing linting, validation, and reproducibility at every stage so that every artifact is traceable and deterministic.

## Quick start (5-line, minipc Windows 11 实测通过)

```bash
git clone --depth 1 https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline
sudo apt-get install -y ffmpeg openjdk-21-jdk libopenexr-dev
pip install -e . OpenEXR Imath openpyxl numpy
python3 bin/sample_tarball_builder.py --output sample.tar.gz   # → 27 MB · lint PASS · 0 issues · < 5 秒
```

**Cross-platform 验证矩阵**(都是真跑过,不是 demo):

| 环境 | Python | sample 大小 | lint 结果 |
|---|---|---|---|
| macOS 26.3 (mac-1) | 3.14 | 28.2 MB | **0 issues, PASS=True** ✅ |
| Windows 11 + WSL2 Ubuntu 22.04 (minipc) | 3.10.12 | 26.9 MB | **0 issues, PASS=True** ✅ |

最新 release: [**v0.1.0-rc8**](https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/tag/v0.1.0-rc8) — 真 Paper + Mineflayer + adapter E2E 在 minipc Windows 实测通过。

## 🔬 真 E2E 一行验证(rc8 新增)

```bash
bash bin/integration_test_minipc.sh                              # 30s smoke
DURATION_MS=300000 bash bin/integration_test_minipc.sh           # 5 min vendor production
```

**实测结果**:启 Paper Minecraft 1.20.4 server (16s) + Mineflayer ScriptedProvider 30s 捕获 **628 真实 events** + buyer_spec_adapter 转换 **628 PDF-spec records** + 5-file tarball + lint。30s smoke 有 3 expected fails(短样本约束),5 min vendor run 通过。

> **vendor 真采集流程**: `bash bin/produce_real_sample_v2.sh` 用真 Minecraft Java 1.20.4 + OBS Studio + DepthAnything V2 (见 [`docs/VENDOR_ONBOARDING.md`](docs/VENDOR_ONBOARDING.md) STEP 7)。`bin/sample_tarball_builder.py` 是 schema 演示工具,`bin/integration_test_minipc.sh` 是 E2E 集成验证。

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

### For external partners / vendors (start here)

| Document | Description |
|----------|-------------|
| [docs/PRD.md](docs/PRD.md) | **整体 PRD v1.0** — 业务背景 + SOW + 单价方向 + 联络人 |
| [docs/VENDOR_ONBOARDING.md](docs/VENDOR_ONBOARDING.md) | **8 步 vendor onboarding** — 60 分钟产出第一个合格 clip |
| [docs/SUBMISSION_FORMAT.md](docs/SUBMISSION_FORMAT.md) | **tarball 命名 / 上传方式 / 自助验收** |
| [docs/BUYER_SPEC_V1.md](docs/BUYER_SPEC_V1.md) | 字段级技术规格(20 字段 schema + acceptance gates) |

### For internal contributors

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
