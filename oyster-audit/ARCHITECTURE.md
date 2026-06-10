# GameData Recorder — System Architecture (2026-05-16)

> Single-page map of the entire pipeline from operator launch → buyer-ready
> session. All file paths real, all commit SHAs verifiable. **No vapor.**

---

## Layer 0 — Repos (2 distinct, 3 working copies)

```
github.com/howardleegeek/
  ├── gamedata-recorder       (Rust + OBS, Windows recorder, forked OWL Control)
  │      └── fix/prd-iron-law-field-names @ 83156aed
  │
  └── oyster-gamedata-pipeline (Python pipeline + audit + dispatch)
         ├── feat/prd-compliance-audit-tool @ 193b390b  (audit + metadata utils)
         └── stream-rc19.0.3-coord-units    @ b571e004  (runtime — finalize_session.py lives here)
                                            └── checked out at ~/Downloads/oyster-agent-runner
```

**Why 2 checkouts of the same repo**: `oyster-gamedata-pipeline` (audit branch)
and `oyster-agent-runner` (rc19.0.3 branch) are the SAME git repo on different
branches. The agent-runner directory holds the runtime `bin/finalize_session.py`
that operators actually call; the audit branch holds the verification tools.

---

## Layer 1 — Runtime stack (operator → buyer)

```
┌──────────────────────────────────────────────────────────────────────┐
│                    OPERATOR'S PC (Windows, minipc2)                  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │              gamedata-recorder.exe (Rust)                  │      │
│  │  ┌──────────────────┐    ┌──────────────────────────────┐  │      │
│  │  │  OBS embedded    │    │     Tokio async runtime       │  │      │
│  │  │  H.264 encoder   │    │  ┌────────────────────────┐   │  │      │
│  │  │  → recording.mp4 │    │  │ input-capture crate     │   │  │      │
│  │  └──────────────────┘    │  │   (Win32 hooks, XInput) │   │  │      │
│  │           ▲              │  │   → inputs.jsonl        │   │  │      │
│  │           │              │  │   → action_camera.json  │   │  │      │
│  │           │              │  └────────────────────────┘   │  │      │
│  │           │              │  ┌────────────────────────┐   │  │      │
│  │           │              │  │ frame timestamp logger  │   │  │      │
│  │           │              │  │   → frames.jsonl        │   │  │      │
│  │           │              │  └────────────────────────┘   │  │      │
│  │           │              └──────────────────────────────┘  │      │
│  │           │                          ▲                     │      │
│  │           │                          │                     │      │
│  │  ┌────────┴──────────────────────────┴─────────────────┐   │      │
│  │  │       Minecraft Fabric Mod IPC (planned A21/A22)    │   │      │
│  │  │       → game_state.jsonl (player pos/rot per tick) │   │      │
│  │  └─────────────────────────────────────────────────────┘   │      │
│  └────────────────────────────────────────────────────────────┘      │
│           ▼                                                          │
│  recording.mp4 + frames.jsonl + inputs.jsonl + action_camera.json    │
│  + game_state.jsonl + systeminfo.json                                │
│           │                                                          │
│           ▼                                                          │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │   python3 bin/finalize_session.py <session_dir>            │      │
│  │     (1186 LOC in oyster-agent-runner repo, b571e004)       │      │
│  │                                                            │      │
│  │   [1/6] sync_game_state          ← mc-mod fallback path   │      │
│  │   [2/6] backfill_action_camera   ← quat + pos from gs     │      │
│  │         compute_mouse_look_vector  ← A24 cumulative yaw   │      │
│  │         resample_action_camera_to_frames ← A23 → 9000 rows│      │
│  │   [3/6] generate_gameinfo + _augment_gameinfo_coords (X)  │      │
│  │   [4/6] generate_depth_exr  ← DA-V2 inference (H7 path)   │      │
│  │   [5/6] generate_audio_check + extract_audio_flac (U2)    │      │
│  │   [6/6] generate_input_latency                            │      │
│  └────────────────────────────────────────────────────────────┘      │
│           │                                                          │
│           ▼                                                          │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │   python3 bin/post_finalize_metadata.py <session_dir>      │      │
│  │     metadata.json (session_id UUID4 + device + UTC + M5)   │      │
│  │     MANIFEST.json (sha256 + size per file)                 │      │
│  └────────────────────────────────────────────────────────────┘      │
│           │                                                          │
│           ▼                                                          │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │   python3 bin/prd_compliance_audit.py <session_dir>        │      │
│  │     77 checks across 13 groups → JSON / Markdown report    │      │
│  │     --fix mode self-heals legacy field names               │      │
│  └────────────────────────────────────────────────────────────┘      │
│           │                                                          │
└───────────┼──────────────────────────────────────────────────────────┘
            ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │              session_<id>.tar.gz uploaded to buyer S3            │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Layer 2 — Output artifacts (PRD §3)

| File | Producer | PRD constraint |
|---|---|---|
| `recording.mp4` | OBS encoder | 1920×1080 / 30 fps / H.264 / ≤12 Mbps / AAC audio / 300-360s |
| `action_camera.json` | recorder + A23 + A24 | 9000 rows × 20 PRD-named fields, mouse_x/y as look-vector |
| `gameinfo.xlsx` | `generate_gameinfo.py` | 14 PRD fields + 5 X-group (gravity/coord/units) |
| `depth/000000-001799.exr` | `depth_exr_writer.rs` (DA-V2 or MC mod) | 1800 × 1920×1080 float32 single-channel Z @ 6fps |
| `audio.flac` | `extract_audio_flac` (U2) | Lossless audio split from mp4 |
| `audio_check.json` | `audio_continuity_check.py` | Silence-gap detection |
| `input_latency.json` | `generate_input_latency` | Post-hoc input→frame latency |
| `inputs.jsonl` | `input_recorder.rs` | One event per line, JSONL |
| `systeminfo.json` | recorder startup | 5 fields (currently — pending C6 decision) |
| `metadata.json` | `post_finalize_metadata.py` | session_id UUID4, device, UTC timestamps, recorder_version |
| `MANIFEST.json` | `post_finalize_metadata.py` | sha256 + size per file (tamper detection) |

---

## Layer 3 — Dispatch infrastructure (Aliyun cluster)

```
┌─────────────────────────────────────────────────────────────────────┐
│   mac1 (orchestrator) — runs minimax_agent_simple.py                │
│      reads SPEC.md → calls Aliyun API → writes code to WORKING_DIR  │
└─────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│   Aliyun Token Plan API                                             │
│   key: ~/.oyster-keys/aliyun-token-plan.env                         │
│   models: deepseek-v3.2  /  MiniMax-M2.5  /  qwen3.6-plus  /  glm-5 │
└─────────────────────────────────────────────────────────────────────┘
                │
                ▼ (autonomous tool loop: list_files → read_file → write_file → run_cmd → finish)
