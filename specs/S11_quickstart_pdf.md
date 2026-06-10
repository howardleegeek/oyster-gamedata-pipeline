---
task_id: S11-quickstart-pdf
project: gamedata-pipeline
priority: 2
estimated_minutes: 30
depends_on:
  - S10-provenance-offline-bundle
modifies:
  - docs/Quickstart.md
  - scripts/gen_quickstart.py
  - tests/test_gen_quickstart.py
executor: qwen3.6-plus
---

## 目标

生成买方上手指南 `docs/Quickstart.md`（≤ 3 页，markdown 可直接转 PDF）。内容：
1. 装 Python 3.10+（不需要其他）
2. 下载 bundle .tar.gz
3. 跑 `bash verify.sh`（来自 bundle）
4. exit 0 = 数据可信
5. （optional）跑 `python3 bin/end_to_end_gate_smoke.py <session> --strict-buyer`
6. 联系信息 + FAQ（5 条）

`scripts/gen_quickstart.py`：从 `bin/provenance_verify.py --help`、`bin/end_to_end_gate_smoke.py --help`、CHANGELOG.md 自动抽内容回填 Quickstart.md 的模板段。每次 release 跑一次。

## 约束

- 不用 LaTeX；纯 markdown（GitHub Actions 可用 pandoc 转 PDF）
- 不需要图片资源
- 测试 mock subprocess（避免真跑 verify.sh）

## 验收标准

- [ ] `docs/Quickstart.md` ≤ 1000 行（约 3 页 PDF）
- [ ] `scripts/gen_quickstart.py` 跑完不报错
- [ ] 生成内容包含 "exit 0", "verify.sh", "strict-buyer" 字串
- [ ] `pytest tests/test_gen_quickstart.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不实际转 PDF（CI workflow 后续做）
- 不加图片
- 不加外部链接（买方可能没 internet）
- 直接 commit 到 branch `feat/S11-quickstart-pdf`
