---
task_id: D3v2-audit-h8-engine-zbuffer-pass
project: oyster-gamedata-pipeline
priority: 1
estimated_minutes: 35
depends_on: []
modifies:
  - bin/prd_compliance_audit_H8_patch.py
  - bin/zbuffer_pipeline_smoke.py
  - tests/test_zbuffer_pipeline_smoke.py
executor: codex-aliyun
iron_law_waived: "Smoke + audit-patch — references engine_zbuffer / monocular_da_v2 honest markers."
---

## 目标 (Howard 2026-05-18, retry of D3 after empty-fail)

让 audit H8 在 engine_zbuffer 路径下从 SKIP_honest → PASS。
加端到端 smoke 脚本验证 D1+D2+D3 闭环。

D3 V1 跑了 40 turn 输出 0 文件 (hallucinated done)。V2 收紧 — 自含的脚本和测试，
不依赖在 cluster 看 repo 内容。

## 上下文 (self-contained, 不需要 cluster 读 repo)

D2 已经 (或正在) 产生：
- `<session>/depth/frame_<N>.exr` (16-bit half float, channel "Z")
- `<session>/depth/.source` JSON 形如：
  ```json
  {"kind":"engine_zbuffer", "framerate":60, "max_depth_m":256.0,
   "calibrated":true, "frame_count":N,
   "alignment_method":"nearest_tick_50ms",
   "gap_misses":M, "gap_miss_ratio":"M/N"}
  ```
- `<session>/zbuffer/tick_<N>.bin` (raw f32 depth, 12-byte header W,H,tick_id)

H8 currently in `bin/prd_compliance_audit.py` (不要重写整个 audit，写 patch)：
当前逻辑：读 depth/.source → kind == "monocular_da_v2" → SKIP_honest。
要改成：识别 engine_zbuffer 并 PASS。

## 验收标准

### A. H8 patch 函数 (独立文件)

写到 `bin/prd_compliance_audit_H8_patch.py`，内容是单独一个函数：

```python
def evaluate_h8(session_dir: pathlib.Path) -> dict:
    """Return {'id':'H8', 'status':PASS|FAIL|SKIP_honest|PASS_DEGRADED, 'evidence':str}"""
    source_path = session_dir / "depth" / ".source"
    if not source_path.exists():
        return {"id":"H8", "status":"FAIL", "evidence":"depth/.source missing"}
    src = json.loads(source_path.read_text())
    kind = src.get("kind")
    frame_count = src.get("frame_count", 0)
    if kind == "monocular_da_v2":
        return {"id":"H8", "status":"SKIP_honest",
                "evidence":f"monocular DA-V2 fallback, {frame_count} frames"}
    if kind == "engine_zbuffer":
        if frame_count == 0:
            return {"id":"H8", "status":"FAIL",
                    "evidence":"engine_zbuffer marker but frame_count==0"}
        # Verify at least one EXR exists and is readable
        exrs = list((session_dir / "depth").glob("frame_*.exr"))
        if not exrs:
            return {"id":"H8", "status":"FAIL",
                    "evidence":"engine_zbuffer marker but no EXR files"}
        try:
            import OpenEXR
            f = OpenEXR.InputFile(str(exrs[0]))
            f.close()
        except Exception as e:
            return {"id":"H8", "status":"FAIL",
                    "evidence":f"engine_zbuffer EXR unreadable: {e}"}
        # Check gap miss ratio
        gap_str = src.get("gap_miss_ratio", "0/0")
        try:
            miss, total = (int(x) for x in gap_str.split("/"))
            ratio = miss / total if total else 0
        except (ValueError, ZeroDivisionError):
            ratio = 0
        if ratio > 0.1:
            return {"id":"H8", "status":"PASS_DEGRADED",
                    "evidence":f"engine ground truth with {ratio:.1%} gap misses"}
        return {"id":"H8", "status":"PASS",
                "evidence":f"engine Z-buffer ground truth, {frame_count} frames, EXR readable"}
    return {"id":"H8", "status":"FAIL",
            "evidence":f"unknown depth source kind: {kind}"}
```

- [ ] 文件创建成功
- [ ] 函数签名严格如上 (id 在 dict 内，不是 keyword)
- [ ] 5 个 status 分支都有
- [ ] OpenEXR 不可用 → 函数仍能 return (在 try/except 内 import)

### B. Smoke 脚本

写到 `bin/zbuffer_pipeline_smoke.py`:

- [ ] 调用方式：`python3 bin/zbuffer_pipeline_smoke.py` (无参数)
- [ ] 创建 tempdir 作为 fake session
- [ ] 生成 5 个 fake tick_<N>.bin 文件 (8×8 f32 depth, 模拟 5–50m 距离)
- [ ] 生成 fake game_state.jsonl (5 ticks @ 50ms 间隔，timestamp_ms 递增)
- [ ] 生成 fake action_camera_*.jsonl (10 camera frames @ 16.67ms = 60fps)
- [ ] 生成 fake depth/.source (kind=engine_zbuffer, frame_count=10)
- [ ] 生成 fake depth/frame_<N>.exr — 用 OpenEXR write 1×1 black EXR (最小有效)
- [ ] 调用 H8 evaluate function (import 自 prd_compliance_audit_H8_patch)
- [ ] assert result["status"] == "PASS"
- [ ] print result + exit 0; 失败 print 错误 + exit 1
- [ ] 不依赖任何外部 fixture / 真 session

### C. Test

写到 `tests/test_zbuffer_pipeline_smoke.py`:

- [ ] subprocess run smoke 脚本
- [ ] assert returncode == 0
- [ ] assert "PASS" in stdout
- [ ] 用 pytest skip if OpenEXR not installed (而不是 fail)

## 不要做

- ❌ 不要重写整个 `bin/prd_compliance_audit.py` — 只写 patch 函数
- ❌ 不要 mock OpenEXR — 失败就 graceful skip + 报告，不要假 PASS
- ❌ 不要把 H8 改成 always-PASS — DA-V2 路径必须保持 SKIP_honest
- ❌ 不要假设 cluster 能看到主 repo — 所有 import 都是 stdlib + numpy + OpenEXR (optional)
- ❌ 不要试图 commit / git / push — 只写文件到 WORKING_DIR
