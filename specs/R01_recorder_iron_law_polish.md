---
task_id: R01-recorder-iron-law-polish
project: recorder
priority: 1
estimated_minutes: 90
depends_on: []
modifies:
  - bin/recorder_*.py                      # capture path + game-state hard-gate
  - mc-mod/build.gradle                    # multi-MC-version build matrix
  - mc-mod/gradle.properties               # parameterize per-version build
  - .github/workflows/build-mc-mod.yml     # expand matrix to 10+ versions
  - tests/test_iron_law_no_fake_data.py    # add hard-gate test
executor: codex-aliyun
iron_law_waived: "Meta-spec defining the iron law itself — must reference the banned terms to specify what gets hard-failed at the recorder package layer."
---

## 目标 (Howard 2026-05-08, sharpened from v1)

让 OysterRecorder.exe 满足三条铁律, 任何一条违反都 hard-fail, 绝不静默
fallback 到假数据 / 桌面隐私泄漏 / 缩减语言支持:

1. **数据真玩意儿** — 任何场景下绝不出 placeholder 数据.
   missing game-state JSONL → hard-fail, 不出 tarball.

2. **多语言原生支持** — 中英日韩俄阿任何 locale 的 MC 窗口标题都必须能录
   且**只录 MC 窗口**. 不允许让 user 改语言到 English 来 "解决" 问题.

3. **多版本兼容** — Fabric mod 必须支持 Mojang 已 ship 的所有 stable
   版本 (1.20.x family + 1.21.x family + 后续), 加上一条 "best-effort
   snapshot" lane.

## 上下文 — 真 diagnostic 证据 (Howard 在 Windows 跑 v0.26.0)

```
ffmpeg: full-desktop capture (title unsafe='Minecraft 26.2 Snapshot 6 - 单人游戏')
package: no game-state JSONL — using placeholder camera/player fields
```

第 1 行: 中文 "单人游戏" 让 ffmpeg gdigrab 退到全屏捕获. **录到了整个桌面**,
包括其他 app, 浏览器, 私人内容. Privacy Policy 写明 "Recordings only capture
the Minecraft window" — 已经被 silent fallback 给违反.

第 2 行: 没装 Fabric mod, 但 recorder 还照样出 tarball, 里面 camera/player
全是常数 `[0.0, 64.0, 0.0]`. Sells "real" but ships fake.

## 约束

- 不破坏现有 v0.26.0 已发布 `.exe` 的下载链接 (R01 = v0.27.0 release).
- mac/Linux 不能本地验证 Windows 行为 — 改动后必须等 CI
  `build-recorder-exe.yml` 在 windows-latest runner 上 build + smoke 才合并.
- 不让 user 做任何 "改语言 / 换版本" 的妥协.

## 验收标准

### A. 窗口捕获 (locale-blind, primary path 切换)

**老逻辑 (R01 v1, 已废弃):** title-based gdigrab + cropped-desktop fallback.
**新逻辑 (R01 v2, 必须):** **永远使用** cropped-desktop capture +
`-vf crop=W:H:X:Y`, 用已经成功检测到的 `mc_window` 几何. title 编码无关.

- [ ] `mc_window` 检测仍在 — Win32 API `EnumWindows` + window-class
  match (`GLFW30` for Minecraft Java) 是稳定的, 不依赖 title 字符串内容.
- [ ] 检测到 mc_window → ffmpeg 永远调:
  ```
  ffmpeg -f gdigrab -framerate 60 -offset_x <x> -offset_y <y> \
         -video_size <w>x<h> -i desktop -c:v libx265 ...
  ```
  这是 ffmpeg 原生 supported 的窗口区域捕获方式, 完全 locale-blind.
- [ ] 删除 "title unsafe" 分支 — 没有 fallback path, 也没有 "如果 title
  非 ASCII 就 X". title 编码完全不参与决策.
- [ ] log 行: `ffmpeg: window-area capture title='<raw>' geometry=<x,y,w,h>`
  (raw title 直接打, 编码不再是逻辑分支).
- [ ] 如果 `mc_window=None` (没检测到窗口), hard-fail:
  `die("Minecraft window not detected. Is Minecraft running and visible?")`.
  不录, 不出 tarball. 不再让 user "改语言".

### B. game-state JSONL — v0.26.0+ 必须真 mod 数据

- [ ] `package` 阶段, 如果运行的 .exe version >= 0.26.0 且
  `~/Documents/OysterClips/active_session/game_state.jsonl` 不存在或 0 字节,
  hard-fail with this exact message:
  ```
  Real game-state Fabric mod not loaded.
  Detected MC version: <X.Y.Z>
  Supported mod builds:  1.20.1, 1.20.2, 1.20.4, 1.20.6,
                         1.21.1, 1.21.2, 1.21.3, 1.21.4, 1.21.5
  Download from:        https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/latest
  Install path:         %APPDATA%\.minecraft\mods\
  Tarball NOT created.
  ```
- [ ] 删除 "no game-state JSONL — using placeholder camera/player fields" 这一行
  和它对应的 placeholder 写入逻辑.
- [ ] `--allow-placeholder` flag 仍可用, 但启用时 tarball metadata 必加:
  ```json
  { "data_authenticity": "placeholder",
    "warning": "camera/player fields are constant [0.0, 64.0, 0.0]" }
  ```
  并在 tarball README.txt 里 BOLD warning. 买家拿到这种 tarball 必能识别.

### C. 多版本 mod build matrix

