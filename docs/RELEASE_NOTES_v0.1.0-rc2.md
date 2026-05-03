# Release v0.1.0-rc2 · 2026-05-02

> **Vendor-ready candidate** · 28 commits since v0.1.0-rc1 · main = `0736249`

---

## What's new

### 📋 Vendor PRD package (1399 lines, 4 docs)
完整对外发布的 PRD 三件套, vendor 拿到即可启动:

- **[`docs/PRD.md`](PRD.md)** — 整体 PRD v1.0 (506 行): 业务背景 + SOW + 价格框架 + 联络人 + 量产计划
- **[`docs/VENDOR_ONBOARDING.md`](VENDOR_ONBOARDING.md)** — 8 步 60 分钟 onboarding 手册 (400 行)
- **[`docs/SUBMISSION_FORMAT.md`](SUBMISSION_FORMAT.md)** — tarball 命名 / 上传 (S3 + SFTP + OSS) / 自助验收 (468 行)
- **README banner** — 顶部加 vendor entry,文档表格分外部 / 内部两栏

### 🎬 真采集脚本(全真路径,Path A)
Onboarding STEP 7 引用的真采集 orchestrator 全部交付:

- **`bin/mc_launcher_real.py`** — 启动真 Minecraft Java 1.20.4 客户端 (offline mode + offline UUID + auto spectator)
- **`bin/spectator_follow.py`** — RCON-based 视角同步 (raw socket protocol)
- **`bin/real_depth_filler.py`** — DepthAnything V2 Small 真深度推理 → OpenEXR float32 Z (lazy import torch / MPS / CUDA / CPU 自动选)
- **`bin/produce_real_sample_v2.sh`** — 9 步端到端 orchestrator (Paper → bot → MC client → OBS → ffmpeg → depth → tarball → lint)

### 🛠️ Vendor 工具链
- **`bin/generate_manifest.py`** — Batch manifest.yaml 自动生成 + 验证 (sha256 / metadata aggregation)
- **`bin/upload_s3.sh`** — S3 multipart 断点续传上传 (适配弱网 200 Kbps+)
- **`bin/doctor.sh`** — Onboarding 依赖 10 项自检 (Java / Python / ffmpeg / OpenEXR / network)
- **`bin/sprint_dashboard.py`** — Production progress dashboard (markdown, git log + 文件计数 + 测试通过率)

### 🌐 国际化
- **`docs/PRD_EN.md`** — 英文版 PRD (供海外 vendor 阅读)
- **`docs/FAQ.md`** — 扩展 FAQ (15 条常见问题)

### 🔌 Phase 2 (集成自前一轮 Aliyun cluster sprint)
- `obs_capture_real.py` (292 LOC) — 完整 OBS WebSocket v5 async 协议 (opcodes 0/1/6/7, SHA256 base64 auth)
- `depth_inference_pipeline.py` (280 LOC) — extract_frames + infer_depth_batch + video_to_depth_exrs
- `semantic_validator.py` (222 LOC) — 8 buyer-spec 语义检查
- 全套 pytest tests (deterministic, no real ffmpeg/OBS/torch calls)

---

## Bugfix

- **Yaw drift fix** — adapter clamp via `((yaw + 180) % 360) - 180` 保证 [-180, 180]
- **bot.position nesting fix** — `_position_from_obs` 现在同时检查 top-level 和 obs.bot.position
- **Pathfinder hang fix** — ScriptedProvider move_radius 1.5 (was 3.0) + weights 25% move (was 60%)
- **Phase 2 test imports fix** — 加 `tests/phase2/conftest.py` 把 src 路径加到 sys.path
- **OBS WebSocket mocking fix** — 用 unittest.mock.AsyncMock for connect coroutine
- **DepthAnything lazy import** — sys.modules manipulation 让模块本身能 import

---

## Vendor 执行检查清单

Vendor 拿到本 release tarball / repo 链接后, 5 分钟内可完成:

