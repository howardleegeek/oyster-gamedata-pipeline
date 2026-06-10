---
task_id: S60v7-remove-locked-flag
priority: 1
estimated_minutes: 8
modifies:
  - .github/workflows/build-recorder-windows.yml
executor: qwen3.6-plus
---

## 目标 — Layer 6 fix: --locked 阻止 cargo 更新 Cargo.lock

CI error:
```
error: cannot update the lock file ... because --locked was passed to prevent this
```

Even after pinning recorder to v2.6.0, the lock file needs minor updates (transitive deps). `--locked` blocks this.

**Step 1**: read `.github/workflows/build-recorder-windows.yml`

**Step 2**: write_file — find the line `cargo build --release --locked` and change to `cargo build --release`.

**Step 3**: run_cmd("grep -c 'cargo build --release --locked' .github/workflows/build-recorder-windows.yml") MUST be 0

## 约束

- ≤ 5 turns
- 1 write_file
- 不动 install steps, sign steps, upload steps
- 直接 commit 到 branch `fix/S60v7-remove-locked`
