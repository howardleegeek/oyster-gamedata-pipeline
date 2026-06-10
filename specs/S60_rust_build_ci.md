---
task_id: S60-rust-build-ci
project: gamedata-pipeline
priority: 1
estimated_minutes: 35
depends_on:
  - S14-windows-inno-installer
modifies:
  - .github/workflows/build-recorder-windows.yml
  - scripts/build_recorder_artifact.sh
  - tests/test_build_recorder_script.py
executor: qwen3.6-plus
---

## 目标

新建 `.github/workflows/build-recorder-windows.yml` — 跑 cargo build + Inno Setup compile + upload artifact.

Trigger: tag matching `recorder-v*` OR push to main with `vendor/recorder` changed OR manual workflow_dispatch.

Steps:
1. checkout + submodule init
2. install Rust stable toolchain (windows-latest runner)
3. `cd vendor/recorder && cargo build --release`
4. Run Inno Setup against `installer/oyster-recorder.iss` (use `mareangler/iscc-action@v1`)
5. (optional) sign with EV cert if `EV_CERT_PFX_BASE64` secret exists
6. Upload `OysterRecorder-setup-vX.Y.Z.exe` as workflow artifact
7. If tag triggered → attach to GitHub release

`scripts/build_recorder_artifact.sh` — local helper（mac/linux）run with cargo cross-compile if possible.

## 约束

- runners: `windows-latest`
- 不依赖 EV cert（unsigned 也能 release，warning 在 README）
- artifact retention 90 天
- 不需要真 Rust toolchain on mac1（cluster 自己写 workflow yaml）

## 验收

- [ ] workflow YAML valid (yamllint)
- [ ] shell script syntax OK (shellcheck)
- [ ] `pytest tests/test_build_recorder_script.py -v` 全绿（mock）
- [ ] 包含 EV signing 条件 branch

## 不要做

- 不真跑 cargo build (CI 自己跑)
- 不 hardcode EV cert path
- 不签别人的 cert
- 直接 commit 到 branch `feat/S60-rust-build-ci`
