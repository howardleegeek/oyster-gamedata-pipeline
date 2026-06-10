---
task_id: S100-download-host-cdn
priority: 1
estimated_minutes: 25
modifies:
  - scripts/upload_to_r2.py
  - scripts/cdn_warm.sh
  - tests/test_upload_r2.py
executor: qwen3.6-plus
---

## 目标

Cloudflare R2 upload script for .exe artifacts (alternative to GitHub release assets).

1. `scripts/upload_to_r2.py`: takes local .exe path, uploads to R2 bucket via boto3 (S3-compatible)
2. `scripts/cdn_warm.sh`: HEAD request on uploaded URL × 3 regions to warm CDN
3. config from env: `R2_ACCESS_KEY`, `R2_SECRET`, `R2_BUCKET`, `R2_ENDPOINT`

## 验收

- [ ] `python3 scripts/upload_to_r2.py --file foo.exe` (mock S3 backend)
- [ ] missing env vars → exit 1 with clear msg
- [ ] `pytest tests/test_upload_r2.py` 全绿
- [ ] Black + ruff + shellcheck

## 不要做

- 不真上传 (Howard 配 R2 后 CI 跑)
- 不存 R2 creds in repo
- 直接 commit `feat/S100-r2-upload`
