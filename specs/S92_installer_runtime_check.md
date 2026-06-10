---
task_id: S92-installer-runtime-check
priority: 2
estimated_minutes: 20
modifies:
  - installer/check_runtime.bat
  - installer/oyster-recorder.iss
  - tests/test_runtime_check.py
executor: qwen3.6-plus
---

## 目标

Installer 启动前 check Windows VC++ runtime. 若缺，提示用户下载或退出。

1. `installer/check_runtime.bat`:
   - check `HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64` registry
   - exit 0 if found, exit 1 if missing
   - on missing: prompt "VC++ runtime required. Download? (Y/n)" → open URL
2. modify `oyster-recorder.iss` to call `check_runtime.bat` in `[Run]` section pre-install

## 验收

- [ ] check_runtime.bat syntax valid (cmd /c)
- [ ] .iss valid
- [ ] mock registry → exit 0/1 correctly
- [ ] `pytest tests/test_runtime_check.py` 全绿

## 不要做

- 不真下载 VC++ runtime
- 不修改 system32
- 直接 commit 到 branch `feat/S92-installer-runtime-check`