`.github/workflows/build-mc-mod.yml` 扩展:

- [ ] Stable matrix (must all green for release):
  ```yaml
  mc_version:
    - "1.20.1"
    - "1.20.2"
    - "1.20.4"
    - "1.20.6"
    - "1.21.1"
    - "1.21.2"
    - "1.21.3"
    - "1.21.4"
    - "1.21.5"   # release if Mojang has shipped by build time
  ```
- [ ] Per-version yarn / fabric-api / loader_version pinning — use a
  matrix.include block to lookup compatible Fabric API per MC version.
  例如 `1.21.4 → fabric_version=0.110.0+1.21.4`.
- [ ] `gradle.properties` 改成 fully parameterized (从 ENV 读 MC_VERSION,
  YARN_MAPPINGS, LOADER_VERSION, FABRIC_VERSION), CI 注入.
- [ ] Snapshot best-effort lane (continue-on-error: true):
  尝试 build 最新 snapshot, 失败不阻塞 release, 但 log 写明
  "snapshot 26.2 not yet supported".
- [ ] mod 文件名: `oyster-recorder-mod-<modver>-mc<mcver>.jar`
  (现有命名约定保持, 不破坏 release links).

### D. 自检 + UX

- [ ] 启动时打印 supported list (动态读自 mc-mod release artifacts):
  ```
  OysterRecorder v0.27.0 — supported Minecraft versions for real game-state:
    1.20.1, 1.20.2, 1.20.4, 1.20.6, 1.21.1, 1.21.2, 1.21.3, 1.21.4, 1.21.5
  ```
- [ ] 检测到 MC 窗口后, 解析 title 中的版本号 (regex
  `Minecraft\s+([\d\.]+)`, 兼容 "Minecraft 1.21.4" / "Minecraft 1.21.4 -
  单人游戏" / "Minecraft 1.21.4 - シングルプレイ" 等所有 locale):
  - 命中 supported → 继续
  - 不命中 → 警告但允许:
    ```
    WARN: Minecraft <X> not in supported list. Real game-state mod
    only loads on stable releases. Recording will hard-fail at packaging
    unless you switch to a supported version OR pass --allow-placeholder.
    ```
- [ ] 如果 user 已经在 record 中途切到 unsupported 版本, packaging 阶段
  hard-fail 仍然兜底 — UX 警告只是 early-warning.

### E. Iron-law lint

`tests/test_iron_law_no_fake_data.py` 加这些 test (Python-side simulating
recorder package logic if possible, OR mark xfail with platform check):

- [ ] `test_recorder_hard_gates_placeholder_in_v026_plus`
  patch package() with `version=0.26.0` and no JSONL → assert
  raises `RecorderError("Real game-state Fabric mod not loaded")`.
- [ ] `test_recorder_allows_placeholder_with_explicit_flag`
  patch package() with `--allow-placeholder` + no JSONL → assert
  tarball is created BUT metadata.json contains
  `data_authenticity == "placeholder"`.
- [ ] `test_recorder_window_capture_uses_geometry_not_title`
  patch ffmpeg invocation builder with non-ASCII title → assert
  resulting cmdline contains `-offset_x ... -video_size ... -i desktop`,
  NOT `-i title=...`.

### F. CI 验证

- [ ] `.github/workflows/build-recorder-exe.yml` 在 windows-latest runner
  build 完后, 新增 smoke step:
  - launch a fake MC window with title containing non-ASCII chars
    (PowerShell: `$host.UI.RawUI.WindowTitle = "Minecraft 1.21.4 - 単人游戏"`)
  - run recorder in headless / scripted mode
  - assert ffmpeg cmdline used cropped-desktop, not title-based
- [ ] mc-mod build matrix 全部 9+ 版本 green 才能发 release.

## 不要做

- ❌ 不要让 user 改 MC 语言到 English. 任何 locale 都必须 work out of the box.
- ❌ 不要 silent fallback 到 placeholder data. 任何 placeholder 必须显式
  flag + 元数据标注.
- ❌ 不要 silent fallback 到 full-desktop capture. 任何捕获范围 != MC
  window 必须 hard-fail.
- ❌ 不要假设 Windows codepage / locale. ffmpeg 用 geometry 不用 title 就行.
- ❌ 不要破坏 v0.26.0 已发布的 .exe / mod jars 下载链接 — R01 = v0.27.0.

## Release path

变更落 main → CI build-recorder-exe.yml + build-mc-mod.yml 全绿 (9+ MC
versions × Windows recorder) → 新 tag `recorder-v0.27.0-iron-law-strict` →
GitHub Release with bundled assets:
  - OysterRecorder.exe (Windows binary, code-signed if cert ready)
  - OysterRecorder-onedir.zip (alternate format)
  - oyster-recorder-mod-0.2.0-mc{1.20.1, 1.20.2, 1.20.4, 1.20.6, 1.21.1,
    1.21.2, 1.21.3, 1.21.4, 1.21.5}.jar (9 mod jars)
  - SHA-256 manifest (one file with all hashes)

## 数据准确铁律 (Howard 2026-05-08, 强调)

执行 agent 必须真在 windows-latest runner 跑 build + smoke 才能合并.
不允许 "代码看着对就推" — Windows-only 行为必须 Windows-real 验证. CI
log 里要看到:
  - `ffmpeg ... -i desktop` 出现在录制命令 (不是 `-i title=...`)
  - mod build matrix 9 个版本全 green
  - iron-law lint 加的 3 个 test 全 pass
