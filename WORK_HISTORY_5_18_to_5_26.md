# Work History — Oyster GameData Recorder (5/18 → 5/26, 8 days)

*Howard's "梳理之前的 work" — Claude + Codex 协同。*

---

## 时间轴 (release tags as proxy for milestones)

| Date | Tags shipped | Lead agent | Main contribution |
|------|--------------|-----------|-------------------|
| **5/18 (Wed)** | v0.4.0, v0.4.1 (hotfix) | Claude | Audit gates H1-H8 + buyer-spec audit (synthetic data) |
| **5/19 (Thu)** | v0.4.2-specs, v0.5.0, v0.5.1, v0.5.2, v0.5.3 | Claude | Wave 1-5 (cluster: 27 specs, daemons, CI, Rust submodule integration) |
| **5/20 (Fri)** | v0.5.4 | Claude→Codex handoff | Wave 6-13: 内测 readiness specs, partial release |
| **5/21 (Sat)** | v0.6.0, v0.6.1, v0.6.2, v0.6.3, v0.7.0, v0.7.1, v0.7.2, v0.8.0, v0.8.1, **v0.8.2 first .exe** | Claude finale + Codex start | Claude: CI layer-peels 1-8 → first real downloadable .exe |
| **5/22 (Sun)** | v0.8.x → **v0.11.0** (15+ tags) | Codex | Bundled installer infra, OysterPlay launcher, recorder pipeline contract |
| **5/23 (Mon)** | v0.11.1 | Codex (archived) | Stabilization |
| **5/24 (Tue)** | v0.11.2 → v0.11.8 (7 tags) | Codex | Production readiness gate, release channels, real-session smoke |
| **5/25 (Wed)** | v0.11.9 → **v0.11.17** (9 tags) | Codex | Auto-release sync, anchor drift guard, asset verification |
| **5/26 (Thu, 今天)** | v0.11.18 + recorder-v0.28.0-rc19.0.4 (in-progress) | Codex (Howard active) | Launcher-gate bug fix, OysterPlay default launch |

**Total: 40+ tags shipped in 8 days. ~30 are actual feature/fix tags (some are skip-ci anchors).**

---

## Two-agent contribution map

