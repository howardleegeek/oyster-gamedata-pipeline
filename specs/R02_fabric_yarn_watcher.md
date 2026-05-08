---
task_id: R02-fabric-yarn-watcher
project: recorder
priority: 2
estimated_minutes: 60
depends_on: []
modifies:
  - .github/workflows/fabric-yarn-watcher.yml      # NEW — daily cron job
  - .github/workflows/build-mc-mod.yml             # accept new versions from watcher PRs
  - bin/check_fabric_yarn_versions.py              # NEW — meta API poller + matrix auto-extender
executor: glm-aliyun
---

## 目标 (Howard 2026-05-08)

让 mc-mod build matrix 自动跟上 Fabric 上游 — 一旦 Fabric 发布新的
stable MC 版本的 yarn mappings (e.g. 26.1.2, 26.2 stable), 自动扩展
build matrix 并触发 CI build, 不需要 Howard 手动改 yaml.

## 上下文

Howard 2026-05-08 的 minipc 装的是 MC 26.1.2 (Mojang 当前 stable),
而我们 mod matrix 只支持 1.20.1...1.21.5. 对 fabric meta API 检测:
- MC 26.1.2 已在 Fabric `versions/game` 列表里
- 但 yarn mappings (`versions/yarn/26.1.2`) 还没 publish
- 所以现在硬加进 matrix 会编译失败

每次 Mojang 发新 stable, Fabric 通常几天到几周 ship yarn. 我们需要
一个 watcher 自动检测 → 一旦上游就绪, 自动加进矩阵.

## 数据准确铁律

- 绝不向 matrix 加任何 yarn mappings 还没 publish 的 MC 版本.
- 检测必须查 `https://meta.fabricmc.net/v2/versions/yarn/<version>` 真实返回非空 JSON 数组.
- fabric-api compatibility 也必须查 `https://maven.fabricmc.net/.../fabric-api/maven-metadata.xml`
  确认有 `*+<version>` 后缀的 artifact.
- snapshot 版本绝不进 stable matrix — 只 stable=true 的版本才扩展.

## 约束

- 不动现有 9 个版本的 coords.
- 不写假 yarn build number — 必须从 meta API 取最新 build.
- 不引新 Python 依赖 (stdlib only, urllib + json).
- watcher 不直接 push main — 必须通过 PR (人工/自动批 review 都行).
- snapshot 版本 build best-effort lane 维持 (现有逻辑).

## 验收标准

### A. `bin/check_fabric_yarn_versions.py`

- [ ] CLI: `python3 bin/check_fabric_yarn_versions.py`
- [ ] 调用 `meta.fabricmc.net/v2/versions/game` 取所有 stable=true 版本
- [ ] 对每个 stable 版本调用 `versions/yarn/<v>` 检查 yarn 已 ship
- [ ] 对每个 stable 版本调用 `https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/maven-metadata.xml`
  解析最新 `*+<v>` artifact 版本号
- [ ] 对比当前 `.github/workflows/build-mc-mod.yml` matrix —
  输出 stdout JSON: `{"current": [...], "available_to_add": [...], "skip_reason": {...}}`
- [ ] exit 0 always; report-only (不直接改文件)

### B. `.github/workflows/fabric-yarn-watcher.yml`

- [ ] schedule: `cron: '0 6 * * *'` (daily 06:00 UTC)
- [ ] workflow_dispatch trigger 也支持
- [ ] step 1: run `bin/check_fabric_yarn_versions.py`, capture json output
- [ ] step 2: 如果 `available_to_add` 非空,
  - 修改 `.github/workflows/build-mc-mod.yml` 加新版本 + matrix.include 块
  - 创建 PR 标题: `chore(mc-mod): auto-extend matrix to MC <X.Y.Z> (Fabric yarn shipped <DATE>)`
  - PR body: 引用 fabric meta API response 作为证据 (real evidence, no fabrication)
  - PR labels: `auto-extend`, `mc-mod`
- [ ] step 3: 如果 `available_to_add` 为空, exit 0 with "no new versions" message

### C. build-mc-mod.yml 兼容

- [ ] 现有 9 版本 + snapshot lane 不动
- [ ] matrix.include 结构保持, watcher 加新条目时按现有模式
- [ ] 任何新版本进 matrix 后, CI build 必须 green 才能合并 PR

### D. 数据验证

- [ ] watcher 调用 fabric meta 返回空 → 跳过该版本 (不假设可以)
- [ ] fabric-api maven 没匹配 artifact → 跳过该版本
- [ ] 任何 HTTP 失败 (5xx, 网络断) → exit 0 with error log, 不创建 PR

### E. Howard's UX

- [ ] PR 出现时, Howard 直接 review + merge 或 close
- [ ] Merged PR 自动触发 build-mc-mod.yml CI
- [ ] CI 全绿后, Howard 可手动 cut 新 recorder release (`recorder-v0.27.X-rc1`)
  把新 jar 加进 release assets

## 不要做

- ❌ 不要在 watcher 里直接 push main (只能 PR).
- ❌ 不要假设 fabric-api 命名规则 (必须真查 maven xml).
- ❌ 不要在 watcher 里 cut release tag (人工决定 release timing).
- ❌ 不要 silent-skip 失败 — 任何 API 错误必须 log + exit 0 (cron 不该崩).
- ❌ 不要把 snapshot 版本 (stable=false) 加进 stable matrix.

## Release path

watcher 每天 06:00 UTC 跑 → 如果 26.1.2 yarn 上游就绪, 它创建 PR →
Howard merge → mc-mod CI build 全 9+1 版本 + 26.1.2 = 10 个 → green →
Howard cut `recorder-v0.27.1-rc1` with 10 jars → 推到 minipc 上的真 26.1.2
就能 work.

期间, Howard 临时方案是装 1.21.4 stable (~5 min Mojang Launcher).
