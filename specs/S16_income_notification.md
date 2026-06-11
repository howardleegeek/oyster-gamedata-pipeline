---
task_id: S16-income-notification
project: gamedata-pipeline
priority: 2
estimated_minutes: 40
depends_on:
  - S12-recorder-egui-to-tray
  - S13-oauth-google-discord-pkce
modifies:
  - vendor/recorder/src/notify/mod.rs
  - vendor/recorder/src/notify/income_poller.rs
  - vendor/recorder/Cargo.toml
executor: qwen3.6-plus
---

## 目标

每日定时（晚上 8 pm 本地时间）：
1. 调用 backend `GET /api/v1/income/today` (Bearer = OAuth token)
2. 收到 income (USD) → tray 弹气泡通知 "今天 $X，已上传 N session" (3 秒消失)
3. notification 用 `notify-rust` 的 Windows toast / macOS NSUserNotification

如果 income > $0 且这是用户第一次收钱（onboard signal），弹 onboard "💰 第一笔到账！点击查看 dashboard"

## 约束

- 不依赖任何 GUI（tray + native notification only）
- 不在前台 fetch — async background tokio task
- 5G/限速时不要 retry 死循环（exponential backoff，最多 3 次）
- 用户离线时不通知（先检查 net availability）

## 验收标准

- [ ] `cargo build --release` 全绿
- [ ] `cargo test --features mock-notify` 通过
- [ ] mock backend 返回 $X → notification 包含 "$X"
- [ ] backend 不可达不报错，下次再试
- [ ] tests/test_income_poller.py 集成测试（mock HTTP）

## 不要做

- 不写 backend endpoint（那是 backend 仓库）
- 不弹弹窗（只用 tray 气泡）
- 不写 sound — 静默
- 直接 commit 到 branch `feat/S16-income-notification`
