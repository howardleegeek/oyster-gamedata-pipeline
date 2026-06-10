---
task_id: S71-e2e-recorder-backend-audit
project: gamedata-pipeline
priority: 1
estimated_minutes: 40
depends_on:
  - S25-backend-stub-fastapi
  - S27-recorder-local-smoke
modifies:
  - bin/e2e_recorder_backend_audit.py
  - tests/test_e2e_recorder_backend_audit.py
executor: qwen3.6-plus
---

## 目标

`bin/e2e_recorder_backend_audit.py` — single-script end-to-end smoke:

1. start `backend_stub` (port 8500) in subprocess
2. wait until ready (`GET /healthz` returns 200, max 5s)
3. run `bin/generate_session_fixture.py --output /tmp/e2e_session`
4. run `bin/recorder_local_smoke.py --backend-url http://localhost:8500 --session /tmp/e2e_session`
5. run `bin/end_to_end_gate_smoke.py /tmp/e2e_session --strict-buyer`
6. assert: gate verdict = BUYER_READY OR STRICT_GATES_PASS_SYNTHETIC (acceptable since fixture is synthetic)
7. assert: backend_stub received 1 session upload
8. shutdown backend_stub gracefully
9. exit 0 if all pass, 1 if any fail

## 约束

- subprocess.Popen for backend
- 30s total timeout
- cleanup on exit (kill backend even on failure)
- pytest fixture for ready-check polling

## 验收

- [ ] `python3 bin/e2e_recorder_backend_audit.py` exit 0 within 30s
- [ ] backend started + stopped cleanly
- [ ] `pytest tests/test_e2e_recorder_backend_audit.py -v` 全绿 (mock subprocess + HTTP)
- [ ] Black + ruff

## 不要做

- 不真启 OBS / Rust recorder（mock 即可）
- 不上传真数据到外部
- 直接 commit 到 branch `feat/S71-e2e-smoke`
