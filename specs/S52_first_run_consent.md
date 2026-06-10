---
task_id: S52-first-run-consent
project: gamedata-pipeline
priority: 1
estimated_minutes: 35
depends_on: []
modifies:
  - bin/first_run_consent.py
  - bin/consent_dialog_cli.py
  - tests/test_first_run_consent.py
executor: qwen3.6-plus
---

## 目标

`bin/first_run_consent.py` — 第一次启动 OysterRecorder 时弹 consent dialog (CLI for now, Rust GUI TBD)。用户必须 explicitly approve：

1. 录制屏幕 (录的是什么 — 仅运行 supported games)
2. 上传到 Oyster (服务器位置, 数据保留期)
3. OAuth 通过 Google/Discord (登录目的)
4. 自动更新 (说明)
5. (optional) 匿名 telemetry

状态写到 `~/.oyster/consent.json`:
```json
{
  "version": "v0.5.x",
  "timestamp": "ISO8601",
  "screen_record": true,
  "upload": true,
  "oauth": true,
  "auto_update": true,
  "telemetry": false,
  "user_sig": "sha256(timestamp + version)"
}
```

`bin/consent_dialog_cli.py` — terminal-based dialog (interactive prompts)，每个选项 y/N + 说明。

## 约束

- 默认 telemetry = false
- 不强制 screen_record 同意 → 不同意则 exit
- consent.json 文件存在 → skip dialog
- 不上传 consent 到 backend（local only）

## 验收

- [ ] 首次跑 → 6 个 prompts + 写 consent.json
- [ ] 已有 consent.json → exit fast，不弹
- [ ] reject screen_record → exit 1
- [ ] `pytest tests/test_first_run_consent.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不写 GUI（Rust 那边 dialog 是 follow-up）
- 不发邮件
- 不连真 backend
- 直接 commit 到 branch `feat/S52-first-run-consent`
