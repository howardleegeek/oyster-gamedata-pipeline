---
task_id: S54-telemetry-optin
project: gamedata-pipeline
priority: 1
estimated_minutes: 30
depends_on:
  - S52-first-run-consent
modifies:
  - bin/telemetry.py
  - backend_stub/telemetry.py
  - backend_stub/main.py
  - tests/test_telemetry_optin.py
executor: qwen3.6-plus
---

## 目标

`bin/telemetry.py` — 匿名使用统计（仅 opt-in）。

每天上传 1 次到 backend stub `POST /api/v1/telemetry/daily`：
```json
{
  "anon_id": "sha256(machine_id + os_user)",
  "version": "0.5.3",
  "os": "Windows",
  "sessions_today": 3,
  "uploads_today": 3,
  "total_session_seconds": 5400,
  "crash_today": false,
  "ts": "ISO8601"
}
```

- 用户 opt-in via `~/.oyster/consent.json` → telemetry=true
- 不上传 any game data, file names, paths
- 不上传 IP / 真 user ID
- 网络断 silent skip
- backend 仅返回 200 OK (no payload)

## 约束

- 不收 PII
- 不阻塞 recorder 主线程（async fire-and-forget）
- backend stub 简单 in-memory append
- 测试 mock HTTP

## 验收

- [ ] opt-in true → daily upload happens
- [ ] opt-in false → 0 upload
- [ ] schema 严格符合
- [ ] `pytest tests/test_telemetry_optin.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不上传 PII
- 不收 game session content
- 直接 commit 到 branch `feat/S54-telemetry-optin`
