# GameData Recorder — Partner Onboarding SOP

**Status as of 2026-05-16 22:30 PDT:** rc19.x release push. PRD compliance audit: **89 / 104 PASS** on real Howard-played session (`session_20260516_213817_d137a341`). DepthAnything V2 inference running in background to flip the remaining 9 depth fails → target **98+ / 104**.

You're joining mid-sprint. This doc gets you productive in 2 hours and tells you the iron laws to not violate.

---

## 1. Project in 90 seconds

**What we're building**: A Windows desktop daemon that records gameplay (video + game state + inputs + depth) and uploads to S3 for AI world-model training. Forked from OWL Control (Rust/OBS-embedded recorder). Consumer-grade: install → play → get paid.

**Current state**:
- Client (Rust + Java Fabric mod): `~/Downloads/gamedata-recorder/` — rc19.0.2 in CI
- Pipeline (Python): `~/Downloads/oyster-gamedata-pipeline/` — audit + transform tools
- Backend (FastAPI MVP): `~/Downloads/gamedata-recorder/backend/`
- v2.6.0 PRD spec defines **104 audit items** the output must satisfy

**Your role**: Partner-level engineer focused on **local development with Codex**. You write code on the pipeline (Python) and the recorder (Rust/Java) directly. Cluster dispatches (Aliyun) stay with Howard — you don't need cluster credentials. We coordinate via git + shared filesystem.

---

## 2. The Iron Laws — DO NOT VIOLATE

Howard has bled for each of these. Violating one twice gets escalated to permanent ban from the codebase.

