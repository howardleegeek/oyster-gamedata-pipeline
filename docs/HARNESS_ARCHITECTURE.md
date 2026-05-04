# Harness Architecture — Handoff Document

> Howard 在多个 Claude session 并发迭代。本文档是**完整 handoff** — 任何新 session 读完这一份就能继续工作,无需读历史 transcript。

---

## 1. Mental Model(30 秒看懂)

```
┌──────────────────────────────────────────────────────────────┐
│  Howard (decision maker, 不动手)                              │
│       ↓ 编辑 docs/audit_gaps.yaml 加新 gap                    │
└──────────────────────────────────────────────────────────────┘
                ↓
┌─────────────────────────┐    ┌─────────────────────────┐
│  HARNESS DAEMON #1      │ ↔  │  HARNESS DAEMON #2      │
│  (mac-1 nohup)          │    │  (mac-2 nohup)          │
│  bin/harness_loop.py    │    │  bin/harness_loop.py    │
│                         │    │                         │
│  Loop forever:          │    │  Loop forever:          │
│  1. git pull            │    │  1. git pull            │
│  2. check lock (180s TTL)│    │  2. check lock          │
│  3. claim or exit       │    │  3. claim or exit       │
│  4. dispatch top-N gaps │    │  4. dispatch ...        │
│  5. poll completion     │    │  ...                    │
│  6. scp + verify + commit + push (1 spec = 1 commit)│
│  7. heartbeat + sleep 30│    │                         │
└─────────────────────────┘    └─────────────────────────┘
       │                                    │
       │ ssh / scp                          │ local (no ssh self)
       ↓                                    ↓
┌──────────────────────────────────────────────────────────────┐
│  Aliyun cluster on mac-2                                     │
│  /Users/howardlee/aliyun_work/                               │
│  - minimax_agent_simple.py (worker)                          │
│  - 4-model rotation (deepseek/qwen/glm/minimax)             │
│  - 8-concurrent stable                                       │
└──────────────────────────────────────────────────────────────┘

WATCHDOG (cron */5 min on both mac-1 and mac-2):
  - bin/harness_watchdog.sh
  - if daemon dead → restart
  - if heartbeat > 600s old → kill + restart (zombie)
```

**核心 invariant**: `docs/audit_gaps.yaml` 是单一真相源。两 daemon 通过 git push/pull 同步状态。lock 机制防双跑(任意时刻只一台 active dispatch)。

---

## 2. File Inventory(全部移动部件)

### 核心代码
| 文件 | 作用 | 行数 |
|---|---|---|
| `bin/harness_loop.py` | 主 daemon | ~480 |
| `bin/harness_watchdog.sh` | 5-min cron 自愈 | ~73 |
| `docs/audit_gaps.yaml` | gap 注册表 + lock state | ~190 |

### 文档
| 文件 | 用途 |
|---|---|
| `docs/HARNESS_ARCHITECTURE.md` | **本文件** — handoff |
| `docs/HARNESS_FAILOVER.md` | 详细 failover 流程 |
| `docs/audit_gaps.yaml` | gap registry (also data) |

### Cluster 端(mac-2,不在 git)
| 路径 | 作用 |
|---|---|
| `/Users/howardlee/aliyun_work/minimax_agent_simple.py` | Aliyun LLM agent |
| `/Users/howardlee/aliyun_work/G*/spec.md` | gap spec(harness 写) |
| `/Users/howardlee/aliyun_work/G*/agent.log` | minimax run 日志 |
| `/Users/howardlee/aliyun_work/G*/<artifact>` | cluster 产出 NEW file |

---

## 3. audit_gaps.yaml Schema

```yaml
version: 1
last_updated: 2026-05-03

# Active-passive failover 锁(daemon 自动管理,人不要改)
harness_lock:
  host: <hostname>
  pid: <pid>
  last_heartbeat: 2026-05-03T22:45:00Z

gaps:
  - id: G001                           # unique ID
    title: bin/preflight_check_v2.py   # NEW file path (single new file per gap)
    purpose: "1-2 line description for spec generator"
    status: pending|dispatched|completed|failed|skipped
    priority: P0|P1|P2|P3              # P0 highest
    lines_estimate: 150                # informational only
    deps: []                           # currently unused, future cross-gap deps
    # auto-set by daemon:
    dispatched_at: <timestamp>
    completed_at: <timestamp>
    fail_reason: <string>
    retries: 0                         # max 3 then permanent fail
    skip_reason: <string>              # set if file already exists
```

### Status 状态机
```
pending → (dispatch) → dispatched → (cluster done) → completed
                                  ↓ (artifact missing/syntax fail/git fail)
                              failed → (retries < 3) → pending
                                     ↓ (retries == 3)
                                 permanent failed
pending → (file already exists) → skipped (no dispatch)
```

