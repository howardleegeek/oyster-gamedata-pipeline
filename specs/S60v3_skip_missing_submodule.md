---
task_id: S60v3-skip-missing-submodule
priority: 1
estimated_minutes: 10
modifies:
  - .github/workflows/build-recorder-windows.yml
executor: qwen3.6-plus
---

## 目标 — 修 CI submodule failure

Real CI error:
```
fatal: repository 'https://github.com/howardleegeek/oyster-enrichment.git/' not found
Failed to clone 'vendor/enrichment' a second time, aborting
```

`.gitmodules` references `vendor/enrichment` → nonexistent repo. Recursive submodule init fails.

**Step 1**: read `.github/workflows/build-recorder-windows.yml`

**Step 2**: write_file — modify `actions/checkout@v4` step.

Replace this block (or similar):
```yaml
- name: Checkout repository
  uses: actions/checkout@v4
  with:
    submodules: recursive
```

With this:
```yaml
- name: Checkout repository
  uses: actions/checkout@v4
  with:
    submodules: false
- name: Init only vendor/recorder and vendor/input-logger
  shell: bash
  run: |
    git submodule update --init --depth 1 vendor/recorder vendor/input-logger
```

This skips the broken `vendor/enrichment` submodule.

## 约束

- ≤ 8 turns
- 必须 1 个 write_file on the yaml
- 不动 build/compile/sign steps
- 直接 commit 到 branch `fix/S60v3-submodule-skip`
