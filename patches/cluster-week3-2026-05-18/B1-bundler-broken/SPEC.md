---
task_id: B1-batch-bundler
project: oyster-gamedata-pipeline
priority: 1
estimated_minutes: 35
depends_on: []
modifies:
  - bin/batch_bundler.py
  - tests/test_batch_bundler.py
executor: codex-aliyun
---

## 目标 (Howard 2026-05-18, Week 3 Gap #4 batch path)

把 N 个 finalized session 打包成 1 个 buyer-deliverable tarball + manifest。
manifest 含 per-session sha256, 总体 Merkle root, 元数据。
买方下载一个 tarball, 用 manifest 验证每个 session 完整未被篡改。

## 上下文 (self-contained)

输入: 一个或多个 finalized session 目录 (各自含完整 PRD artifacts:
video, game_state.jsonl, action_camera_*.jsonl, depth/, gameinfo.xlsx 等)。

输出: 单个 `<output_dir>/oyster-batch-<YYYYMMDD-HHMMSS>.tar.gz` 文件 +
`oyster-batch-<YYYYMMDD-HHMMSS>.manifest.json`。

## 约束

- 用 stdlib tarfile + hashlib (无新 deps)
- Merkle 用简单二叉树: leaf = sha256(file_bytes), node = sha256(left + right)
- 不修改已有 session — 只读
- tarball 文件 layout: `<session_id>/<original_paths>` (preserve dir structure)

## 验收标准

### A. CLI

```bash
python3 bin/batch_bundler.py <session_dir>... --output-dir <output_dir>
```

例子:
```bash
python3 bin/batch_bundler.py \
  /tmp/session_a /tmp/session_b /tmp/session_c \
  --output-dir /tmp/batches
```

输出:
```
BATCH BUNDLER — 3 sessions → 1 deliverable
  session_a: 12 files, 234.5 MB, sha256: a3f9...
  session_b: 11 files, 189.2 MB, sha256: b71c...
  session_c: 13 files, 267.8 MB, sha256: c4e2...
  
  Total: 36 files, 691.5 MB
  Merkle root: d8a1f3...
  Tarball: /tmp/batches/oyster-batch-20260518-094800.tar.gz
  Manifest: /tmp/batches/oyster-batch-20260518-094800.manifest.json
```

### B. Manifest 格式

```json
{
  "batch_id": "oyster-batch-20260518-094800",
  "created_at_utc": "2026-05-18T16:48:00Z",
  "session_count": 3,
  "total_files": 36,
  "total_bytes": 725012345,
  "merkle_root": "d8a1f3...",
  "sessions": [
    {
      "session_id": "session_a",
      "file_count": 12,
      "bytes": 245951234,
      "session_sha256": "a3f9...",
      "files": [
        {"path": "session_a/recording.mp4", "sha256": "...", "bytes": 234567890},
        {"path": "session_a/game_state.jsonl", "sha256": "...", "bytes": 12345},
        ...
      ]
    },
    ...
  ],
  "tarball_filename": "oyster-batch-20260518-094800.tar.gz",
  "tarball_sha256": "f2e7..."
}
```

### C. 逻辑

- [ ] 对每个 session dir: walk all files, 计算 sha256 per file, 收集 (path, sha256, size)
- [ ] session_sha256 = sha256(concat of sorted file sha256 hex strings)
- [ ] Merkle root: collect all file sha256 hashes (bytes), pad to power-of-2 with zeros,
      buildBottomUp: node[i] = sha256(left + right)
- [ ] 写 tarball: tar.add each session_dir with arcname=<session_id>/...
- [ ] 计算 tarball_sha256 after close
- [ ] 写 manifest.json

### D. 测试

`tests/test_batch_bundler.py`:
- [ ] fixture: 2 fake sessions each 3 files (small txt)
- [ ] run bundler, assert tarball + manifest 存在
- [ ] parse manifest, assert session_count == 2, total_files == 6
- [ ] verify Merkle root deterministic (same inputs → same root)
- [ ] verify tarball_sha256 in manifest matches actual sha256 of tarball file
- [ ] verify file sha256 in manifest matches actual sha256 of file inside tarball

### E. 自检

```bash
mkdir -p /tmp/test-sessions/{a,b} && \
  echo "test-a-1" > /tmp/test-sessions/a/file1.txt && \
  echo "test-a-2" > /tmp/test-sessions/a/file2.txt && \
  echo "test-b-1" > /tmp/test-sessions/b/file1.txt
python3 bin/batch_bundler.py /tmp/test-sessions/a /tmp/test-sessions/b \
  --output-dir /tmp/test-batches
ls /tmp/test-batches/
python3 -m pytest tests/test_batch_bundler.py -v
```

两个都应该 exit 0。

## 不要做

- ❌ 不要用 boto3 / S3 upload — 那是 B3 spec 的事
- ❌ 不要 sign manifest (ed25519) — 那是 B2 spec 的事
- ❌ 不要 OP_RETURN anchor — 那是 B2 spec 的事
- ❌ 不要修改 session dirs — 只读
- ❌ 不要把 session 元数据嵌入 tarball 名 (会泄露 PII)
