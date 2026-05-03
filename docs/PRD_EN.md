# GameData Collection PRD v1.0 (English)

> **Version**: 1.0  **Date**: 2026-05-02  **Buyer**: Oysterworld INC
> **Status**: ✅ Released — vendors can quote and start first batch on receipt
> **Contact**: Howard Li · howard.linra@gmail.com · +1 (341) 250-6526
> **Reference repo**: <https://github.com/howardleegeek/oyster-gamedata-pipeline>
>
> 中文版: [PRD.md](PRD.md)

---

## 0. About this document

This is the **vendor execution manual**, not internal engineering notes.
After reading you can:
- Quote unit price + monthly capacity
- Pick a tech stack and start the first sample
- Run the full "record → package → submit → accept" loop end-to-end

**Companion docs** (read after PRD):
- [`BUYER_SPEC_V1.md`](BUYER_SPEC_V1.md) — field-level technical spec (types, bounds)
- [`VENDOR_ONBOARDING.md`](VENDOR_ONBOARDING.md) — 8-step SOP from zero to first valid clip
- [`SUBMISSION_FORMAT.md`](SUBMISSION_FORMAT.md) — tarball naming + upload + self-lint

---

## 1. Project background

### 1.1 Business context
Train **interactive world models** — given a stretch of gameplay video + player action history, the model predicts the next frame. Applications:
- Game AI / NPC behaviour generation
- Robotics simulation training data
- Spatial computing / VR scene generation

### 1.2 Data use
This batch trains a **joint visual + action encoder**, with these quality requirements:
- Frame and action **precisely synchronised** (≤ 20 ms latency)
- Player behaviour **diverse** (not idle, not pure W-forward)
- Trajectory **resembles real player** (not patrol script, not demo replay)
- Depth information **real** (not placeholder), used for 3D representation training

### 1.3 Commercial targets
| Metric | Target |
|---|---|
| First batch | 100 clips (~5 min each) |
| Monthly capacity | 1,000–3,000 clips/month |
| Total target | 50,000 clips by end of 2026 |
| Acceptance rate | ≥ 90 % (below 80 % triggers rework, no charge) |

---

## 2. Scope of work

### 2.1 We provide
- ✅ Complete technical spec (this PRD)
- ✅ Reference implementation (GitHub repo, MIT-licensed)
- ✅ Self-lint script (vendor runs locally before submitting)
- ✅ Sample tarball (`samples/buyer-spec-v1-rc1.tar.gz`)
- ✅ 7×24 technical support (Slack / WeChat group)
- ✅ 30 % advance payment, balance on acceptance

### 2.2 Vendor provides
- ✅ Recording staff + scheduling
- ✅ Capture machines (Windows / macOS / Linux)
- ✅ Recording, organising, packaging, submitting per PRD
- ✅ Submitting only tarballs that pass self-lint
- ✅ Re-recording failed clips (no charge)

### 2.3 Out of scope
- ❌ We do not staff your team
- ❌ We do not provide capture machines
- ❌ We do not reimburse network / power / software licences
- ❌ We reject hand-edited / composited footage

---

## 3. Deliverables (4-piece bundle)

Each deliverable corresponds to **one 5–6 minute recording**, packaged as one `.tar.gz`:

```
<clip_id>/
├── video.mp4              # 5–6 min, 1920×1080, 30 fps, H.264/H.265
├── action_camera.json     # 20-field telemetry per frame
├── gameinfo.xlsx          # Operator-curated metadata
└── depth/                 # Depth maps at 6 fps
    ├── 000000.exr
    ├── 000001.exr
    └── ...
```

| # | File | Required | Est. size | Notes |
|---|---|---|---|---|
| 1 | `video.mp4` | ✅ | 200–500 MB | Real game capture |
| 2 | `action_camera.json` | ✅ | 5–15 MB | JSON array, one record per frame |
| 3 | `gameinfo.xlsx` | ✅ | < 100 KB | Single sheet, fields in §3.3 |
| 4 | `depth/*.exr` | ✅ | 300–800 MB | OpenEXR float32 single-channel Z |

**Per-clip total**: 0.5–1.5 GB (scene-dependent)

### 3.1 video.mp4 hard constraints
- **Duration**: 5 ≤ x ≤ 6 minutes (out-of-range rejected)
- **Resolution**: 1920×1080 (system fullscreen AND game window)
- **Frame rate**: 30 fps **stable** (no dynamic FPS, no 60→30 downsample)
- **Latency**: action-to-frame ≤ 20 ms
- **Audio**: present, continuous, no environmental noise, no NPC dialog spam
- **Codec**: H.264 (default) or H.265 (allowed), CRF ≤ 23, AAC audio
- **Forbidden**: UI dialogs / inventory open / 1st↔3rd person switch / death / respawn