---

## 4. Operational Cheatsheet

### 看 daemon 状态
```bash
# mac-1
ps -ef | grep harness_loop | grep -v grep
tail -f /Users/howardli/Downloads/oyster-agent-runner/harness.log

# mac-2
ssh howard-mac2 'pgrep -fl harness_loop; tail -f ~/oyster-gamedata-pipeline/harness.log'

# 看 lock 当前 owner
python3 -c "
import yaml
with open('docs/audit_gaps.yaml') as f: d = yaml.safe_load(f)
print(d.get('harness_lock', {}))
"

# 看 gap 状态分布
python3 -c "
import yaml
with open('docs/audit_gaps.yaml') as f: d = yaml.safe_load(f)
from collections import Counter
print(Counter(g.get('status','pending') for g in d['gaps']))
"
```

### 启 / 停 daemon
```bash
# mac-1 启
cd /Users/howardli/Downloads/oyster-agent-runner
nohup python3 bin/harness_loop.py >> harness.log 2>&1 &

# mac-1 停
pkill -f 'python3 bin/harness_loop.py'

# mac-2 启
ssh howard-mac2 'cd ~/oyster-gamedata-pipeline && nohup python3 bin/harness_loop.py >> harness.log 2>&1 &'

# mac-2 停
ssh howard-mac2 'pkill -f harness_loop.py'

# 一次手动跑 (debug, 不进入 loop)
python3 bin/harness_loop.py --once

# dry-run 看会派啥 (无 ssh / 无 commit)
python3 bin/harness_loop.py --once --dry-run

# 强制 dispatch 单个 gap
python3 bin/harness_loop.py --gap G014
```

### 加新 gap
```yaml
# 编辑 docs/audit_gaps.yaml,在 gaps: 列表末尾加:
  - id: G031                                 # 找空 ID
    title: bin/your_new_helper.py            # 必须 NEW file (不能改已有)
    purpose: "What this does + why vendor cares"
    status: pending
    priority: P1
    lines_estimate: 100
```

```bash
git add docs/audit_gaps.yaml && \
git commit -m "feat(gap): add G031 your_new_helper" && \
git push origin main
# 下一轮 daemon iter 自动 pickup
```

### Watchdog cron (auto-heal)
```bash
# mac-1 安装
(crontab -l 2>/dev/null; echo "*/5 * * * * /Users/howardli/Downloads/oyster-agent-runner/bin/harness_watchdog.sh") | crontab -

# mac-2 安装
ssh howard-mac2 '(crontab -l 2>/dev/null; echo "*/5 * * * * /Users/howardlee/oyster-gamedata-pipeline/bin/harness_watchdog.sh") | crontab -'

# 看 watchdog 日志
tail -f /Users/howardli/Downloads/oyster-agent-runner/watchdog.log
ssh howard-mac2 'tail -f ~/oyster-gamedata-pipeline/watchdog.log'
```

---

## 5. Iron Rules(违反必 break)

### 5.1 Cluster spec 必须 1 spec = 1 NEW file
**Why**: minimax_agent_simple.py write_file 整文件,不支持 patch。改大文件(>200 行)它会 truncate(rc6 buyer_spec_adapter 846→79 LOC 灾难)。

**怎么做**: 每个 gap.title 必须是**仓库当前不存在**的 NEW file path。daemon `pick_pending_gaps()` 会自动 skip "file already exists"。

**怎么 patch existing file?** — 不要 patch。改成"创建一个新 helper file,主程序 import 它"。

### 5.2 audit_gaps.yaml lock field 不要手动改
daemon 自动写 `harness_lock.last_heartbeat`。手动改可能造成两台同时拿锁。

### 5.3 gap.title 唯一
两个 gap 不能 title 相同(file conflict + commit race)。

### 5.4 不要 push 时绕开 lock
即使你 git push 了 docs/audit_gaps.yaml 改 status=completed,daemon 下一轮 pull 会看到。但**永远不要在 daemon 跑时手动 commit 同 file** — 会 git push reject(non-fast-forward)。

---

## 6. Decision Log(避免重蹈覆辙)

| 决策 | 理由 |
|---|---|
| 用 YAML lock 而非 SQLite/Postgres | 单文件 + git 已是分布式 KV;无需新基础设施 |
| Lock TTL 180s | 30s daemon iter ≤ 1 cycle 就续 heartbeat,3× 容错 |
| Watchdog 5min | 比 daemon iter 30s 慢 10×;不会 thrash |
| 1 gap = 1 NEW file | 唯一防 cluster truncation 灾难的方式(rc6 教训) |
| Daemon 用 nohup 不 launchd | launchd auto-load 被 permission 系统拒;cron watchdog 替代 |
| mac-1 主 + mac-2 备 active-passive | 两 active 同时 dispatch 会 commit race;lock 强制单 active |
| YAML lazy import (PyYAML 可选) | bootstrap 时 vendor 可能没装;有 fallback minimal parser |
| Subprocess timeout 180s | 60s 在 mac-2 busy 时太短(实际 dispatch crashed once) |
| Try/except 包 dispatch | 单 gap fail 不能 take down 整 daemon |