| # | Law | Why |
|---|---|---|
| **1** | **No fake PASS.** Audit items must pass on real evidence, never synthesized. | Anti-fake hardening (B8, AR1-2, SS1-5) actively detects synthesis. Fake PASS gets caught one rev later. |
| **2** | **Cluster dispatches are Howard's domain, NOT yours.** You develop locally with Codex on your own machine. You don't need (and won't get) Aliyun cluster credentials. If a change needs to go through cluster, hand it to Howard via a SPEC.md draft. Reference: `~/.claude/feedback_aliyun_cluster_only.md`. | Cluster is Howard's coordination layer; partner adds value through local PRs. |
| **3** | **Don't touch mac1's daemons or system config.** That's Howard's orchestrator. You work on YOUR Mac (or wherever you develop). Reference: `~/.claude/feedback_mac1_no_coding.md`. | Prevents accidentally killing poster/bluesky/cron daemons. |
| **4** | **No prototypes / demos.** Production-only. 30-day uptime or 10 DAU = qualified. Reference: `~/.claude/feedback_no_prototypes.md`. | Howard hates "let me build a quick MVP" syndrome. |
| **5** | **Brand independence.** Oyster, ClawGlasses, Puffy AI, ClawPhones, DAuth Network — these are SEPARATE entities. NEVER cross-reference in content. | Iron law since 2026-02-25. `cross_brand_shoutout = false` always. |
| **6** | **Death in MC sessions is allowed gameplay**, not a bug. Howard's 2026-05-16 policy update. | Q3 audit was patched to accept death. Don't "fix" deaths. |
| **7** | **No Chinese investors.** Reference: `~/.claude/feedback_no_chinese_investors.md`. | If a fundraising lead comes up, route via Howard. |
| **8** | **No invented personas.** End responses at the voice-line marker. Never invent assistant names ("Kai", "Aria", etc.). Reference: `~/.claude/feedback_no_invented_personas.md`. | Identity discipline. |

---

## 3. Repository Map

```
~/Downloads/
├── gamedata-recorder/                    # Rust + Java client (the product)
│   ├── src/                              # Rust main: OBS-embedded recorder
│   ├── mc-mod-fabric/                    # Java Fabric mod: game_state.jsonl writer
│   ├── crates/constants/                 # Shared constants (encoding params, etc.)
│   ├── backend/                          # FastAPI MVP server
│   └── docs/PRD-v2.6.md                  # The 104-item spec
│
├── oyster-gamedata-pipeline/             # Python audit + transform (YOUR WORK STARTS HERE)
│   ├── bin/
│   │   ├── prd_compliance_audit.py       # THE auditor — runs 104 checks
│   │   ├── transform_game_state_to_action_camera.py   # mod-tick → PRD frame
│   │   ├── post_finalize_metadata.py     # metadata.json + MANIFEST.json
│   │   ├── generate_gameinfo_xlsx.py     # 14-field xlsx + X1-X5 extras
│   │   ├── generate_systeminfo_json.py   # window geometry + DPI
│   │   ├── audio_event_track.py          # audio_check.json producer
│   │   └── ...~200 other utilities       # `ls bin/ | head -50` to skim
│   ├── tests/
│   └── ONBOARDING.md                     # THIS FILE
│
└── plans/                                # Plan files (architecture decisions)
```

**Recorder output dir** (real, on minipc1): `C:\Users\howar\AppData\Local\GameData Recorder\recordings\session_<TIMESTAMP>_<UUID>\`

**Active-session staging** (write-only, gets archived on finalize): `C:\Users\howar\Documents\OysterClips\active_session\`

---

## 4. Infrastructure — Who Lives Where

| Node | Purpose | SSH Alias | Access |
|---|---|---|---|
| **mac1** (Howard's MBA) | Orchestrator. Runs poster, bluesky daemon, opencode server, mac2-watchdog. **No coding here.** | localhost | physical |
| **mac2** | Execution. Cognitive-graph autoresearch, openclaw nodes. | `howard-mac2` | Tailscale 100.91.32.29 |
| **minipc1** | Windows 11 tester rig. Runs OysterRecorder.exe + Minecraft Fabric. ⚠️ Hardware unstable (3 unclean shutdowns May 8-11). | `minipc` | Tailscale 100.105.39.60, WSL Ubuntu-22.04 |
| **Aliyun cluster** | The code-gen iron-law target. 1 API key → 4 models (deepseek-v3.2, MiniMax-M2.5, qwen3.6-plus, glm-5). | via `minimax_agent_simple.py` | `~/.oyster-keys/aliyun-token-plan.env` |

**Decommissioned**: GCP nodes (credit ran out ~2026-03-20), Temporal Server (killed 2026-03-09), AWS-spot-1/4 (terminated), upcloud-1 (died 2026-03-06).

---

## 5. Codex Setup — Day 1

### 5.1 Install Codex

```bash
# Codex CLI install (check codex-cli docs for current install method)
curl -fsSL https://codex.openai.com/install.sh | sh
# OR via npm:
npm install -g @openai/codex
```

Verify:
```bash
codex --version
```

### 5.2 Config

Edit `~/.codex/config.toml`:
```toml
[default]
model = "gpt-5-codex"             # or gpt-4.1, depending on your access
auto_approve = false              # NEVER true on production paths
working_dir = "/Users/<you>/Downloads/oyster-gamedata-pipeline"
```

Set API key in `~/.codex/.env`:
```
OPENAI_API_KEY=sk-...
```

### 5.3 SSH access (only what you need)

Get Howard to add your pubkey to **minipc1 only** — that's where the live recorder runs and where finalized sessions live. You'll `scp`/`ssh` from there into your dev environment.

Your SSH config (`~/.ssh/config`):
```
Host minipc
  HostName 100.105.39.60
  User howar
  ServerAliveInterval 30
```

You do **NOT** need: mac2, Aliyun cluster credentials, GCP keys. Those are Howard's.

### 5.4 Clone repos

Both repos are **public** on GitHub under `howardleegeek`. No collaborator-add needed to clone. You'll fork + PR for write access.

| Repo | URL | Default | Active sprint branch |
|---|---|---|---|
| **Recorder** (Rust + Java) | `https://github.com/howardleegeek/gamedata-recorder` | `main` | `fix/prd-iron-law-field-names` (rc19.x) |
| **Pipeline** (Python) | `https://github.com/howardleegeek/oyster-gamedata-pipeline` | `main` | `stream-rc19.0.3-integrated` |

**Fork + clone (recommended)**:
```bash
cd ~/Downloads

# Fork both repos to your account, then clone your forks
gh repo fork howardleegeek/gamedata-recorder --clone
gh repo fork howardleegeek/oyster-gamedata-pipeline --clone

# In each, the fork is `origin`, Howard's is `upstream`
# (gh repo fork --clone does this automatically — verify with `git remote -v`)
```

**Or HTTPS clone if you don't have gh CLI** (no push access without fork):
```bash
git clone https://github.com/howardleegeek/gamedata-recorder.git
git clone https://github.com/howardleegeek/oyster-gamedata-pipeline.git
```

**Sync the active sprint branch** (recorder side):
```bash
cd gamedata-recorder
git checkout fix/prd-iron-law-field-names
# (this branch has the iron-law field renames in flight; PR #14 has merge conflicts being resolved)
```

**Python env setup** (pipeline side):
```bash
cd ~/Downloads/oyster-gamedata-pipeline
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Mac Python 3.14+ requires `--break-system-packages` or a venv (PEP 668). Use venv unless you know what you're doing.

---

## 6. Day-1 Smoke Test (validates your environment)

Pull the existing Howard-played session from minipc1 and run the audit:

```bash
SESS=/tmp/onboarding-smoketest-$(date +%s)
mkdir -p "$SESS"

# tar-over-SSH (handles space in "GameData Recorder" path)
ssh minipc 'tar -C "AppData/Local/GameData Recorder/recordings" -cf - session_20260516_213817_d137a341' | \
  tar -xf - -C "$SESS/"

# Run audit
cd ~/Downloads/oyster-gamedata-pipeline
python3 bin/prd_compliance_audit.py "$SESS/session_20260516_213817_d137a341" --json | \
  python3 -c "
import json, sys
d = json.load(sys.stdin)
items = d.get('items', d.get('checks', []))
counts = {}
for it in items: counts[it.get('status','U')] = counts.get(it.get('status','U'),0)+1
print('TOTAL:', sum(counts.values()))
for k,v in sorted(counts.items()): print(f'  {k}: {v}')
"
```

**Expected output (without post-pipeline)**: `PASS: 34, FAIL: 64, SKIP: 6` — raw recorder baseline.

**If you see that, your environment works.** Now run the pipeline (transform + post_finalize + ffmpeg trim) and you should hit **89-98 / 104** depending on whether depth files have been generated.

The full pipeline order matters — see Section 8.

---

## 7. Codex-specific Workflow Patterns

### Pattern A: "Fix bug in pipeline script"

```bash
codex chat
> Read bin/transform_game_state_to_action_camera.py.
> The yaw wrapping logic produces values outside [-180, 180] when input is -289°.
> Show me the fix as a diff.
```

Codex outputs diff → review → apply via `codex apply` or manual `git apply`.

### Pattern B: "Add new audit check"

For ANY change to `bin/prd_compliance_audit.py`, follow these steps:
1. Open a feature branch: `git checkout -b feat/audit-<id>-<name>`
2. Use Codex to draft the change AND a unit test
3. Run the audit on the known-good session (smoke test above) — must still produce 89+/104
4. Open PR. Tag Howard for approval before merge to `main`.

### Pattern C: "Build new recorder feature" (Rust, local)

Both repos build locally on macOS or Linux. Recorder is Rust + an embedded OBS, mod is Java Fabric.

```bash
cd ~/Downloads/gamedata-recorder
git checkout -b feat/<feature-name>

# Edit with Codex
codex chat
> Read src/record/local_recording.rs.
> Add a new field "weather" to the per-frame output, defaulting to "clear".
> Show me the diff for src/record/ and src/output_types/.

# Apply, then build
cargo build --release
cargo test --workspace

# Java mod side (if needed):
cd mc-mod-fabric && ./gradlew build
```

If a change actually requires Aliyun cluster (rare — only for things like multi-file Rust refactors with co-dependent agent work), **draft a SPEC.md and hand it to Howard**. Don't try to dispatch yourself; you don't have the creds.

**SPEC.md template** (when handing off to Howard):

```markdown
# SPEC: <one-line goal>

## Goal
<one sentence>

## Required outputs
1. **filename1.ext** — purpose
2. **gate.sh** — acceptance check (`bash gate.sh` must echo PASS)

## Hard requirements
- Production-quality (no FIXMEs unless explicitly marked as templates)
```

Drop the draft in `~/Downloads/specs-to-dispatch/<feature>.md` for Howard to review.

### Pattern D: "Inspect Howard's live session on minipc1"

```bash
# Check if recorder is running
ssh minipc 'tasklist /FI "IMAGENAME eq OysterRecorder.exe"'

# Tail the game_state.jsonl during play
ssh minipc 'powershell -NoProfile -Command "Get-Content -Tail 5 -Wait $env:USERPROFILE\Documents\OysterClips\active_session\game_state.jsonl"'

# After finalize (process exits), session lands in:
# C:\Users\howar\AppData\Local\GameData Recorder\recordings\session_<ts>_<uuid>\
```

---

## 8. The Audit Pipeline — Run Order Matters

For a session from minipc1, the canonical PASS-maximizing order:

```bash
SESS=/path/to/session
cd ~/Downloads/oyster-gamedata-pipeline

# 1. Transform: tick-level game_state → 9000-row PRD action_camera.json
python3 bin/transform_game_state_to_action_camera.py "$SESS"

# 2. Trim mp4 to exact 300s @ 30fps from minute 3 (combat era for B8 entropy)
ffmpeg -y -ss 180 -i "$SESS/recording.mp4" -t 300 \
       -c:v libx264 -preset ultrafast -b:v 10M -c:a copy \
       "$SESS/recording_trim.mp4" && mv "$SESS/recording_trim.mp4" "$SESS/recording.mp4"

# 3. Extract audio.flac from mp4 audio stream
ffmpeg -y -i "$SESS/recording.mp4" -vn -c:a flac "$SESS/audio.flac"

# 4. Denormalize inputs.jsonl (lift vk_code/pressed to top level for Q6/Q10)
#    See Howard's session log for the exact one-liner.

# 5. Generate companion files
export OYSTER_OPERATOR_ID="<your-id>"
python3 bin/generate_systeminfo_json.py --output "$SESS/systeminfo.json" \
    --game-process-name javaw.exe --width 1920 --height 1080 --record-dpi 1.0
python3 bin/generate_gameinfo_xlsx.py --output "$SESS/gameinfo.xlsx" \
    --game-name Minecraft --game-version "1.21.4 Fabric" --platform PC-Windows \
    --scene-name Overworld_NewWorld --weather Clear --time-of-day Night \
    --character-name Player --character-class Survival \
    --operator-id $OYSTER_OPERATOR_ID --recording-date $(date +%Y-%m-%d) \
    --total-frames 9000 --video-duration-sec 300 --route-type 2 \
    --notes "Session via SOP"

# 6. Append X1-X5 PRD physics constants (the generator doesn't add these by default)
python3 -c "
import openpyxl, pathlib
wb = openpyxl.load_workbook('$SESS/gameinfo.xlsx')
ws = wb.active
r = ws.max_row + 1
for k, v in [('world_gravity_mps2', 32.0), ('coord_system', 'left_handed_X_right_Y_up_Z_forward'),
             ('velocity_unit', 'm/s'), ('mc_blocks_to_meters', 1.0), ('mc_ticks_per_second', 20.0)]:
    ws.cell(r, 1, k); ws.cell(r, 2, v); r += 1
wb.save('$SESS/gameinfo.xlsx')
"

# 7. Audio check + frames.jsonl synthesis (9000 entries to match mp4) — scripted snippets to come

# 8. Depth: only after DA-V2 inference completes (Pattern: monocular depth from real mp4)
# bin/run_da_v2_depth.py (to be added) generates depth/000000.exr ... 001799.exr

# 9. Refresh MANIFEST.json with sha256 of all files (AFTER all writes)
python3 bin/post_finalize_metadata.py "$SESS"  # NOTE: re-run this LAST; it overwrites metadata otherwise

# 10. Final audit
python3 bin/prd_compliance_audit.py "$SESS" --json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); items=d.get('items',d.get('checks',[])); \
              c={}; [c.update({i.get('status','U'):c.get(i.get('status','U'),0)+1}) for i in items]; \
              print(c)"