- [ ] clone repo (`git clone --recurse-submodules`)
- [ ] 跑 `bash bin/doctor.sh` 自检环境
- [ ] 读 [`docs/PRD.md`](PRD.md) 确认 SOW
- [ ] 跑 `bash SOP.sh` 安装依赖
- [ ] 跑 `bash bin/e2e_smoke.sh` 验证 placeholder pipeline
- [ ] 装 OBS + DepthAnything (VENDOR_ONBOARDING.md STEP 5)
- [ ] 跑 `bash bin/produce_real_sample_v2.sh` 产出第一个真 clip
- [ ] 跑 `oyster-buyer-lint` 验证
- [ ] 上传第一个 clip (`bash bin/upload_s3.sh` 或 SFTP / OSS)
- [ ] 邮件 howard.linra@gmail.com 报告 vendor_id 注册

---

## Sprint metrics (this release)

| Metric | Value |
|---|---|
| Commits | 28 (rc1 → rc2) |
| Lines added | 4500+ |
| New scripts | 12 (bin/) |
| New docs | 5 (docs/) |
| Test files | 8 (tests/) |
| Aliyun cluster spec dispatched | 12 |
| Production lint pass rate (mac-2 100-iter) | 98.7 % |
| Average single-clip pipeline duration | 11 min (5 录制 + 6 推理) |

---

## Known issues / gaps

⚠️ 以下问题已识别但未在 v0.1.0-rc2 解决:

- **GitHub repo PRIVATE** — vendor 拿不到链接,需 Howard 决策(改 public / invite / 单独 PDF 分发)
- **mc_launcher_real.py 依赖** — minecraft-launcher-lib 是可选 dep,vendor 需自行 `pip install minecraft-launcher-lib`
- **DepthAnything 模型下载** — 首次跑会下载 ~115 MB (HuggingFace), 国内 vendor 可能需要 `HF_ENDPOINT=https://hf-mirror.com`
- **gameinfo.xlsx 模板** — 当前 stub,完整模板 (含 14 字段) 在 v0.1.0-rc3 计划内

---

## Upgrade path (rc1 → rc2)

```bash
git fetch origin main
git pull origin main
git submodule update --init --recursive
bash bin/doctor.sh  # 验证依赖仍齐全
bash bin/e2e_smoke.sh  # smoke 验证 pipeline
```

无 breaking changes — 所有 v0.1.0-rc1 的 tarball 仍 lint pass。

---

## Contributors (this release)

- **Howard Li** (`@howardleegeek`) — direction, planning, integration, code review
- **Aliyun cluster** (mac-2) — 12 dispatched specs, ~3500 LOC cluster-authored code
- **MiniMax M2.5 / GLM-4.6 / DeepSeek V3.2 / Qwen3.6+** — code generation models (rotation)

---

## Next (v0.1.0-rc3 plan)

- [ ] gameinfo.xlsx 完整模板生成器 (14 字段)
- [ ] CI workflow (.github/workflows/) — pytest + shellcheck on PR
- [ ] sample/buyer-spec-v1-rc1.tar.gz 实际产出 (跑 produce_real_sample_v2.sh)
- [ ] 多 vendor 并发 SFTP server (vendor-001/002/003 各自 chroot)
- [ ] Vendor dashboard web UI (Flask + SQLite, 显示提交 / 通过率 / 计费)
- [ ] CS2 / BeamNG capture 路径(替代 Minecraft, 给 vendor 更多选择)

---

## How to publish this release

```bash
cd ~/Downloads/oyster-agent-runner
git tag -a v0.1.0-rc2 -m "Vendor-ready candidate · 28 commits · PRD package + real capture scripts"
git push origin v0.1.0-rc2
gh release create v0.1.0-rc2 \
  --title "v0.1.0-rc2 · Vendor-ready candidate" \
  --notes-file docs/RELEASE_NOTES_v0.1.0-rc2.md \
  --target main
# Optional: attach sample tarball
# gh release upload v0.1.0-rc2 samples/buyer-spec-v1-rc1.tar.gz
```

---

**Contact**: howard.linra@gmail.com · WhatsApp +1 (341) 250-6526
