---
task_id: S83-recorder-backend-url-config
priority: 1
estimated_minutes: 20
modifies:
  - bin/recorder_config.py
  - bin/recorder_local_smoke.py
  - tests/test_recorder_config.py
executor: qwen3.6-plus
---

## 目标

`bin/recorder_config.py` — central config loader. Reads `~/.oyster/config.json`:

```json
{
  "backend_url": "https://oyster-backend-stub.fly.dev",
  "discord_webhook": "https://discord.com/api/webhooks/...",
  "update_server": "https://updates.oyster.example",
  "auto_update_check_hours": 24,
  "income_notification_time": "20:00",
  "telemetry_enabled": false
}
```

Modify `bin/recorder_local_smoke.py` + crash_reporter + bug_report 调 `recorder_config.load()` 而非 hardcode URL.

Default config copy to `installer/default_config.json` (installer 写到用户 dir).

## 验收

- [ ] `recorder_config.load()` returns dict with required keys
- [ ] missing config file → loads from default
- [ ] env var override (`OYSTER_BACKEND_URL`)
- [ ] `pytest tests/test_recorder_config.py` 全绿

## 不要做

- 不存 OAuth tokens in config.json (separate file)
- 不 hardcode prod URL
- 直接 commit 到 branch `feat/S83-recorder-config`
