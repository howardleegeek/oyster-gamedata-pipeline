---
task_id: S61-tester-invite-form
project: gamedata-pipeline
priority: 1
estimated_minutes: 35
depends_on:
  - S25-backend-stub-fastapi
modifies:
  - backend_stub/tester_invite.py
  - backend_stub/main.py
  - bin/send_tester_invite.py
  - tests/test_tester_invite.py
executor: qwen3.6-plus
---

## 目标

让 Howard 几分钟内邀请 5-10 个内测：

1. `backend_stub/tester_invite.py` endpoints:
   - `POST /api/v1/testers/apply` { email, discord_user, why_interested } → { tester_id, status: "pending" }
   - `GET /api/v1/testers` (admin token) → list applicants
   - `POST /api/v1/testers/{id}/approve` (admin) → status="approved" + return signed download URL
   - `POST /api/v1/testers/{id}/reject` (admin) → status="rejected"
2. `bin/send_tester_invite.py` — CLI: takes email → POST /approve → email (mock — just print) link + tester_id
3. Simple in-memory store

## 约束

- 不真发邮件（print URL 给 Howard 手动 send）
- admin token from env var
- email validation regex
- 不收 PII beyond email + discord handle

## 验收

- [ ] `POST /api/v1/testers/apply` returns 200 + tester_id
- [ ] `POST /api/v1/testers/{id}/approve` returns signed URL
- [ ] CLI prints email-ready text
- [ ] `pytest tests/test_tester_invite.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不真 SMTP
- 不存 password
- 直接 commit 到 branch `feat/S61-tester-invite-form`
