# SE — rc10 磁盘空间预检 (B5)

在 `_run_one_session()` 的 Phase 1 等录制开始之前 (大约 line 1700–1760, 在 `self._tmp_dir = Path(tempfile.mkdtemp(...))` 之后), 加:

```python
import shutil
free_bytes = shutil.disk_usage(self._tmp_dir).free
MIN_FREE_BYTES = 500 * 1024 * 1024  # 500 MB
if free_bytes < MIN_FREE_BYTES:
    free_mb = free_bytes / (1024 * 1024)
    self._set("⚠️ 磁盘空间不足", ORANGE,
              f"剩余 {free_mb:.0f} MB, 录制需 ≥500 MB. 清理后重试.")
    _trace(f"disk_check: ABORT — only {free_mb:.0f} MB free in {self._tmp_dir}")
    return  # 退出本次 session, 让 watch_loop 重新等待 arm
```

## 约束
- 仅在 session 开始前检查 1 次, 不在录制循环里反复查 (会打 IO)
- 用现有的 `self._set` 提示, 不弹 messagebox (录制中弹窗会抢焦点导致 MC 卡顿)
- 颜色用 `ORANGE` (现有的常量)
- 不杀 MC, 不动 ffmpeg

## 验收
- [ ] 函数顶部有 `import shutil` (如果还没的话; 文件可能已经 import 过)
- [ ] `_run_one_session` 里有 `shutil.disk_usage` 调用 + `MIN_FREE_BYTES = 500 * 1024 * 1024` 常量
- [ ] 磁盘不足走 `self._set(..., ORANGE, ...)` + `_trace` + early `return`
- [ ] `python3 -c "import ast; ast.parse(open('bin/recorder_consumer_lite.py').read())"` 通过
