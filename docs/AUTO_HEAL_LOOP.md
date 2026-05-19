# Auto-Heal Loop — closed-loop recorder diagnostics

> Howard 2026-05-08: 自动跑 mc → 自动看日志 → 自动修理.
>
> Tester runs Minecraft + OysterRecorder.exe on Windows. The recorder
> already dumps `OysterRecorder_diagnostic.zip` on every session.
> This loop turns that diagnostic into a real spec + cluster dispatch
> automatically — no manual triage between failure and fix.

---

## Architecture

```
Windows (tester)              transport               Mac (this box)
──────────────────             ────────               ──────────────
MC + Recorder run                                      bin/recorder_autoloop.sh
  │                                                      │ polls every 60s
  │ session ends                                         ▼
  ▼                                                    new zip detected
OysterRecorder_diagnostic.zip                            │
  │                                                      ▼
  │ ── iCloud Drive sync ──┐                           bin/recorder_log_analyzer.py
  │ ── AirDrop ────────────┤                             │ classifies issues
  │ ── scp / rsync ────────┤                             │ extracts run-info
  │ ── manual drag ────────┘                             ▼
                                                       specs/auto/R-AUTO-<ts>.md
                                                         │
                                                         ▼
                                                       claude-glm cluster dispatch
                                                         │
                                                         ▼
                                                       GLM reads spec, references R01,
                                                       proposes sub-spec or comments
                                                         │
                                                         ▼
                                                       state file marks zip processed
                                                       (sha256-dedup)
```

---

## Quick start (Mac side)

```bash
cd ~/Downloads/oyster-agent-runner

# Foreground (see live activity, Ctrl-C to stop):
./bin/recorder_autoloop.sh

# Background (daemonize, log to file):
nohup ./bin/recorder_autoloop.sh > /tmp/recorder-autoloop.log 2>&1 &

# Report-only (no cluster dispatch):
DISPATCH=none ./bin/recorder_autoloop.sh

# Custom drop dir (e.g. iCloud Drive folder):
WATCH_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Oyster" ./bin/recorder_autoloop.sh
```

Environment knobs:

| Var | Default | Purpose |
|---|---|---|
| `WATCH_DIR` | `~/Downloads` | Where Howard / testers drop diag zips |
| `INTERVAL` | `60` (seconds) | Poll cadence |
| `STATE_FILE` | `~/.oyster-recorder-autoloop-state.json` | SHA-256 dedup state |
| `REPO_ROOT` | `~/Downloads/oyster-agent-runner` | Where to write `specs/auto/*.md` |
| `DISPATCH` | `glm` | One of `glm` / `codex` / `none` |
| `ZAI_API_KEY` + `ZAI_BASE_URL` | (required for `DISPATCH=glm`) | Z.AI tokens |

---

## Tester side (Windows)

The recorder already produces `OysterRecorder_diagnostic.zip` on the
"Send log" button click and on session end. Tester just needs to deliver
the zip to the Mac's `WATCH_DIR`. Three paths, ordered by ease:

1. **iCloud Drive** — both Mac and Windows have iCloud installed; drop the
   zip into a shared `Oyster/` folder, Mac sees it within seconds. Set
   `WATCH_DIR` to that iCloud path.
2. **AirDrop** — manual but instant. Tester sends to the Mac, lands in
   `~/Downloads`, default `WATCH_DIR` picks it up next poll.
3. **scp** — for headless setups: `scp OysterRecorder_diagnostic.zip user@mac:~/Downloads/`.

> **Privacy note:** the diag zip contains the recorder log + sysinfo
> only — no recordings, no PII beyond the tester's home directory path
> string.

---

## Output format

When a zip is processed and issues are found, the loop writes a real
spec to `specs/auto/R-AUTO-<UTC-timestamp>.md`. Example structure:

```yaml
---
task_id: R-AUTO-20260508T213039Z
project: recorder-autoloop
priority: 2
estimated_minutes: 30
depends_on: [R01-recorder-iron-law-polish]
executor: glm-aliyun
source_zip: OysterRecorder_diagnostic.zip
source_sha256: 9480ac4911a6e9df...
---
# Auto-generated from real OysterRecorder diagnostic
...
issues: [
  { code: FULL_DESKTOP_CAPTURE, line: 318, evidence: '...' },
  { code: PLACEHOLDER_GAMESTATE, line: 320, evidence: '...' }
]
```

