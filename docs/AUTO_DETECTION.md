# Auto-Detection System

> Howard 2026-05-08: 自动检测系统. Single orchestrator that polls every
> layer of the stack and emits a unified status.

`bin/detect_all.sh` aggregates 6 detection layers into one live dashboard,
a JSON status file, and macOS-native notifications on RED transitions.

---

## What it watches

| Layer | What | How |
|---|---|---|
| L0 | Mac host | `df -h /`, `uptime` (1m load) |
| L1 | Local stack | `docker ps` (Supabase containers) + curl probes (`:3000`, `:3001`, `/api/catalog`) |
| L2 | Auto-heal daemons | `pgrep` for `recorder_autoloop.sh`, `watch.sh` |
| L3 | Cluster jobs | `pgrep` for in-flight `claude --dangerously-skip` / `codex exec` |
| L4 | CI lanes | `gh run list --limit 5` |
| L5 | Auto-spec backlog | count `specs/auto/R-AUTO-*.md` |

Overall status is the worst of all rows: any RED → RED, else any YELLOW → YELLOW, else GREEN.

---

## Usage

```bash
# One-shot snapshot (human-readable)
./bin/detect_all.sh once

# One-shot JSON (for downstream consumers)
./bin/detect_all.sh json | jq .

# Live dashboard, refresh every 60s, macOS notifications on new RED
./bin/detect_all.sh loop

# Custom interval
INTERVAL=30 ./bin/detect_all.sh loop

# Suppress notifications
NOTIFY=false ./bin/detect_all.sh loop
```

---

## Output artifacts

- **Live console** — color-coded table, refreshed each cycle in `loop` mode.
- **`/tmp/oyster-detect-status.json`** — overwritten each cycle. Schema:
  ```json
  {
    "ts": "2026-05-08T22:33:37Z",
    "overall": "YELLOW",
    "counts": { "red": 0, "yellow": 3, "green": 8 },
    "rows": [
      { "layer": "L1", "code": "supabase.containers",
        "label": "supabase containers", "status": "YELLOW",
        "detail": "9/11 healthy" }
    ]
  }
  ```
- **macOS notifications** — `osascript display notification ...` with sound,
  fired only on transitions (a row going GREEN→RED or YELLOW→RED). Suppressed
  while a row stays RED across cycles to avoid spam.

---

## Iron-law guarantees

1. **Every row is a real probe.** `docker ps`, `curl -sIL`, `pgrep`, `gh run list`,
   `find` — never an invented status.
2. **No "looks healthy" without a source.** If a probe times out or returns
   nothing, the row is YELLOW with `gh unavailable` / `no response` detail —
   never silently GREEN.
3. **Notifications fire on transitions, not states.** A red row staying red
   across 10 cycles produces 1 notification, not 10.
4. **Status file is overwrite-on-cycle.** Downstream consumers read the latest
   atomic snapshot; never a half-written partial.

---

## Pairs with

- **`bin/recorder_autoloop.sh`** — feeds L2 (its own daemon) and L5 (auto-spec backlog).
- **`watch.sh`** — feeds L2 when running. Production-side HTTP probe daemon.
- **`bin/recorder_log_analyzer.py`** — feeds L5 indirectly (by writing the auto-specs).
- **`PRODUCTION_LAUNCH_SOP.md`** — Stage 5 incident response references this for triage.

---

## Sample run

```
═══ Oyster Auto-Detection — 2026-05-08T22:33:37Z ═══
  layer  detector                status   detail
  ─────  ──────────────────────  ───────  ──────────────────────────
L0  host disk               GREEN    62% used, 7.3Gi free
L0  host load (1m)          GREEN    6.33
L1  supabase containers     YELLOW   9/11 healthy
L1  tester :3000            GREEN    HTTP 200
L1  buyer :3001             GREEN    HTTP 200
L1  /api/catalog            GREEN    HTTP 200 (real DB)
L2  recorder autoloop       GREEN    PID 49361
L2  production watch.sh     YELLOW   not running (optional in local dev)
L3  cluster jobs            GREEN    0 running (idle)
L4  ci recent (5)           YELLOW   fail=1 ok=4 running=0
L5  auto-spec backlog       GREEN    1 specs in specs/auto/
  overall: YELLOW   (status file: /tmp/oyster-detect-status.json)
```

YELLOW interpretation: the 3 yellow rows are non-blocking noise (Supabase health-check is partial, watch.sh is intentionally off in local dev, CI fail is a known orchestration heartbeat). No RED means no real action needed.

— Howard Li, Oysterworld Inc, 2026-05-08
