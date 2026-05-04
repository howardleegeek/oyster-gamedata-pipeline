#!/usr/bin/env bash
# bin/harness_watchdog.sh — 5-min auto-heal watchdog for harness_loop.py
#
# 在 mac-1 + mac-2 上各跑一份 (cron 每 5 分钟):
#   */5 * * * * /Users/howardli/Downloads/oyster-agent-runner/bin/harness_watchdog.sh
#   */5 * * * * /Users/howardlee/oyster-gamedata-pipeline/bin/harness_watchdog.sh
#
# 行为:
#   1. 检查 harness_loop.py 进程是否活
#   2. 若死: git pull + 启动 nohup daemon
#   3. 若活: 检查 audit_gaps.yaml 的 last_heartbeat 是否 < 600s 老
#      (lock 自己活的 daemon 应该至少每 ~30s 写 heartbeat)
#      若老 → kill + 重启
#   4. log 状态到 watchdog.log

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$REPO_ROOT/watchdog.log"
PYTHON="$(command -v python3)"
HEARTBEAT_TTL=600   # daemon should heartbeat every iter; 600s = stuck

ts() { date +"%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

cd "$REPO_ROOT" || { log "FATAL: cannot cd $REPO_ROOT"; exit 1; }

# Get latest state
git pull --quiet --rebase origin main 2>/dev/null || log "WARN git pull failed"

# Check daemon
PID=$(pgrep -f 'python3 bin/harness_loop.py' | head -1)

restart_daemon() {
  log "RESTARTING harness daemon ..."
  pkill -f 'python3 bin/harness_loop.py' 2>/dev/null
  sleep 2
  cd "$REPO_ROOT"
  nohup "$PYTHON" bin/harness_loop.py >> harness.log 2>&1 &
  NEW_PID=$!
  log "RESTARTED PID=$NEW_PID"
}

if [ -z "$PID" ]; then
  log "DAEMON DEAD (no PID); restarting"
  restart_daemon
  exit 0
fi

# Daemon alive — check heartbeat freshness
HB=$("$PYTHON" -c "
import yaml, time
try:
    with open('docs/audit_gaps.yaml') as f: d = yaml.safe_load(f)
    hb = d.get('harness_lock', {}).get('last_heartbeat', '')
    if not hb:
        print(99999); exit()
    t = time.mktime(time.strptime(hb, '%Y-%m-%dT%H:%M:%SZ'))
    print(int(time.time() - t))
except Exception as e:
    print(99999)
" 2>/dev/null)

if [ -z "$HB" ]; then
  HB=99999
fi

if [ "$HB" -gt "$HEARTBEAT_TTL" ]; then
  log "DAEMON ALIVE (PID=$PID) but heartbeat STALE ($HB s > ${HEARTBEAT_TTL}s) — restarting"
  restart_daemon
else
  log "OK PID=$PID heartbeat=${HB}s ago"
fi
