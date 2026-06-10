---
task_id: S50-tester-onboarding-kit
project: gamedata-pipeline
priority: 1
estimated_minutes: 30
depends_on: []
modifies:
  - docs/TESTER_ONBOARDING.md
  - docs/TESTER_FAQ.md
  - docs/TESTER_TROUBLESHOOTING.md
  - scripts/gen_tester_kit.py
  - tests/test_gen_tester_kit.py
executor: qwen3.6-plus
---

## 目标

写 3 个面向非技术内测用户的文档 + 1 个生成脚本。

1. `docs/TESTER_ONBOARDING.md` — 1 页，包含：
   - 什么是 Oyster GameData (1 段，无 jargon)
   - 装 Windows Installer (1 屏，截图位置 placeholder)
   - 第一次启动 (OAuth login walkthrough)
   - 玩游戏赚 \$ (auto-record explanation)
   - 看 income notification
   - 卸载方式 (1 行)
2. `docs/TESTER_FAQ.md` — 10 个 Q&A：
   - "为什么我没收到钱？"
   - "录制偷偷开了我吃惊"
   - "怎么暂停录制"
   - "卡死怎么办"
   - "数据是不是隐私"
   - "支持哪些游戏"
   - "电脑慢怎么办"
   - "uninstall 后数据清吗"
   - "为什么 OBS 弹出"
   - "怎么联系支持"
3. `docs/TESTER_TROUBLESHOOTING.md` — 5 个常见 issue + step-by-step fix
4. `scripts/gen_tester_kit.py` — 把 3 个 docs zip 成 `tester_kit_vX.Y.Z.zip` 发给内测

## 约束

- 中文为主（内测是 Howard 朋友圈），technical terms 加英文
- 不超过 3 页 PDF
- 不依赖图片（图片 placeholder OK）
- 文档使用 Markdown

## 验收

- [ ] 3 个 .md 各 ≤300 行
- [ ] `python3 scripts/gen_tester_kit.py --output /tmp/kit.zip` 5s 内出
- [ ] zip 内含 3 个 .md
- [ ] `pytest tests/test_gen_tester_kit.py -v` 全绿
- [ ] Black + ruff

## 不要做

- 不写截图（mockup 描述 OK）
- 不绑定 EV 证书细节
- 不写 backend 部署
- 直接 commit 到 branch `feat/S50-tester-onboarding-kit`
