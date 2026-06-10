---
task_id: S25-backend-stub-fastapi
project: gamedata-pipeline
priority: 1
estimated_minutes: 40
depends_on: []
modifies:
  - backend_stub/main.py
  - backend_stub/__init__.py
  - tests/test_backend_stub.py
executor: qwen3.6-plus
---

## 目标

新建 `backend_stub/` — local FastAPI server for recorder integration testing。Endpoints recorder S13/S16 需要：

1. `POST /api/v1/auth/google/exchange` → { access_token, refresh_token, expires_in } (mock)
2. `POST /api/v1/auth/discord/exchange` → 同上
3. `GET /api/v1/income/today` (Bearer auth) → { date, total_usd, sessions_uploaded, currency: "USD" }
4. `POST /api/v1/upload/signed-url` (Bearer) → { url, expires_at, key }  (mock S3 presigned URL)
5. `POST /api/v1/sessions` (Bearer) → { session_id, status: "received" }

数据存内存 dict（不上 db），重启清空。CORS allow `http://localhost:*`。

## 约束

- FastAPI 0.115+, uvicorn 0.32+ — 加到 requirements-test.txt
- 不真验签 token — accept any Bearer for now
- 不依赖外部服务（无 OAuth provider 真调用、无 S3）
- Port 8500 default; --port flag override

## 验收标准

- [ ] `python3 -m backend_stub.main` 启动 listen :8500
- [ ] `curl localhost:8500/api/v1/income/today -H "Authorization: Bearer foo"` 返回 JSON 200
- [ ] `pytest tests/test_backend_stub.py -v` 全绿（用 httpx.AsyncClient）
- [ ] Black + ruff

## 不要做

- 不连真 db
- 不真验 OAuth
- 不写 Stripe/PayPal 集成（S17 单独）
- 直接 commit 到 branch `feat/S25-backend-stub`
