---
task_id: S70-recorder-cargo-check-ci
project: gamedata-pipeline
priority: 1
estimated_minutes: 30
depends_on:
  - S60-rust-build-ci
modifies:
  - .github/workflows/recorder-cargo-check.yml
  - tests/test_cargo_check_workflow.py
executor: qwen3.6-plus
---

## 目标

`.github/workflows/recorder-cargo-check.yml` — 快速 cargo check (no full build) trigger on PR to vendor/recorder submodule pointer change.

trigger:
- `pull_request:` paths `vendor/recorder` + `.github/workflows/recorder-cargo-check.yml`
- `workflow_dispatch:`

steps (≤ 5 min total):
1. checkout + submodule init shallow
2. install Rust stable + cache `~/.cargo` + `vendor/recorder/target/`
3. `cd vendor/recorder && cargo check --release --no-default-features 2>&1 | tee /tmp/cargo_check.log`
4. (no cargo test — too slow)
5. exit 0 if check OK; exit 1 + post PR comment if fail

## 约束

- Linux runner (`ubuntu-latest`)
- Cargo cache key includes Cargo.lock hash
- Don't full-build (just `cargo check`)
- Don't fail if vendor/recorder unchanged

## 验收

- [ ] YAML valid (yamllint)
- [ ] `pytest tests/test_cargo_check_workflow.py -v` 全绿
- [ ] grep "cargo check" in workflow
- [ ] grep "vendor/recorder" in paths

## 不要做

- 不真跑 cargo (CI 自己跑)
- 不 hardcode Rust version (用 stable)
- 直接 commit 到 branch `feat/S70-cargo-check-ci`