`specs/auto/*.md` is **gitignored** (only `.gitkeep` tracked) — these
are operational artifacts, not source of truth. If a particular auto-run
surfaces something worth permanent fix, hand-write a spec into
`specs/W*` or `specs/R*` referencing the auto-spec.

---

## Iron-law guarantees

The auto-heal loop honours the same iron-law commitments as the rest of
the pipeline:

1. **Every issue cites a literal log line.** The analyzer never invents
   a failure pattern — it pattern-matches against a documented catalogue
   in `bin/recorder_log_analyzer.py` (PATTERNS list) and surfaces the
   matching line verbatim as evidence.
2. **Pattern catalogue is reviewed.** Adding a new pattern requires
   updating the PATTERNS list with `code`, `severity`, `regex`,
   `summary`, `suggested_spec`. No silent additions.
3. **Unknown errors → unclassified bucket.** Lines with ERROR/FAIL/
   Exception that don't match any documented pattern go into
   `unclassified_errors` so they get human attention rather than wrong
   classification.
4. **Cluster dispatch is opt-in.** `DISPATCH=none` mode runs the
   classifier and writes specs but doesn't touch the cluster — useful
   for offline triage or when Z.AI tokens aren't available.
5. **SHA-256 dedup** prevents duplicate specs / dispatches when the
   same zip is re-dropped.

---

## Pattern catalogue

| code | severity | suggested spec | what it means |
|---|---|---|---|
| `FULL_DESKTOP_CAPTURE` | critical | R01 | Recording captured whole desktop instead of MC window (privacy violation) |
| `PLACEHOLDER_GAMESTATE` | critical | R01 | Tarball shipped with `[0,64,0]` placeholder coords (Fabric mod missing) |
| `FFMPEG_FATAL` | high | new | ffmpeg errored mid-capture; tarball may be incomplete |
| `UPLOAD_FAILED` | high | new | Upload to /api/upload-tarball failed |
| `UNCAUGHT_EXCEPTION` | high | new | Python traceback escaped to log |
| `DEPTH_INFERENCE_INTERRUPTED` | medium | none | User disarmed during DepthAnything V2 run |
| `AUDIO_DEVICE_MISSING` | low | none | No audio capture device; video-only (expected on most machines) |
| `UPDATE_REFUSED_SINGLE_EXE` | low | none | Auto-updater refused to overwrite --onedir bundle (expected) |

Add patterns in `bin/recorder_log_analyzer.py`'s `PATTERNS` list. Each
addition needs a real evidence line from at least one diag zip before
being committed (iron-law: don't pre-pattern speculative failures).

---

## Smoke verification

The loop has been smoke-tested against Howard's real diag zip from
2026-05-08 14:08 (Windows-10, recorder lite-v0.26.0-real-game-state,
MC 26.2 Snapshot 6):

```
new diag zip: OysterRecorder_diagnostic.zip (sha=9480ac4911a6)
analyzer: 5 issues (2 critical)
[OK] spec written: specs/auto/R-AUTO-20260508T213039Z.md
```

Both critical issues classified correctly with exact line numbers
(L318 FULL_DESKTOP_CAPTURE, L320 PLACEHOLDER_GAMESTATE) and pointed
at the in-flight R01 spec.

---

## See also

- [`bin/recorder_log_analyzer.py`](../bin/recorder_log_analyzer.py) — the analyzer
- [`bin/recorder_autoloop.sh`](../bin/recorder_autoloop.sh) — the watcher
- [`specs/R01_recorder_iron_law_polish.md`](../specs/R01_recorder_iron_law_polish.md) — the canonical fix spec
- [`PRODUCTION_LAUNCH_SOP.md`](../PRODUCTION_LAUNCH_SOP.md) — how this fits into the full launch flow
- [`PRODUCTION_GAPS.md`](../PRODUCTION_GAPS.md) — gap audit

— Howard Li, Oysterworld Inc, 2026-05-08
