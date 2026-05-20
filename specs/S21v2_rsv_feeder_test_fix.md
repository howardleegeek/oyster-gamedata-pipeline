---
task_id: S21v2-rsv-feeder-test-fix
project: gamedata-pipeline
priority: 1
estimated_minutes: 10
depends_on: []
modifies:
  - tests/test_rsv_feeder.py
executor: qwen3.6-plus
---

## 目标

修 `tests/test_rsv_feeder.py` 2 个 NameError bug，模式与 S07v2 完全一致：

- Line 203 `TestFilterNewSessions::test_all_processed`：
  ```python
  make_fake_session(root, "s1")  # 没 capture 返回值
  compute_session_hash(s1)        # NameError: s1 undefined
  ```
  应改为：
  ```python
  s1 = make_fake_session(root, "s1")
  ```
- Line 219 `TestFilterNewSessions::test_partial_processed`：同样 `s2` 未绑定。

## 约束

- 只动 `tests/test_rsv_feeder.py`
- 不动 `daemon/rsv_feeder.py`（production code 正确）
- 已经过的 42 个测试不能动

## 验收标准

- [ ] `pytest tests/test_rsv_feeder.py -v` 44/44 全绿
- [ ] Black + ruff
- [ ] grep "NameError" pytest output → 0 hit

## 不要做

- 不写新测试
- 不重构 fixture
- 直接 commit 到 branch `fix/S21v2-rsv-feeder-test-bind`
