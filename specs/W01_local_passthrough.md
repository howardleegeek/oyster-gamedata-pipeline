---
task_id: W01-local-passthrough
project: web-portals
priority: 1
estimated_minutes: 45
depends_on: []
modifies:
  - bootstrap-local.sh           # NEW
  - local-smoke.sh               # NEW
  - LOCAL_DEV.md                 # NEW
  - README.md                    # add Web Portals section
executor: codex-aliyun
---

## 目标 (one-line direction from Howard 2026-05-08)

让 real users 从 clean clone 到 web-tester (:3000) + web-buyer (:3001)
本地全跑通 — single command bootstrap + smoke verification + 5-minute guide.

## 约束

- **数据准确铁律 (Howard 2026-05-08)**: 所有输出必须是 real probe results,
  不允许编造 .env keys, fake URLs, 假 smoke responses. bootstrap 必须真跑,
  smoke 必须真探活, doc 必须引用真实端口和路径. 违反 = iron-law 违反.
- 模块化: 不要超过 3 个新文件 + 1 个 README 修改. 已识别的 4 个 deliverable
  恰好够用.
- Shared-Supabase 模式: 一个 stack (tester ports 54321/54322/54323), 两套
  migration apply 到同一 DB. tester 表 (testers/tarballs/payouts) 和 buyer
  表 (buyers/purchases/licenses/cart_items/catalog_metadata) 不重叠, 这种
  设计已在 .env.example 注释里写明 "Dev / prototyping: point at the SAME
  project". 不要起两个 Supabase stack — 复杂且费资源.
- macOS + Linux POSIX shell 兼容 (避免 bash-isms 如 `[[ ]]`, mapfile).
- 不得改 web-tester / web-buyer / supabase 的源码、迁移、env.example.
- 不得改 iron-law lint, 不得改任何已存在的测试.

## 验收标准

- [ ] `bootstrap-local.sh` 从 clean repo 状态可以一行跑完:
  - 检测前置 (node, npm, supabase CLI, docker)
  - 启动 supabase (web-tester/supabase/, port 54321)
  - apply BOTH `web-tester/supabase/migrations/*.sql` AND
    `web-buyer/supabase/migrations/*.sql` 到 tester 的本地 DB
  - 解析 `supabase status` 输出, 提取 anon key + service_role key
  - 生成 `web-tester/.env.local` + `web-buyer/.env.local` (相同 URL+keys)
  - 在两个 portal 跑 `npm install` (idempotent)
  - 打印 "下一步" 指引 (开两个 terminal, `npm run dev`)
- [ ] `local-smoke.sh` probes (real curl, color-coded):
  - http://localhost:3000/ → 2xx/3xx
  - http://localhost:3001/ → 2xx/3xx
  - http://localhost:3001/api/catalog?limit=10 → 200, JSON 解析成功
  - http://127.0.0.1:54321/ → Supabase up
  - 返回 0 if all green, 1 if any red
- [ ] `LOCAL_DEV.md` (top-level):
  - Prerequisites (node 18+, supabase CLI, Docker Desktop running)
  - bootstrap-local.sh 调用方式
  - 双 terminal 启动指引
  - smoke 调用方式
  - 第一次走通 walkthrough (signup → /dashboard 看到 "No uploads yet")
  - Troubleshooting: 端口冲突 / Docker 没启动 / supabase CLI 未安装
- [ ] `README.md` 顶部加 "## 🌐 Web Portals — local dev" 小节, 一行跳转
  到 `LOCAL_DEV.md`. 不动现有 recorder/data 部分.
- [ ] **真跑过验收 (数据准确铁律)**:
  - 在 clean clone 上真跑一遍 `bootstrap-local.sh` 不报错完成
  - 跑完后 `local-smoke.sh` exit 0
  - 浏览器打开 :3000 看到 "Real Minecraft gameplay data..." landing
  - 浏览器打开 :3001 看到 "Real Minecraft gameplay data for AI training" landing
  - 把这次真跑的输出 (端口/进程/curl 结果) 贴到 commit message 里证明
- [ ] black --check src/ tests/: 全绿
- [ ] iron-law lint 24/24: 全绿
- [ ] 双 portal `npm run build`: 全绿
- [ ] commit message 引用 W01-local-passthrough 这个 spec id

## 不要做

- ❌ 不要起两个并行 Supabase stack (config.toml 里端口虽然不同,
  shared-stack 模式更简单且 tables 不冲突)
- ❌ 不要伪造 .env.local 内容 — 必须从 supabase status 真解析
- ❌ 不要写 docker-compose / kubernetes / 复杂编排
- ❌ 不要碰任何 portal 源码 / 迁移 / iron-law tests
- ❌ 不要 询问 / 等待 用户确认 — 直接 ship

## 执行环境

- repo: `~/Downloads/oyster-agent-runner`
- target tag baseline: `v0.1.0-rc10`
- 完成后: commit + push + 报告 (在 commit message 里贴真跑输出)

## 上下文背景

- `PRODUCTION_GAPS.md` 已列出 Howard 须自办的 5+1 件; 本 spec 是为了让
  开发者/早期用户 *在没 Vercel deploy* 的情况下也能本地跑通.
- 现有 `watch.sh` 已有 probe 模式可参考 — 但 watch.sh 是 production
  monitor, 这里要写的是 local smoke (短期一次性).
- `web-tester/.env.example` 第 14-22 行注释里说明了 hard-gate 行为, 同时
  `web-buyer/.env.example` 注释里说明 "Dev / prototyping: point at the SAME
  project as web-tester so the `tarballs` table is shared".