---

## 7. Known Issues + Roadmap

### 当前 Issues
1. **mac-1 daemon 偶尔 stuck**(无 heartbeat after 5+ min)— watchdog 会救
2. **重复日志行**(双 logging handler)— cosmetic, 不影响功能
3. **Lock 抢占时 git pull rebase 可能 conflict** — 极少 hit, retry 解决
4. **Cluster truncation 没法防住** — 只靠 "1 spec 1 NEW file" 规则规避
5. **没有 commit message dedup** — 两 daemon 极快交替时可能多 push 同一 commit

### Roadmap
- **Wave-7+** gaps in audit_gaps.yaml(29 pending)— daemon 自跑
- **真 OBS / DepthAnything e2e 测试** — 需在真 Mac/Windows 客户端测(non-cluster)
- **Backend ingest pipeline 真部署**(SQS/Lambda/PG)— 当前只 architecture doc
- **vendor exe 一键安装包** — PyInstaller + Inno Setup,P3 priority
- **Multi-vendor lock**(>2 host)— 当前 host-string lock,理论支持 N host 但未测

### 下一步明确动作
1. Howard 在新 session: `cat docs/audit_gaps.yaml` 看 pending gaps
2. 加新 gap entry 到 yaml(独立 NEW file)
3. commit + push → daemon 自动 pickup
4. 看 git log --oneline | head -10 流入新 commit

---

## 8. Bootstrap from Scratch(全死时怎么救)

```bash
# 1. mac-1 上 clone (如不在)
cd ~/Downloads
git clone https://github.com/howardleegeek/oyster-gamedata-pipeline.git oyster-agent-runner
cd oyster-agent-runner

# 2. 装依赖
pip install -e . pyyaml

# 3. 验证 ssh howard-mac2 work
ssh -o ConnectTimeout=5 howard-mac2 echo OK

# 4. 启 daemon
nohup python3 bin/harness_loop.py >> harness.log 2>&1 &

# 5. 装 watchdog
chmod +x bin/harness_watchdog.sh
(crontab -l 2>/dev/null; echo "*/5 * * * * $(pwd)/bin/harness_watchdog.sh") | crontab -

# 6. mac-2 重复(SSH 配 deploy key in GitHub repo settings already done)
ssh howard-mac2 'cd ~/oyster-gamedata-pipeline && git pull && nohup python3 bin/harness_loop.py >> harness.log 2>&1 &'
ssh howard-mac2 '(crontab -l 2>/dev/null; echo "*/5 * * * * /Users/howardlee/oyster-gamedata-pipeline/bin/harness_watchdog.sh") | crontab -'
```

---

## 9. Repo Hot Links

- Repo: <https://github.com/howardleegeek/oyster-gamedata-pipeline> (PUBLIC)
- Latest release: <https://github.com/howardleegeek/oyster-gamedata-pipeline/releases>
- Live commits: <https://github.com/howardleegeek/oyster-gamedata-pipeline/commits/main>

---

## 10. Mental Model 升级版(给同时跑多个 Claude sessions 的 Howard)

```
                Howard (你, 多 session 并发)
                   │
                   ├── Session A: 加 gap 到 audit_gaps.yaml + push
                   ├── Session B: 调研 PRD / 写新 spec / push
                   ├── Session C: 真测 vendor e2e (OBS/Depth)
                   └── Session D: 看 GitHub commits + 决策
                   │
                   ▼
              git origin/main (单一真相源)
                   │
                   │ pull/push 同步
                   ▼
        ┌──────────┴──────────┐
        ▼                     ▼
    mac-1 daemon          mac-2 daemon
    (active 或 stale)     (active 或 stale)
    lock 协调最多 1 active
        │                     │
        └─────────┬───────────┘
                  ▼
            mac-2 cluster (8 worker, 4-model rotate)
```

每个 session 互相独立,通过 audit_gaps.yaml 通信。daemon 自跑。**Howard 你只动 yaml + 看 git log**,不要直接 SSH 操控 daemon(让它们自己活)。

---

**最后更新**: 2026-05-03 22:50 PT
**当前 daemon 状态**: 见 `harness.log` + `pgrep harness_loop` (mac-1 + mac-2 通过 ssh)
**当前 gap 状态**: `python3 -c "import yaml; from collections import Counter; print(Counter(g.get('status','pending') for g in yaml.safe_load(open('docs/audit_gaps.yaml'))['gaps']))"`