### Claude (我, 5/18–5/21)
- ✅ Built audit framework (8 ISC criteria, H1-H8 gates, strict-buyer mode)
- ✅ Cluster dispatch infra (qwen3.6-plus, 27 specs across Wave 1-13)
- ✅ Iron-law-gate + auto-merge + auto-tag bot (CI workflows)
- ✅ Real CI layer-peeling (8 layers from fake action → real .exe)
- ✅ First downloadable artifact: v0.8.2 with `OysterRecorder-setup-v2.6.0.exe` (10.9 MB)
- ✅ Partner Brief documents (v0.4.1, v0.8.2)
- 🟡 Pinned recorder submodule to v2.6.0 (sidestepped cluster's broken Rust modules)

### Codex (5/22–5/26)
- ✅ Bundled installer infra (Inno + MC + JRE bundled into single 30 MB .exe)
- ✅ OysterPlay launcher app (auto-launches MC, arms recorder)
- ✅ Production readiness gate (scripts/production_readiness_gate.py)
- ✅ Release channels framework (src/oyster_agent_runner/release_channels.py)
- ✅ Anchor drift guard (scripts/verify_latest_release_assets.sh)
- ✅ Auto-release source sync (scripts/auto_release.sh + tests)
- ✅ Real-session smoke (scripts/windows_real_session_smoke.ps1)
- ✅ Appcast server v2 (backend_stub/appcast_server.py with 16 commits)
- 🟡 v0.11.18 has Launcher-detection bug (recorder catches MinecraftLauncher.exe as game)
- 🟡 rc19.0.4 in-flight fix for above bug (Build Recorder EXE in_progress 14m+)

---

## Top 15 hot files (joint authorship, last 7 days)

| Commits | File | Owner |
|---------|------|-------|
| 39 | `CHANGELOG.md` | both (auto-updated) |
| 20 | `.github/workflows/build-recorder-windows.yml` | Claude (CI layer peels) + Codex (refinement) |
| 17 | `tests/test_windows_real_session_smoke.py` | Codex |
| 16 | `tests/test_appcast_server.py` | Codex (v2) |
| 16 | `docs/RECORDER_PIPELINE_CONTRACT.md` | Codex |
| 16 | `backend_stub/main.py` | both |
| 15 | `tests/test_component_version_alignment.py` | Codex |
| 15 | `backend_stub/appcast_server.py` | Codex |
| 14 | `tests/test_release_channels.py` | Codex |
| 13 | `tests/test_production_readiness_gate.py` | Codex |
| 13 | `src/oyster_agent_runner/release_channels.py` | Codex |
| 13 | `scripts/windows_real_session_smoke.ps1` | Codex |
| 13 | `docs/RELEASE_CHANNELS.md` | Codex |
| 11 | `vendor/recorder` (submodule pointer) | both |
| 10 | `tests/test_verify_deployed_backend.py` | Codex |

---

## Verified-working releases by download adoption

| Tag | Date | Size | Downloads | Notes |
|-----|------|------|-----------|-------|
| v0.11.10 | 5/25 | 26.5 MB | **18** ⭐ | Highest adoption — production readiness gate added |
| v0.11.17 | 5/26 | 26.5 MB | 14 | Pre-bundled-launcher fix |
| v0.11.9 | 5/25 | 26.5 MB | 12 | Initial consumer anchor |
| **v0.11.18** | 5/26 | **30 MB** | 9 | Latest, x64 bundled, has Launcher-detect bug |
| v0.11.15 | 5/26 | 26.5 MB | 8 | |

**v0.11.10 = 内测 friend's likely test version** (18 downloads, most adopted yesterday, has production readiness gate).

---

## 当前问题 (5/26 daytime)

**Bug**: recorder把 `MinecraftLauncher.exe` 当成 Minecraft 游戏，提前生成 session 录到 launcher 界面，真正进入游戏窗口后已经停了。

**Codex 的修复 (rc19.0.4)**:
1. detect_minecraft 排除 Launcher (`MinecraftLauncher.exe` + 标题 "启动器")
2. 进程身份检查 (`javaw.exe`/`java.exe`/`Minecraft.exe`)
3. OysterPlay 自动启动 bundled MC + 自动 arm recorder
4. 安装完成后默认勾选启动 OysterPlay
5. 增加回归测试 (test_bundled_installer_contract.py)

**Status**: tag `recorder-v0.28.0-rc19.0.4` pushed 1 hour ago. 5 workflows triggered, 4 failed (likely YAML config issues), `Build Recorder EXE` still in_progress 14m+.

---

## Claude + Codex 协同模式 (建议)

### 现状
- Claude (sonnet 4.6, 我) = 总调度 + cluster dispatch + Real-CI peel
- Codex = 副元帅, 大量 stabilization + 测试

### 提议分工 (forward)

| 责任 | Claude | Codex |
|------|--------|-------|
| Strategic spec writing | ✅ (大方向, ISC) | partial (小修) |
| Cluster dispatch (Aliyun) | ✅ (qwen3.6-plus 主战场) | 不碰 |
| Real-CI failure diagnosis | ✅ (real CI layer-peel pattern) | partial |
| Local Rust/PowerShell edit | 不碰 (per Howard 5/26 clarification) | ✅ |
| Tag/release management | shared (auto-tag bot) | shared |
| Test writing | dispatch via cluster | direct write |
| Production readiness gates | spec-only | implementation |
| 应急 hotfix | spec → cluster | direct edit if urgent |

### 防冲突机制
1. **handoff.md** (in ~/Downloads/claude_share/) — 任一方动 main 前更新
2. **progress.log** — 时间戳记录 active workstream
3. **同 PR / 同 branch 避免重叠** — Codex 用 `fix/codex-*`, Claude 用 `feat/cluster-*` 命名前缀
4. **Iron law for main**: 任一方 push main 前需检查另一方是否有 pending PR

---

## 下一步选项 (Howard 决定)

1. **等 rc19.0.4 build 完** (~5 min ETA) → 出新 bundled link 给测试员
2. **现在用 v0.11.18 link** (有 Launcher bug 但已稳, 30 MB)
3. **回滚到 v0.11.10** (18 downloads, 已 verified 但 26.5 MB — 没有今天的 fix)
4. **梳理后让 Codex 继续修 rc19.0.4** + 我做 monitor/release management

---

🦪 Joint document — Claude + Codex coordination
2026-05-26 PT
