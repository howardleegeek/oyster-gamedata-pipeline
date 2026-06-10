---
task_id: S101-sentry-stub-endpoint
priority: 1
estimated_minutes: 25
modifies:
  - backend_stub/sentry_compat.py
  - backend_stub/main.py
  - tests/test_sentry_stub.py
executor: qwen3.6-plus
---

## 目标

Backend stub 接受 Sentry-format crash dumps (Rust panic_handler + sentry_log compat) for S51 crash_reporter integration.

1. `POST /api/sentry/store/` (Sentry SDK 格式) 接受 envelope
2. parse + store in memory (no real db)
3. dedup by stack_hash
4. return 200 + event_id

## 验收

- [ ] envelope POST → 200
- [ ] dedup by stack_hash works
- [ ] `pytest tests/test_sentry_stub.py` 全绿
- [ ] Black + ruff

## 不要做

- 不连真 Sentry
- 不收 PII
- 直接 commit `feat/S101-sentry-stub`
