---
task_id: R01-recorder-iron-law-polish
project: recorder
priority: 2
estimated_minutes: 60
depends_on: []
modifies:
  - bin/recorder_*.py     # window-capture fallback + game-state hard-gate
  - mc-mod/build.gradle   # add 1.21.5+ if Mojang ships
executor: codex-aliyun
---

## 目标 (Howard 2026-05-08, surfaced from real diagnostic on Windows)

收紧 OysterRecorder.exe 的两个 iron-law-soft fallback, 让它产出的 tarball
绝不可能含 fabricated 内容.

## 上下文 — 真 diagnostic 证据

Howard 2026-05-08 14:08 在 Windows 跑 v0.26.0-real-game-state, 发回
diagnostic zip. log 显示:

1. `ffmpeg: full-desktop capture (title unsafe='Minecraft 26.2 Snapshot 6 - 单人游戏')`
   非 ASCII 窗口名让 gdigrab 退到全屏捕获 → 录到了整个桌面 (含其他 app /
   隐私), 而不是只录 Minecraft 窗口. Privacy Policy 写明 "Recordings only
   capture the Minecraft window". 这是隐私层面的 iron-law 违反.

2. `package: no game-state JSONL — using placeholder camera/player fields`
   v0.26.0 卖点是 "REAL game-state via Fabric mod". mod 没装时, recorder
   退到 `[0.0, 64.0, 0.0]` 常数 placeholder, tarball 还照传. 买家拿到的
   action_camera.json 里 camera/player 字段全是假的. iron-law 违反.

## 约束

- 不破坏现有 v0.26.0 已发布 .exe 的 user flow (不要让 Howard 已发出的链接失效).
- macOS / Linux 不可测试 (recorder 是 Windows-only) — 改动后 push 让 CI
  build-recorder-exe.yml 在 windows-latest runner 上验证.
- 不动 mc-mod 的核心代码 (现有 mc-mod 设计正确, 只是 Howard 的 MC snapshot
  版本不在 supported list).

## 验收标准

### A. 窗口捕获 — 不可静默退到全屏
- [ ] 当 ffmpeg 检测到窗口标题非 ASCII, 自动退到 "全屏捕获 + 后置 crop"
  路径: 用已检测到的 `mc_window` 几何 (`x, y, width, height`) 作为
  `-vf crop=W:H:X:Y` 输入, 输出文件就是 MC 窗口范围, 不是整个桌面.
- [ ] log 行从 `full-desktop capture (title unsafe=...)` 改为
  `cropped-desktop capture (title unsafe=...) → crop=W:H:X:Y`.
- [ ] 如果窗口几何检测都失败 (mc_window=None), 直接 hard-fail
  `die("Cannot localize Minecraft window — change MC language to English or update title")`,
  不录, 不出 tarball. 这是 iron-law-honest fail-loud.

### B. game-state JSONL 缺失 — v0.26.0+ 不可静默 placeholder
- [ ] 在 `package` 阶段, 如果版本 >= 0.26.0 且 `game_state.jsonl` 不存在
  或为空, hard-fail 而不是用 placeholder. 错误信息:
  ```
  Real game-state mod not loaded. v0.26.0+ requires the Fabric mod —
  install oyster-recorder-mod-mc<your-version>.jar in your mods folder.
  See https://github.com/howardleegeek/.../releases for available builds.
  Tarball NOT created.
  ```
- [ ] log 行删除 "no game-state JSONL — using placeholder camera/player fields".
- [ ] 如果用户偏要 placeholder 模式 (legitimate reason: 没有 mod 但要测试
  pipeline), 加 `--allow-placeholder` flag, 但 tarball 元数据里写明
  `data_authenticity = 'placeholder'` 让买家明确知道.

### C. 自检
- [ ] 启动时打印 supported MC versions 列表 (从 mc-mod CI artifacts):
  `Supported Minecraft versions for real game-state: 1.20.1, 1.20.4, 1.21.1, 1.21.4`
- [ ] 检测到的 MC 窗口标题如果不在 supported list, 警告但不阻止启动:
  `WARN: Minecraft <X.Y> not on real-game-state supported list. Use --allow-placeholder or downgrade.`

### D. Iron-law lint
- [ ] tests/test_iron_law_no_fake_data.py 加新测试:
  `test_recorder_hard_gates_placeholder_in_v026_plus` —
  patch package() with no JSONL file → assert raises hard-fail.
  no fallback to placeholder.

## 不要做

- ❌ 不要为 snapshot 版本 (Minecraft 26.2 Snapshot 6 等) 编译 mod —
  Mojang snapshot 版本号不稳定, 维护成本高.
- ❌ 不要改 v0.26.0 已发布的 .exe — 这是 v0.27.0 的内容.
- ❌ 不要假设 Windows 编码 — 用 ffmpeg 原生 Unicode 路径, 不要试图修
  Windows codepage.
- ❌ 不要静默数据 — 任何 placeholder / non-real fallback 必须 hard-fail,
  除非 user 显式 `--allow-placeholder`.

## Release path

变更落 main → CI build-recorder-exe.yml 在 windows-latest 编译 →
新 tag `recorder-v0.27.0-iron-law-strict` → GitHub Release with
.exe + .zip + mod jars (4 MC versions) bundled.
