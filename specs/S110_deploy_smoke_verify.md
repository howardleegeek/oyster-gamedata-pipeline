---
task_id: S110-deploy-smoke-verify
priority: 1
estimated_minutes: 25
modifies:
  - scripts/verify_deployed_backend.py
  - tests/test_verify_deployed_backend.py
executor: qwen3.6-plus
---

## 目标

After Howard runs `flyctl deploy backend_stub/`, run smoke against the deployed URL.

`scripts/verify_deployed_backend.py --url https://oyster-backend-stub.fly.dev` calls:
- GET /healthz → 200
- POST /api/v1/testers/apply { email: smoke@test.com } → 200 + tester_id
- GET /api/v1/income/today (Bearer mock) → 200 + JSON schema valid
- GET /api/v1/updates/appcast.xml → 200 + XML valid

Exit 0 if all pass, 1 if any fail with details.

## 验收

- [ ] CLI accepts --url
- [ ] all 4 endpoints validated
- [ ] verbose mode shows each step
- [ ] `pytest tests/test_verify_deployed_backend.py` 全绿 (mock httpx)
- [ ] Black + ruff

## 不要做

- 不真 hit prod URL
- 直接 commit `feat/S110-deploy-smoke`