### 3.2 action_camera.json fields (20)
Full table in [`BUYER_SPEC_V1.md`](BUYER_SPEC_V1.md#action_camerajson--20-fields-per-frame). Summary:

```json
{
  "frame": 0,
  "time": "2026-05-02 15:30:45.000",
  "fps": 30.0,
  "route_type": 1,
  "mouse_x": 0.5, "mouse_y": 0.5,
  "mouse_dx": 0.01, "mouse_dy": -0.02,
  "keyCode": [87],
  "camera_position": {"x": 100.0, "y": 64.0, "z": 200.0},
  "camera_rotation_oula": {"x": 0.0, "y": 90.0, "z": 0.0},
  "camera_rotation_quaternion": {"x": 0, "y": 0.707, "z": 0, "w": 0.707},
  "camera_Follow Offset": {"x": 0.0, "y": 1.6, "z": 0.0},
  "camera_intrinsics": {"fx": 960.0, "fy": 960.0, "cx": 960.0, "cy": 540.0},
  "camera_speed": {"x": 0.0, "y": 0.0, "z": 4.317},
  "player_position": {"x": 100.0, "y": 64.0, "z": 200.0},
  "player_rotation_oula": {"x": 0.0, "y": 90.0, "z": 0.0},
  "player_rotation_quaternion": {"x": 0, "y": 0.707, "z": 0, "w": 0.707},
  "player_speed": {"x": 0.0, "y": 0.0, "z": 4.317},
  "metric_scale": 1.0
}
```

**Key constraints**:
- `fx == fy` (mandatory; intrinsics must be pinhole)
- Quaternion order: `[x, y, z, w]`, magnitude ≈ 1
- Angle ranges: pitch [-90, 90], yaw/roll [-180, 180]
- Coordinate system: **left-handed** (right +X, up +Y, forward +Z)
- Speed unit: m/s per axis
- `frame` must be continuous (0..N-1, no gaps, no dups)

### 3.3 gameinfo.xlsx (single sheet)
| Field | Type | Example |
|---|---|---|
| game_name | string | Minecraft |
| game_version | string | 1.20.4 |
| platform | string | Java Edition |
| scene_name | string | flat-overworld |
| weather | string | clear |
| time_of_day | string | day |
| character_name | string | DataPilot |
| character_class | string | spectator |
| operator_id | string | vendor-001-op-A |
| recording_date | string | 2026-05-02 |
| total_frames | int | 9000 |
| video_duration_sec | float | 300.0 |
| route_type | int | 1 |
| notes | string | Plain-area exploration |

### 3.4 depth/*.exr
- **Sample rate**: 6 fps (not 30) → 5 min × 6 fps = **1800 frames**
- **Format**: OpenEXR, single-channel named `Z`
- **Dtype**: float32
- **Unit**: metres (linear depth along optical axis Z)
- **Invalid pixels** (sky / clipped / transparent): 0
- **Filename**: timestamp-aligned with video → `000000.exr` (t=0s) → `000005.exr` (t≈0.83s) → ...

---

## 4. Path diversity requirements

### 4.1 Route types (route_type)
| Type | Name | Description | Required share |
|---|---|---|---|
| 1 | normal | Natural player-style movement | 50 % |
| 2 | special | Wall-hugging / ground-skimming / extreme angles / jumping | 25 % |
| 3 | loop | Last 10 s revisits start point | 25 % |

### 4.2 Input distribution
**Per N-clip batch must satisfy**:
- 50 % "normal" clips: real player free-action
- 50 % "wasd_balanced" clips: strict W=40 % / A=20 % / S=20 % / D=20 %

**Stationary time ≤ 10 %** (over 10 % full-frame idle → reject)

**At least one action per second** (camera rotation counts)

### 4.3 Forbidden behaviour
- ❌ Combat / mob-killing / pet-taming
- ❌ Inventory / menu / map open
- ❌ Mouse-wheel zoom
- ❌ NPC dialog (any open dialog box → reject)
- ❌ Death / respawn / scene change (must stay in same area ≤ 30 min)
- ❌ Single-clip frame **frozen ≥ 2 s** (loading screens count)

---

## 5. Recording methodology

### 5.1 Recommended stack (we have validated)
**Minecraft Java 1.20.4 + OBS Studio + DepthAnything V2** — full reference implementation provided:

| Component | Purpose | Notes |
|---|---|---|
| Minecraft Java 1.20.4 | Client (spectator gamemode) | Offline mode, no paid Mojang account needed |
| Paper 1.20.4 | Server (localhost:25565) | Flat world + RCON |
| Mineflayer | Headless bot driving behaviour | Node.js, we provide ScriptedProvider |
| OBS Studio + WebSocket v5 | Screen + audio capture, H.264 | obs-studio 30+ |
| DepthAnything V2 Small | Depth inference | HuggingFace, fp16 OK on M-series / RTX |

**Full code open-source**: <https://github.com/howardleegeek/oyster-gamedata-pipeline>

### 5.2 Alternative stacks (vendor's choice)
Any tech stack that meets the PRD acceptance gates is acceptable, e.g.:
- **CS2 / Valorant** spectator + auto-replay
- **GTA V** free-roam + ScriptHook + input replay
- **BeamNG.drive** drive mode + built-in telemetry
- **Unity / Unreal custom scenes** + DepthAnything V2 inference

**Vendor must verify**:
- Frames = real game render (not placeholder / not synthetic)
- Inputs = real keyboard+mouse (not script-injected to game state)
- Depth = real inference or G-buffer (not placeholder)

### 5.3 Minimum hardware
| Resource | Min | Recommended |
|---|---|---|
| OS | Win 10 / macOS 13 / Ubuntu 22.04 | Win 11 / macOS 14+ |
| CPU | 4-core 3.0 GHz | 8-core 3.5 GHz+ |
| RAM | 16 GB | 32 GB |
| GPU | GTX 1660 / Apple M1 | RTX 3060 / Apple M2 Pro+ |
| Disk | 1 TB SSD/month | 4 TB SSD |
| Network | 50 Mbps up | 200 Mbps up |

**Single-machine capacity**: 100–300 clips/month (scene-and-skill-dependent)

---

## 6. Acceptance gates

### 6.1 Self-lint (must 100 % pass before submit)
```bash
git clone https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline && bash SOP.sh
oyster-buyer-lint <your_clip>.tar.gz
```

Submit only tarballs that print `PASS`.

### 6.2 Manual sampling (5 % per batch)
We sample 5 % (min 5) per batch and inspect:
- Frame realness (no placeholder / no testsrc)
- Trajectory diversity (no repeated patrol)
- Input realness (operator actually playing, not scripted)
- Audio normality (not on loop, not muted)

**Sampling pass rate < 80 % → entire batch rejected, no charge**

### 6.3 8-item action_camera check (per frame)
1. No missing fields, types correct
2. Coordinate alignment with our left-hand frame
3. mouse_dx/dy direction matches camera motion
4. No frame skip / dup
5. keyCode timing matches visible action
6. Quaternion order `[x, y, z, w]`
7. Speeds physically plausible (no superluminal / no -inf)
8. `fx == fy` in `camera_intrinsics`

### 6.4 Video gate (per clip)
- 5 ≤ duration ≤ 6 min
- 30 fps stable
- 1920×1080
- No UI / no logo / no dialog modal
- ≤ 2 visible NPCs
- Smooth scene flow (no portal cut)
- No 1↔3 person swap
- No death / respawn

---

## 7. Submission

### 7.1 Naming
```
<vendor_id>_<batch_id>_<clip_id>_v<spec_version>.tar.gz

Example: vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz
```

### 7.2 Upload methods
- **Default**: AWS S3 (we provide presigned URL after kickoff)
- **Alternative**: SFTP `vendor@upload.oysterworld.dev:/uploads/`
- **Alternative 2**: Aliyun OSS (for China-based vendors)

### 7.3 Frequency
- **Suggested**: daily (push as you finish)
- **Required**: weekly batch submission
- **Batch size**: 100–500 clips/batch

### 7.4 Per-batch manifest
Each batch must include `manifest.yaml` (see [`SUBMISSION_FORMAT.md`](SUBMISSION_FORMAT.md#41-manifest-format)):

```yaml
batch_id: vendor-001_batch-2026-05-A
vendor_id: vendor-001
spec_version: v1
upload_date: 2026-05-09T15:30:00+08:00
total_clips: 200
total_size_gb: 187.5
operators: [...]
clips: [...]
manifest_sha256: ...
```

---

## 8. Production schedule and pricing

| Phase | Window | Target | Unit price |
|---|---|---|---|
| **Sample acceptance** | 7 days | 5–10 clips | Free (paid via advance) |
| **Small batch** | Weeks 2–4 | 100 clips | Negotiated |
| **Production** | From week 5 | 1000–3000/month | Negotiated (volume discount) |
| **Special batch** | Ad hoc | Specific scenes / paths | Negotiated (premium) |

**Pricing direction** (final negotiated post-quote):
- Standard clip (random scene + normal path): $X/clip
- Special clip (specific scene + special/loop path): $1.5X/clip
- Rush clip (48-hour delivery): $2X/clip

---

## 9. Legal and IP

### 9.1 Data ownership
- Recordings are property of **Oysterworld INC**
- Vendor may not distribute / resell / use for other training
- Vendor must delete local copies **within 30 days of submission acceptance**

### 9.2 Game IP compliance
- **Minecraft**: we provide Mojang EULA compliance evidence; follow our SOP
- **Other games**: vendor verifies EULA permits third-party recording (most singleplayer / self-recording is legal)
- **Forbidden**: cracked clients / unauthorised servers / private servers / paid content gates

### 9.3 Privacy
- Recordings must not contain other players' usernames (own only)
- Recordings must not contain real names / emails / IPs / PII
- Operator names recorded as `vendor-NNN-op-X` only

### 9.4 Confidentiality
- This PRD and reference repo are **publicly published** (GitHub repo public + release v0.1.0-rc2). Vendors may freely share these links.
- **Commercial terms remain confidential**: specific unit prices, customer rosters, monthly capacity caps, and privately negotiated discounts must not be disclosed externally.
- **Personally Identifiable Information** (operator real names, emails, phone numbers, IP addresses) must never appear in deliverables.
- External discussions of this project use the codename **"GameData"** (to avoid confusion with vendor's own clients).

---

## 10. Quote request

Reply to **howard.linra@gmail.com** with:

1. **Monthly capacity** estimate (clips/month)
2. **Expected unit price** (USD/clip)
3. **Start date**
4. **Team size** (operators + machines)
5. **Tech stack** capability (full Minecraft+OBS+DepthAnything stack? Or alternative?)

We respond ≤ 48 h with SOW + 30 % advance.

---

## 11. Onboarding (8-step)

See [`VENDOR_ONBOARDING.md`](VENDOR_ONBOARDING.md). Quick:

```bash
git clone --recurse-submodules https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline
bash bin/doctor.sh    # verify dependencies
bash SOP.sh           # one-shot install
bash bin/e2e_smoke.sh # placeholder smoke (verify environment)
# Install OBS + PyTorch (VENDOR_ONBOARDING.md STEP 5)
bash bin/produce_real_sample_v2.sh  # real sample
oyster-buyer-lint <tarball>          # self-lint
bash bin/upload_s3.sh ...            # submit
```

---

## 12. FAQ

**Q: Must use Minecraft?**
A: No. Any tech stack producing real 1080p 30fps gameplay + real input + real depth is fine.

**Q: Must use OBS?**
A: No. NVIDIA ShadowPlay / AMD ReLive / FFmpeg / SwitchBoard all OK.

**Q: Can run depth inference without GPU?**
A: DepthAnything V2 Small runs on Apple M2 / RTX 3060. CPU works (5–10× slower). Game-engine G-buffer depth (Unity / Unreal) also accepted.

**Q: Operators non-technical?**
A: SOP.sh is one-line, auto-detects environment, installs deps, runs e2e. Operator only needs:
- Game-play skill (spectator flying)
- Open terminal, paste one command
- Wait for completion + upload

**Q: Recording crashed mid-clip?**
A: Discard, re-record. We do not accept any spliced / edited clips.

**Q: How is unit price determined?**
A: Send your team capacity → we negotiate.

**Q: Lint FAIL but operator can't see issue?**
A: Slack/WeChat group, engineer ≤ 4-hour response.

**Q: Upload too slow?**
A: We provide S3 multipart + resume scripts (handles 200 Kbps).

---

## 13. Contact

**Howard Li** · CEO, Oysterworld INC
- 📧 howard.linra@gmail.com
- 📱 +1 (341) 250-6526 (WhatsApp / iMessage)
- 💬 LinkedIn: <https://www.linkedin.com/in/connecthoward/>
- 🐙 GitHub: <https://github.com/howardleegeek>

**Response SLA**:
- Email: ≤ 24 h
- Production stop: ≤ 4 h
- Quote + SOW: ≤ 48 h

---

## Appendix A · Spec changelog
| Version | Date | Notes |
|---|---|---|
| v1.0 | 2026-05-02 | Initial release |

## Appendix B · Doc index
- [`BUYER_SPEC_V1.md`](BUYER_SPEC_V1.md)
- [`VENDOR_ONBOARDING.md`](VENDOR_ONBOARDING.md)
- [`SUBMISSION_FORMAT.md`](SUBMISSION_FORMAT.md)
- [`PRD.md`](PRD.md) (Chinese version)

## Appendix C · Lint output sample
```
$ oyster-buyer-lint vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz
[1/8] Tarball structure ........... PASS
[2/8] video.mp4 1920x1080 30fps ... PASS
[3/8] video.mp4 5-6 min duration .. PASS (5m 32s)
[4/8] action_camera.json schema ... PASS (9000 frames, 20 fields)
[5/8] action_camera continuity .... PASS (no gaps, no dups)
[6/8] gameinfo.xlsx fields ........ PASS
[7/8] depth/*.exr count + format .. PASS (1980 EXRs, all float32-Z)
[8/8] Cross-file timestamp align .. PASS

✅ ACCEPTED · ready to submit
```