┌─────────────────────────────────────────────────────────────────────┐
│   WORKING_DIR/   — agent writes here                                │
│   Iron contract: must pass gate.sh acceptance script (no theater)   │
└─────────────────────────────────────────────────────────────────────┘
```

**Dispatched 2026-05-16 (all 40-turn budget):**

| RFC | Working dir | Status | Output |
|---|---|---|---|
| autoresearch | `/tmp/cluster-2026-05-16/autoresearch/` | DONE | 1 docstring finding → fixed `b571e00` |
| RFC-A21-A22 (MC mod IPC) | `/tmp/cluster-2026-05-16/a21-a22/` | DONE | Java Fabric mod skeleton + Rust IPC client + roundtrip test + gate.sh (16 files, ~50% done) |
| RFC-H7 (real depth) | `/tmp/cluster-2026-05-16/h7/` | DONE | 329-LOC audit variant + tests (alternative impl, not adopted — mine wider) |

---

## Layer 4 — Compliance scoreboard

| View | Number | Notes |
|---|---|---|
| **MECE 92-item code-level** | **87 / 92 (95%)** | Verified via grep + unit tests, per-item commit links in `PRD-COMPLIANCE-SCORECARD.md` |
| **Audit tool E2E (with synth audio)** | **76 / 77 (99%)** | Real `finalize_session.py` + `post_finalize_metadata.py` + audit on gold sample |
| **Audit coverage of full MECE** | 77 / 92 (84%) | Q-group (operator behavior, 10 items) + a few D-constraints not yet automated |

### Remaining 5 items to 92/92

| ID | Blocker | Owner | ETA |
|---|---|---|---|
| **A21, A22** | MC mod IPC needs Java + Rust IPC build | Aliyun cluster (skeleton done) → Howard merge | 1 week dev |
| **H7** | DA-V2 inference unverified on minipc2 hw | Howard records → I audit | 1 day |
| **C6** | systeminfo.json schema doc contradiction (5 vs 7 fields) | Howard decides | 1 conversation |
| **Q1-Q10 subset** | Operator behavior during recording | Howard records → I audit | Recording session |

---

## Layer 5 — Audit tool internal structure

13 groups, 77 checks, ~440 LOC in `bin/prd_compliance_audit.py`:

| Group | Items | Coverage |
|---|---|---|
| **A** files present | 5 | recording.mp4, action_camera.json, gameinfo.xlsx, metadata.json, depth/ |
| **V** mp4 properties | 8 | resolution / fps / duration / codec / bitrate / audio |
| **C** 20 PRD field-name literals | 20 | strict field-name presence in action_camera.json |
| **D** value constraints | 10 | frame continuity / ranges / quat norm / fx==fy |
| **E** coord system | 1 | quat order xyzw |
| **F** gameinfo 14 fields | 14 | xlsx cell-text contains literal field names |
| **X** rc19.0.3 extras | 5 | world_gravity / coord_system / velocity_unit / mc_blocks / mc_ticks |
| **H** depth EXR | 7 | filenames sequential / count / 1920×1080 / float32 / Z channel / metric / real |
| **M** metadata.json | 4 | UUID4 session_id / device != "unknown" / UTC suffixes / recorder_version != "unknown" |
| **F8** MANIFEST.json | 1 | file_count > 0 + sha256 per entry |
| **U** audio files | 1 | audio.flac exists + non-empty |
| **U-aux** audio_check | 1 | audio_check.json exists |
| **G15** operator_id | 1 | not in sentinel set {missing-config, vendor-001, DataPilot, ""} |

Self-healing: `--fix` mode renames legacy field names (`mouseX` → `mouse_x`, etc.)

---

## Layer 6 — Commit timeline (2026-05-15 → 16)

| Commit | Repo | What |
|---|---|---|
| `074f9f9f` | recorder | 4 iron-law field-name violations (mouse_x, Cx/Cy, Follow Offset, oula prefix) |
| `f35a35a2` | recorder | Add player_rotation_oula field (closes MECE C17) |
| `fdfdb55d` | recorder | Auto-detect MC username from launcher_profiles.json (G16) |
| `61b9d88f` | recorder | 3 bugs in generate_gameinfo: utcnow / fh leak / route_type crash |
| `1c578eab` | recorder | **I3** cyclic route_type counter |
| `83156aed` | recorder | **G15** strict operator_id with sentinel |
| `48f9911`  | pipeline | post_finalize_metadata.py (M1/M2/M3/M4/F8) |
| `3e8f271`  | pipeline | Audit extended to V/H/X groups |
| `99c2107`  | pipeline | 10 bugs from code review (D7/D10 false-green + D5 crash + MANIFEST relpath + idempotency) |
| `9efbda6`  | pipeline | **M5** auto-detect recorder_version from Cargo.toml |
| `af962e6`  | pipeline | Honest scorecard rewrite + cluster RFCs |
| `193b390`  | pipeline | Audit coverage 69 → 77 (M + U + G15 groups) |
| `1d538c4`  | agent-runner | **U2** extract_audio_flac |
| `6302778`  | agent-runner | **A24** mouse look-vector from cumulative dx/dy |
| `34de98a`  | agent-runner | **A23** resample to video-frame grid (9000 rows) |
| `b571e00`  | agent-runner | U2 docstring clarification (autoresearch finding) |

---

## Key design decisions (iron laws)

1. **PRD literal field names are sacred.** `camera_Follow Offset` (with space + capital F) and `*_oula` (拼音 not euler) are buyer-pipeline contracts — verified by audit C-group + 6 commits of serde rename attributes.
2. **Idempotency by sentinel.** Every finalize step writes a sentinel (`audio.flac` exists, `look_vector_applied: true`, `frame_aligned_applied: true`, `~/.oyster-route-counter`) so re-running is safe.
3. **Aliyun cluster for ≥1-week work, local for ≤1-day work.** Dispatch MC mod via cluster; do U2/I3/A24/A23 locally.
4. **Audit must be honest before it's useful.** PR #22 closed D7/D10 false-greens — the audit was lying about ✅. Now grep-evidence + audit-evidence converge.
5. **Operator behavior cannot be code-enforced.** Q-group items (no popups, no death, fullscreen, no macro) are recording-time discipline. Code can only detect after the fact.

---

*Single source of truth: this file. Last updated 2026-05-16 by the ship session that closed 7 MECE items.*
