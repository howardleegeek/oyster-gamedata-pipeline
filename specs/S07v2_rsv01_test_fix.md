---
task_id: S07v2-rsv01-output-var-fix
project: gamedata-pipeline
priority: 1
estimated_minutes: 12
depends_on: []
modifies:
  - tests/test_real_session_validator_hardening.py
executor: qwen3.6-plus
---

## 目标

修 `tests/test_real_session_validator_hardening.py` 4 个 NameError bug。

具体测试方法（已知出错）：
- `test_sample_reduces_sessions` (line 422 附近)
- `test_sample_larger_than_total_uses_all`
- `test_continue_on_error_processes_all_sessions`
- `test_without_continue_on_error_stops_at_first_fail`

错误模式：
```python
session_count = sum(1 for i in range(1, 11) if f"session_{i:03d}" in output)
#                                                                    ^^^^^^
# NameError: name 'output' is not defined
```

需要在 grep `output` 之前 capture subprocess.run 的结果：
```python
result = subprocess.run([...], capture_output=True, text=True)
output = result.stdout  # ← 这一行 cluster 上轮漏写
```

## 约束

- 只动 `tests/test_real_session_validator_hardening.py`
- 已经过的 10 个测试一行不能动
- 不动 `bin/real_session_validator.py`

## 验收标准

- [ ] `pytest tests/test_real_session_validator_hardening.py -v` 14/14 全绿
- [ ] Black + ruff
- [ ] grep "name 'output' is not defined" 0 hit on next run

## 不要做

- 不写新测试
- 不改 production code
- 直接 commit 到 branch `fix/S07v2-rsv01-test-output-var`
