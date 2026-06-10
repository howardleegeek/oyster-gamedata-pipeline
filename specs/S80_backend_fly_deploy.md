---
task_id: S80-backend-fly-deploy
priority: 1
estimated_minutes: 30
modifies:
  - backend_stub/fly.toml
  - backend_stub/Dockerfile
  - scripts/deploy_backend.sh
  - tests/test_deploy_script.py
executor: qwen3.6-plus
---

## 目标

`backend_stub/` deployable to Fly.io with one command.

1. `backend_stub/Dockerfile`: python:3.12-slim + pip install fastapi uvicorn + CMD uvicorn main:app
2. `backend_stub/fly.toml`: app name `oyster-backend-stub`, region `iad`, port 8080
3. `scripts/deploy_backend.sh`: `flyctl deploy backend_stub/`
4. tests: validate Dockerfile + fly.toml syntax

## 验收

- [ ] Dockerfile builds (mock with `docker build --dry-run`)
- [ ] fly.toml valid TOML
- [ ] `pytest tests/test_deploy_script.py` 全绿
- [ ] Black + ruff + shellcheck

## 不要做

- 不真 `fly deploy`（Howard 跑）
- 不存 fly token in repo
- 直接 commit 到 branch `feat/S80-backend-fly-deploy`
