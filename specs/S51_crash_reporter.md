---
task_id: S51-crash-reporter
project: gamedata-pipeline
priority: 1
estimated_minutes: 40
depends_on:
  - S25-backend-stub-fastapi
modifies:
  - bin/crash_reporter.py
  - backend_stub/crash_dump.py
  - backend_stub/main.py
  - tests/test_crash_reporter.py
executor: qwen3.6-plus
---

## 目标

`bin/crash_reporter.py` (Python local-side daemon) — 监听 OysterRecorder.exe crash signals 写本地 dump，并 (opt-in) 上传到 backend stub。

1. local crash detection:
   - Watch `%LOCALAPPDATA%\OysterRecorder\logs\` for new `crash-*.log` files
   - Parse Rust panic stack trace + OS + recorder version
   - Write summary to `~/.oyster/crashes/`
2. opt-in upload to backend:
   - First crash prompts user: "Send anonymized crash report to help fix? [Y/n]"
   - User choice persisted to `~/.oyster/telemetry.json`
   - Upload via `POST /api/v1/crash/dump` (anon — no PII)
3. backend `backend_stub/crash_dump.py` accepts + persists in memory

## 约束

- 不上传 game session data（只 panic trace + OS + version）
- 不上传 user identifier
- opt-in 一次性记住选择
- 用 watchdog crate equivalent in Python (watchdog package)

## 验收

- [ ] `python3 bin/crash_reporter.py --daemon &` 起 background daemon
- [ ] mock crash file 出现 → daemon detect + parse
- [ ] opt-in y → upload happen (mock backend assert)
- [ ] opt-in n → 0 upload
- [ ] `pytest tests/test_crash_reporter.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不用真 Sentry SDK（commercial dependency）
- 不上传 game data
- 不收集 PII
- 直接 commit 到 branch `feat/S51-crash-reporter`
