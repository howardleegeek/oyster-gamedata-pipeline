# Harness Daemon Failover (mac-1 ↔ mac-2)

## 现状(2026-05-03)

- **Primary**: mac-1 nohup daemon running (`bin/harness_loop.py`)
- **Fall-back**: mac-2 standby (manual start only, NOT auto-running)

## Lock 机制

`docs/audit_gaps.yaml` 顶部 `harness_lock` field:
```yaml
harness_lock:
  host: <hostname>
  pid: <pid>
  last_heartbeat: <ISO8601 UTC>
```

每次 iteration 起手:
1. `git pull --rebase origin main` (拿最新 lock 状态)
2. 检查 `harness_lock`:
   - 若 host == 本机 → 续 heartbeat
   - 若 host ≠ 本机 + heartbeat < 180s → 退出 (other host alive)
   - 若 host ≠ 本机 + heartbeat ≥ 180s (stale) → 抢锁 + push 新 heartbeat

iteration 末尾: refresh heartbeat + commit + push (other host can see)

## Fall-back 启动(mac-1 死时)

如果 mac-1 关机/sleep > 3 分钟,mac-2 上跑:

```bash
ssh howard-mac2
cd ~/oyster-gamedata-pipeline
git pull --rebase origin main
nohup python3 bin/harness_loop.py > harness.log 2>&1 &
echo "Mac-2 fall-back PID=$!"
```

mac-2 daemon 启动后:
1. git pull 拿最新 lock
2. 看到 mac-1 heartbeat stale (> 180s) → claim lock
3. 开始正常 dispatch / collect / commit
4. 每 iteration push heartbeat → mac-2 现在是 owner

## 切回 mac-1(mac-1 醒来)

mac-1 醒来后,如果 daemon 还活(launchd) — daemon 下次 iteration:
1. git pull 看到 mac-2 是 owner + heartbeat fresh → 自己退出

如果 mac-1 daemon 已经死(关机时):
1. Howard 手动重启 mac-1 daemon
2. 看到 mac-2 fresh → 退出
3. **手动决定** 切回 mac-1: ssh mac-2 pkill harness,等 180s,mac-1 重启

## 常用命令

```bash
# mac-1 看 daemon 状态
ps -ef | grep harness_loop | grep -v grep

# mac-1 看 daemon 日志
tail -f /Users/howardli/Downloads/oyster-agent-runner/harness.log

# mac-1 杀 daemon
pkill -f 'python3 bin/harness_loop.py'

# mac-1 启 daemon
cd /Users/howardli/Downloads/oyster-agent-runner
nohup python3 bin/harness_loop.py >> harness.log 2>&1 &

# mac-2 完全等价命令(只是用 ssh howard-mac2 进 mac-2 后跑)
ssh howard-mac2 'cd ~/oyster-gamedata-pipeline && nohup python3 bin/harness_loop.py >> harness.log 2>&1 &'

# 看 lock 现状
cat docs/audit_gaps.yaml | head -3
```

## 已知限制

1. **mac-2 daemon 不能完全独立工作** — 它需要 ssh 到自己来 dispatch (因为 harness_loop.py 硬编码 MAC2_HOST="howard-mac2"). Future fix: detect local host, run minimax_agent_simple.py 直接 subprocess (no ssh).

2. **网络分区** — 若 mac-1 离线 + mac-2 也无法 git push,两边都 stuck. 实际很少 hit.

3. **commit race** — 两个 daemon 同时 push 同 commit hash 极小概率,但 lock TTL=180s 已大幅缓解.

## 当前部署

| Host | Status | Role | Auto-restart |
|---|---|---|---|
| mac-1 | nohup PID 1294 | **PRIMARY** (active) | ❌ (重启需手动) |
| mac-2 | not running | **STANDBY** (manual fallback) | ❌ |

## 升级路径

- 短期: 当前足够 (mac-1 normally on)
- 中期: launchd LaunchAgent on mac-1 (need explicit user approval, blocked)
- 长期: 修 harness_loop.py 让 mac-2 native run (no self-ssh) → mac-2 真 24/7