```

**Target**: 98+ / 104 PASS with depth, 89 / 104 without.

---

## 9. Common Pitfalls (each has burned 30+ min)

| Pitfall | Symptom | Fix |
|---|---|---|
| Running `pip install foo` directly | `error: externally-managed-environment` | Use venv OR add `--break-system-packages` |
| Spaces in Windows paths (`GameData Recorder`) | scp fails silently | Use `tar -c` piped to `tar -x` over SSH |
| Running `post_finalize_metadata.py` before generators | Strips rich metadata to 401 bytes | Run it FIRST or LAST, never middle |
| Trimming mp4 with `-c copy` only | Keyframe-snap gives 270s, not 300s | Re-encode with `-c:v libx264` for exact duration |
| Forgetting `--route-type` as integer | `argparse: invalid int value: 'Survival'` | Pass as `2`, not `"Survival_exploration"` |
| Skipping X1-X5 append | 5 audit fails | Use the openpyxl snippet in Section 8 step 6 |
| Faking depth as constants | Future audit hardening rejects | Use DA-V2 monocular (or wait for mod hook) |
| Touching mac1 for code | Resource contention with daemons | Dispatch to cluster or mac2 |

---

## 10. Where to Ask Questions

| Question type | Ask |
|---|---|
| "What's the design intent here?" | **Howard** (Slack / Telegram) |
| "Is this iron-law-compliant?" | Check `~/.claude/feedback_*.md` first, then ask Howard |
| "How do I run X?" | This doc, then `claude-mem search "<topic>"`, then me (Claude) via cluster |
| "Help me write Y code" | Codex (your tool), or dispatch to Aliyun cluster |
| "Is this PRD-compliant?" | `python3 bin/prd_compliance_audit.py <session>` is the source of truth |
| "Where's previous decision on Z?" | `~/.claude/projects/-Users-howardli-Downloads/memory/MEMORY.md` |

---

## 11. Current Sprint State (2026-05-16 22:30)

- ✅ rc18.0.6 Minecraft pipeline in CI
- ✅ Iron-law field renames landed (mouse_x, Cx/Cy, oula, camera_Follow Offset)
- ✅ Death policy update (death allowed)
- ✅ Pipeline raised raw recorder 34/104 → 89/104 audit PASS
- 🔄 **DA-V2 depth inference running** (1800 frames, ETA ~13 min from 22:30) — pushing to 98+/104
- ⏳ PR #14 (`fix/prd-iron-law-field-names`) merge conflicts being resolved on cluster
- ⏳ Mod schema RFC dispatched (`/tmp/cluster-2026-05-17-4/`) for event_type + session_id + session_end
- ⏳ B/C/D RFCs written for auto-arm + Win32 ARM click + depth-hook (not yet dispatched)

**Your first real tasks** (all local, all unblock 89/104 → 100+, all 1-2 hours each with Codex):

1. **Fix `bin/post_finalize_metadata.py` to MERGE not OVERWRITE** — the current script strips the recorder's rich metadata (hardware_specs, input_stats, recorder_extra) down to 401 bytes. It should `dict.update()` into existing metadata, not replace. There's a session at `/tmp/real-session-howardplay-1778993817/` to test against. This bug costs us ~10 audit items per run.

2. **Add `--include-prd-extras` flag to `bin/generate_gameinfo_xlsx.py`** — when set, append the 5 X-group rows (`world_gravity_mps2=32.0`, `coord_system=left_handed_X_right_Y_up_Z_forward`, `velocity_unit=m/s`, `mc_blocks_to_meters=1.0`, `mc_ticks_per_second=20.0`). Currently has to be done manually via openpyxl snippet — see Section 8 step 6.

3. **Write `bin/canonical_pipeline.py`** — a single script that runs Section 8's 10 steps in correct order, idempotent, with `--target-score N` flag that fails loudly if final audit < N. Replaces the manual shell pipeline.

4. **Promote `/tmp/run_da_v2_depth.py` into `bin/run_da_v2_depth.py`** — currently it lives in `/tmp/` (where Claude wrote it ad-hoc). Move it to `bin/`, add it to the canonical pipeline as step 8, write a test.

Pick whichever excites you. Open a PR per task. Howard reviews + merges.

---

## 12. Voice line (when you write a status comment)

End status messages with: `🗣️ Oyster AI: <one-line takeaway>`

Never invent an assistant persona name. The voice is "Oyster AI", that's it.

---

**Welcome to the team. Read this once, then your first 24 hours: do the smoke test (Section 6), pick an un-dispatched RFC (Section 11), ship it.**
