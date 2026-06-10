---
task_id: S90-github-release-asset-upload
priority: 1
estimated_minutes: 25
modifies:
  - .github/workflows/release-asset-upload.yml
  - scripts/upload_release_asset.sh
  - tests/test_upload_release_asset.py
executor: qwen3.6-plus
---

## 目标

`.github/workflows/release-asset-upload.yml` — extend S60 build to auto-attach `OysterRecorder-setup-vX.Y.Z.exe` to the GitHub release matching the triggering tag.

Steps:
1. trigger: workflow_run completed from S60 build workflow OR tag push
2. checkout, download artifact from S60 workflow run
3. `gh release upload <tag> OysterRecorder-setup-*.exe --clobber`
4. (optional) generate SHA256SUMS.txt and upload too

`scripts/upload_release_asset.sh` — local helper for manual run.

## 验收

- [ ] YAML valid
- [ ] Bash shellcheck clean
- [ ] `pytest tests/test_upload_release_asset.py` 全绿 (mock gh CLI)
- [ ] grep "gh release upload" in script

## 不要做

- 不真 upload (CI 跑时才)
- 直接 commit 到 branch `feat/S90-release-asset-upload`
