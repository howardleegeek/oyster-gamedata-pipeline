---
task_id: S60v2-real-inno-setup-action
priority: 1
estimated_minutes: 15
modifies:
  - .github/workflows/build-recorder-windows.yml
executor: qwen3.6-plus
---

## 目标 — 修 S60 workflow real action 引用

S60 spec 里我写了 fake action `mareangler/iscc-action` — 真 CI 跑了 18s 就 fail with "repository not found".

**Step 1**: read `.github/workflows/build-recorder-windows.yml`

**Step 2**: write_file — replace fake action with real one:

```yaml
- name: Install Inno Setup
  shell: pwsh
  run: |
    choco install innosetup -y --no-progress
    "C:\Program Files (x86)\Inno Setup 6" | Out-File -FilePath $env:GITHUB_PATH -Append

- name: Compile installer
  shell: pwsh
  working-directory: ./installer
  run: |
    & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" oyster-recorder.iss
```

替换原 `uses: mareangler/iscc-action@v1` step.

## 约束

- ≤ 8 turns
- 必须 1 个 write_file
- 不改其他 step
- 直接 commit 到 branch `fix/S60v2-real-inno-action`
