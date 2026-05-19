# RED_TEAM_TAXONOMY — 4-Bucket Adversarial Attack Catalogue

> **Project:** oyster-agent-runner (game-data producer + BFT verifier mesh)
> **Author:** Vera Sterling (Algorithm Agent), commissioned by Howard Li
> **Date:** 2026-05-06
> **Stream:** R of red-blue parallel push
> **Status:** Spec / threat model — design only, no Python
> **Predecessors:** `ARCH_BFT_CONSENSUS.md` (V₁/V₂/V₂'/V₃ + PBFT), `SPEC_R13_MULTIMODAL.md` (R13–R16), `bin/bft_adversarial_harness.py` (FI-01..FI-05).

---

## 0. TL;DR — Why This Document Exists

The harness covers 5 single-field mutations and detects 4/5. R13 closes the FI-02 blind spot. **That is necessary but not sufficient.** A real adversary will not stop at single-field tampering: they coordinate fields, sit just below thresholds, and exploit the temporal axis. This document enumerates 20 attacks in 4 buckets and predicts which residuals catch each, exposing the priority gaps for the next defense sprint.

| Bucket | Attacks | Sophistication | Currently Caught (estimate) |
|---|---|---|---|
| **A** Single-field mutation | 5 | Low (FI-01..FI-05) | 5/5 once R13 lands |
| **B** Multi-field coordinated | 5 | Medium-High | 2/5 |
| **C** Sub-threshold perturbation | 5 | Medium (border cases) | 1/5 |
| **D** Replay / temporal | 5 | High | 1/5 |
| **TOTAL** | 20 | — | **9/20 = 45%** |

**Net: 11 attacks need new residuals or hardening.** Top-3 priorities in § 6.

---

## 1. Threat Model & Verifier Roster

**Verifiers (N=4 BFT mesh):**

| ID | Implementation | Role | Trust mode |
|---|---|---|---|
| V₁ | Claude residuals | Reference | LLM (re-derives from spec) |
| V₂ | MiniMax residuals | Disjoint LLM | LLM (re-derives from PRD, not from V₁ code) |
| V₂' | GLM residuals | Diversity twin | LLM (independent VK table) |
| V₃ | Physics-Oracle table | Tamper-evident | Hard-coded `< 200 LOC`, no LLM |

**Consensus rule:** ≥ 2 verifiers FAIL on a residual ⇒ frame REJECT. ABSTAIN does not count toward FAIL bucket. V₃ ABSTAIN on residuals it cannot tabulate (R13/R14/R15/R16 default-ABSTAIN).

**Adversary capability:** producer-side (recorder code/config) tamper. Adversary cannot modify V₁/V₂/V₂'/V₃ binaries, cannot inject into the buyer pipeline, but knows the residual specs (white-box).

**In scope:** mutations to `action_camera.json`, `inputs.jsonl`, `video.mp4`, `depth/*.exr`. **Out of scope:** R-residual smoothness/diversity attacks (V₄ matter per `SPEC_R13_MULTIMODAL.md` § 8).

---

## 2. Bucket A — Single-Field Mutation (FI-01..FI-05)

Already implemented in `bin/bft_adversarial_harness.py`. Catalogued here for completeness.

### A-01 / FI-01 — Quaternion order swap
- **Mutation:** `[x,y,z,w] → [w,x,y,z]` on `camera_rotation_quaternion`. Identity `[0,0,0,1]` becomes `[1,0,0,0]` (180° pitch).
- **Severity:** **High** — silently corrupts every rotation downstream; world-model trains on garbage.
- **V₁/V₂/V₂'/V₃ detection:** all 4 detect via R02 (V₃ table-lookup is the strongest; V₂ has a known sign-bug but still detects this magnitude).
- **Residual:** R02 (oula↔quat consistency).
- **Blind spot?** No. Caught by ≥ 3.
- **Mitigation:** none needed.

### A-02 / FI-02 — keyCode W (87) → B (88)
- **Mutation:** Swap recorded keyCode to a *different valid* VK code; screen video shows W, frame metadata says B.
- **Severity:** **Critical** — semantic action label is wrong; agent learns wrong action↔state mapping.
- **V₁/V₂/V₂'/V₃ detection:** R09 PASSes on all four (88 is in VK table). Pre-R13: 0/4. Post-R13: V₁=FAIL, V₂=FAIL, V₂'=FAIL, V₃=ABSTAIN. Quorum ≥ 2 ⇒ REJECT.
- **Residual:** R13 (keyCode↔inputs.jsonl replay).
- **Blind spot?** **Was the original blind spot.** R13 closes it.
- **Mitigation:** R13 already spec'd. Land it.

### A-03 / FI-03 — fps 30.0 → 25.0
- **Mutation:** Declare fps=25 in frame metadata; encoder still emits 30 fps video.
- **Severity:** **High** — temporal axis miscalibrated; physics velocity formulas all wrong.
- **V₁/V₂/V₂'/V₃ detection:** R12 catches if buyer locks fps∈{29,30,31}; otherwise R15 (fps↔ffprobe) catches the disagreement with video.mp4. V₃ R12 lookup table: PASS iff exactly 30. Quorum 4/4.
- **Residual:** R12 (single-modal range) + R15 (cross-modal honesty).
- **Blind spot?** No.
- **Mitigation:** none needed (R15 lands as part of multimodal sprint).

### A-04 / FI-04 — mouse_x list[0.5] → scalar 0.5
- **Mutation:** Drop the list wrapper. Schema-violation, not value-violation.
- **Severity:** **Medium** — schema corruption; downstream pipeline crashes or silently iterates a string.
- **V₁/V₂/V₂'/V₃ detection:** V₂ raises TypeError on `[0]` index (counted as detection). V₁ has type guards. V₃ table-lookup ABSTAINs on schema mismatch. R07 (mouse range).
- **Residual:** R07 + Pydantic schema validation upstream.
- **Blind spot?** No.
- **Mitigation:** treat schema violations as REJECT, not ABSTAIN, at the orchestrator (currently exception → detect=True).

### A-05 / FI-05 — fx 1080 → 900 (fy still 1080)
- **Mutation:** Asymmetric focal length. PRD requires fx == fy.
- **Severity:** **High** — projection matrix wrong; reconstructions skewed.
- **V₁/V₂/V₂'/V₃ detection:** R08 (fx == fy) catches all 4.
- **Residual:** R08.
- **Blind spot?** No.
- **Mitigation:** none needed.

**Bucket A subtotal:** 5/5 caught (4/5 today, 5/5 once R13 lands).

---

## 3. Bucket B — Multi-Field Coordinated Mutation

Adversary mutates ≥ 2 correlated fields *self-consistently* so single-field residuals individually PASS. The hard cases.

### B-01 — Self-consistent oula+quat Hamilton pair
- **Mutation:** Mutate both `camera_rotation_oula` AND `camera_rotation_quaternion` to a *different but mutually-consistent* rotation. Hamilton oula→quat formula holds, so R02 PASSes.
- **Severity:** **High** — the rotation is internally consistent but does not match the actual on-screen camera. World-model misaligned.
- **V₁/V₂/V₂'/V₃ detection:** R02 PASSes (designed-in consistency). R03 (positional velocity) only fires if the velocity field is also tampered or untouched. R13 only fires if input events disagree (they likely do, since mouse_dx drove a different yaw).
- **Residual that should catch it:** **R14** (mouse_dx ↔ Δyaw correlation) — if the new yaw doesn't correlate with mouse_dx, R14 FAILs. V₃ ABSTAINs.
- **Blind spot?** **Partial.** Without R14, both oula and quat lie consistently and nothing catches it. R14 catches the *mouse-decoupled* variant; an attacker who *also* fakes mouse_dx coherently is in B-02 territory.
- **Mitigation:** R14 must land alongside R13. Currently spec only — implementation pending.

### B-02 — Coordinated pos+speed satisfying Δpos·fps = speed'
- **Mutation:** Mutate `camera_position` AND `camera_speed` jointly so the discrete derivative `Δpos·fps == speed_new`. R03 (positional kinematic consistency) passes.
- **Severity:** **High** — position trajectory wrong but kinematically self-consistent; agent learns wrong spatial mapping.
- **V₁/V₂/V₂'/V₃ detection:** R03 PASSes. R10 (|speed| < V_max=50 m/s) catches IF attacker overshoots. Subtle attacker stays under V_max.
- **Residual that should catch it:** No current residual. Needs **R17** (cross-modal: position↔video optical-flow magnitude). Or: physics-oracle V₃ row keyed on per-frame depth-encoded scene anchors (out of current scope).
- **Blind spot?** **YES.** Subtle B-02 evades all current residuals.
- **Mitigation:** Add **R17 (optical-flow magnitude consistency)**: median magnitude of dense flow on `video.mp4` between frames `i` and `i+1` should correlate with `|camera_speed[i]| · dt`. ABSTAIN if video missing. Priority: P2.

### B-03 — Coordinated keyCode + inputs.jsonl (W→B in BOTH)
- **Mutation:** Replace W with B in `action_camera.json[keyCode]` AND in `inputs.jsonl` events. R13 PASSes (modalities agree).
- **Severity:** **Critical** — both producer-side artifacts coordinated. R13 specifically defends single-side mutation; can't defend mutual lying.
- **V₁/V₂/V₂'/V₃ detection:** R09 PASSes. R13 PASSes. V₂' GLM uses the same VK table as V₁/V₂; semantic interpretation identical — no diversity benefit.
- **Residual that should catch it:** **None at V₁/V₂/V₂'/V₃ layer.** This is the documented "out of scope" 20% per `BFT_TRUST_REPORT_FOR_BUYER.md` — buyer-signed V₄ reference (compare against held-out exemplar) is the only defense.
- **Blind spot?** **YES, by design.** Acknowledged limitation of the BFT mesh.
- **Mitigation:** Two-front response: (a) **video pixel-level OCR/UI-overlay check** — if game shows on-screen "W pressed" indicator, OCR catches the disagreement, but most games don't render input HUD; (b) **V₄ buyer reference** as designed. Priority for (a): P3 (game-specific, brittle). Priority for (b): already in roadmap.

### B-04 — Coordinated time+fps proportional dilation
- **Mutation:** Multiply both `time` field and `fps` by 0.9. dt = 1/fps still 33.3 ms; R12 PASSes (since R12 just checks fps in [29, 31] — if attacker keeps fps at 30 and dilates `time` uniformly, R12 PASSes; R05 timestamp Δ also PASSes).
- **Severity:** **Medium** — temporal axis lies; depth-frame timing mismatched; downstream models trained on warped time.
- **V₁/V₂/V₂'/V₃ detection:** R05 (timestamp delta) PASSes. R12 PASSes. **R15 (fps↔ffprobe video.fps)** catches: declared fps=30 but video encoded at 27 fps ⇒ |30−27| > 0.5 ⇒ FAIL.
- **Residual that should catch it:** **R15.**
- **Blind spot?** No, *if R15 lands*. Currently spec-only.
- **Mitigation:** Land R15. Already prioritized in `SPEC_R13_MULTIMODAL.md` § 9.3 (R15 is the easiest of the four).

### B-05 — Full dataset replay (Frankenstein clip)
- **Mutation:** Mix-and-match: `action_camera.json` from clip A + `video.mp4` from clip B + `depth/*.exr` from clip C. Each modality is internally well-formed.
- **Severity:** **Critical** — every single-modal residual PASSes; cross-modal residuals catch some but not all.
- **V₁/V₂/V₂'/V₃ detection:** R13 catches *if* keyCode/inputs disagree (likely between A and clip-A's inputs.jsonl — if attacker pulls inputs.jsonl from A, this collapses). R15 catches *if* fps differs across clips. R16 catches if depth count vs video duration mismatches. R14 catches if mouse vs yaw decorrelates across clip boundary.
- **Residual that should catch it:** **R13 + R15 + R16 + R14 all needed.** Even with all four, a careful attacker matching fps/duration/key-events could pass.
- **Blind spot?** **Partial.** Naive Frankenstein is caught by ≥ 1 of R13–R16. Sophisticated Frankenstein where attacker matches clip durations and fps requires **manifest-level hash check**.
- **Mitigation:** Add **R18 (manifest cross-artifact hash binding)**: producer emits `manifest.json.session_id`; every artifact (`action_camera.json`, `inputs.jsonl`, `video.mp4`, `depth/`) embeds `session_id`. Verifier checks all session_ids equal. Single point of cryptographic continuity. Priority: P1 (cheap to implement, high leverage).

**Bucket B subtotal:** B-04 (R15), B-05 partial (R13–R16). With R18: 4/5. Without R18: 2/5 caught.

---

## 4. Bucket C — Sub-Threshold Perturbation

Adversary mutates a single field by an amount *just below* the residual's detection threshold. Per-frame undetectable; statistical detector required.

### C-01 — Quaternion norm ‖q‖ = 1 + ε, ε just below 1e-6
- **Mutation:** Each frame `q ← q · (1 + 5e-7)`. R01 (‖q‖ within 1e-6) PASSes per-frame.
- **Severity:** **Medium** — magnitude drift integrates; downstream rotation matrices acquire scale bias.
- **V₁/V₂/V₂'/V₃ detection:** R01 PASSes per-frame. No statistical detector.
- **Residual that should catch it:** **R19 (cumulative-drift detector)** — track running mean of `‖q‖ − 1` over a 1000-frame window; FAIL if mean > 1e-7 (1/10 of threshold). Same shape as R14's sliding-window aux_state.
- **Blind spot?** **YES.**
- **Mitigation:** Add **R19**. Priority: P3 (low frequency real-world; clean spec).

### C-02 — Mouse_dx accumulating drift below R04 threshold
- **Mutation:** Each frame `mouse_dx ← (x[n]−x[n−1]) + 0.1px`. R04 (per-frame consistency) PASSes (assuming threshold > 0.1).
- **Severity:** **Medium** — over 9000 frames, accumulated drift = 900 px = full screen width; cursor model drifts off-screen.
- **V₁/V₂/V₂'/V₃ detection:** R04 PASSes per-frame. R07 (mouse_x ∈ [0,1]) catches *only when* the drifting cursor leaves screen bounds (eventually).
- **Residual that should catch it:** **R20 (cumulative integration check)** — `Σ mouse_dx ≈ x[N] − x[0]` to within tolerance. Out-of-bounds detection from R07 is too late.
- **Blind spot?** **YES.**
- **Mitigation:** Add **R20** (computationally trivial; one integration per dataset). Priority: P2.

### C-03 — fps = 29.51 (exactly at R12 boundary)
- **Mutation:** fps = 29.51, just above R12 lower bound 29.5 (assuming R12 is `[29.5, 30.5]`).
- **Severity:** **Low-Medium** — borderline; PASS or FAIL depends on whether R12 uses `>=` (inclusive) or `>` (strict). V₁ uses `>=`, V₂ might use `>` ⇒ disagreement.
- **V₁/V₂/V₂'/V₃ detection:** Mixed verdict expected. **The disagreement itself is the bug** — diversity-by-implementation breaks here.
- **Residual that should catch it:** R12 with locked threshold semantics across V₁/V₂/V₂'.
- **Blind spot?** **Specification ambiguity.** Not a residual gap — a *spec clarity* gap.
- **Mitigation:** Document threshold semantics in `PRD_FORMULAS.md` ("inclusive on both sides") and add a sentinel test that exercises 29.5 / 29.5 + ε / 29.5 − ε and asserts all four verifiers vote identically. Priority: P1 (cheap, high integrity payoff).

### C-04 — Pitch wraps 180.001° (just over R06 [-180, 180])
- **Mutation:** `pitch = 180.001`. R06 boundary exclusive.
- **Severity:** **Low** — rounding error, not adversarial; but if attacker exploits, accumulates.
- **V₁/V₂/V₂'/V₃ detection:** R06 should FAIL on V₁/V₂; V₃ table lookup might bucket 180 vs 180.001 differently. Mixed.
- **Residual that should catch it:** R06.
- **Blind spot?** **Boundary semantics same as C-03.**
- **Mitigation:** Same as C-03 — document inclusive/exclusive convention. Add sentinel at exactly 180.0, 180.001, −180.001. Priority: P1 (rolls up with C-03).

### C-05 — Speed = 49.99 m/s (just under R10 50 m/s)
- **Mutation:** `|camera_speed| = 49.99` m/s. PRD says max realistic ≤ ~10 m/s for typical FPS character; 50 m/s is "physically absurd but allowed by R10".
- **Severity:** **High** — semantic absurdity that R10's threshold is too generous to catch.
- **V₁/V₂/V₂'/V₃ detection:** R10 PASSes. R03 PASSes if pos changes consistently.
- **Residual that should catch it:** **None at threshold-50.** Tightening R10 to game-specific V_max (Minecraft sprint ≈ 5.6 m/s; per-game lookup) would catch.
- **Blind spot?** **YES.** Documented in `BUYER_SPEC_V1.md` as "global threshold; per-game tightening pending".
- **Mitigation:** Per-game V_max in `buyer_spec.yaml`; R10 reads `config.max_speed` instead of hard-coded 50. Priority: P2.

**Bucket C subtotal:** 0/5 caught with current residuals; 1/5 (C-03/C-04 spec fix is identity-pair) once boundary docs are normalized; 4/5 with R19+R20+per-game-V_max.

---

## 5. Bucket D — Replay & Temporal Attacks

Adversary manipulates the time axis or splices frames out-of-order. R-residuals are mostly per-frame and ill-equipped for sequence-level checks.

### D-01 — Timestamp jump 33ms → 66ms (skip 1 frame)
- **Mutation:** `time[i+1] − time[i] = 66ms` (not 33ms) but `frame_idx[i+1] = frame_idx[i] + 1`.
- **Severity:** **High** — implies dropped frame in metadata but not in indexing.
- **V₁/V₂/V₂'/V₃ detection:** **R05 (timestamp delta) catches.** Threshold ≈ 1.5/fps ⇒ 50ms; 66ms exceeds.
- **Residual that should catch it:** R05.
- **Blind spot?** No, *assuming R05 threshold tight enough*. Audit: R05 currently uses tolerance proportional to `1/fps`; verify `tolerance < 33ms` or this slips through.
- **Mitigation:** Lock R05 threshold to `0.05 · 1/fps` (1.5 ms wiggle at 30 fps). Sentinel test required.

### D-02 — Frame index reordering (frame 5 before frame 4)
- **Mutation:** Re-shuffle frames such that `action_camera.json[i].frame_idx` is non-monotonic. Each frame's *contents* are valid.
- **Severity:** **Critical** — temporal causality broken; agent learns nonsensical state transitions.
- **V₁/V₂/V₂'/V₃ detection:** **No current residual checks monotonicity.** Each frame in isolation passes all R01–R12. R13 might catch (input events bound to wall-clock time, won't realign), but not guaranteed.
- **Residual that should catch it:** **R21 (monotonic frame index)** — `frame_idx[i+1] > frame_idx[i]` for all i. One-line check, dataset-level.
- **Blind spot?** **YES.** Egregious gap.
- **Mitigation:** Add **R21** (monotonic frame index). Trivially cheap. Priority: P0 (immediate).

### D-03 — inputs.jsonl truncated mid-session
- **Mutation:** `inputs.jsonl` cut off after frame 1000; `action_camera.json` continues to frame 9000. R13 ABSTAINs after frame 1000 (no events for tail).
- **Severity:** **High** — 8000 frames of action_camera have no input cross-check; effectively undefended.
- **V₁/V₂/V₂'/V₃ detection:** R13 ABSTAINs after frame 1000. Per `SPEC_R13_MULTIMODAL.md` § 7, ABSTAIN ratio > 0.50 ⇒ dataset verdict ABSTAIN. But ABSTAIN ≠ FAIL — the dataset is *not rejected*, just flagged.
- **Residual that should catch it:** R13's ABSTAIN behavior needs reinterpretation. **R13 should FAIL (not ABSTAIN) when inputs.jsonl is truncated relative to action_camera.json's frame count.** Truncation is silent failure on producer side; ABSTAIN is too lenient.
- **Blind spot?** **YES — semantic.**
- **Mitigation:** R13 spec amendment: distinguish `inputs.jsonl missing entirely` (ABSTAIN) from `inputs.jsonl present but truncated relative to action_camera.json` (**FAIL**). Add `R13.config.require_full_coverage = true` (default). Priority: P1.

### D-04 — Depth files renamed/shuffled
- **Mutation:** Rename `depth_001.exr ↔ depth_500.exr`. Count correct, content scrambled.
- **Severity:** **High** — depth misaligned with video frames; world-model trains on wrong geometry.
- **V₁/V₂/V₂'/V₃ detection:** R16 only counts files (and optionally checks filename-encoded timestamp uniformity per `SPEC_R13_MULTIMODAL.md` § 6.4). **Content not checked.**
- **Residual that should catch it:** **R22 (depth-content↔video-frame alignment)** — sample N random depth files, decode EXR, check that depth statistics (mean, std, edge density) align with the corresponding video frame's grayscale gradient. Heuristic but catches gross shuffles.
- **Blind spot?** **YES.**
- **Mitigation:** R22 (heuristic; computationally heavy but per-dataset). Or simpler: producer emits `depth_manifest.json` with per-file SHA-256 plus `frame_idx` binding; R16 checks the manifest. **The manifest approach is much cleaner** — same shape as R18. Priority: P2.

### D-05 — video.mp4 transcoded with different codec
- **Mutation:** Re-encode H.265 → H.264 (PRD locks to H.265). Same fps/resolution, different codec.
- **Severity:** **Medium** — quality loss / different artifacts; downstream perceptual models inconsistent.
- **V₁/V₂/V₂'/V₃ detection:** R15 (fps) PASSes. **No codec lock residual.** ffprobe outputs codec; nothing checks it.
- **Residual that should catch it:** **R23 (codec lock)** — extension to R15: `probe.codec_name == config.expected_codec` (default `"hevc"` per PRD).
- **Blind spot?** **YES.**
- **Mitigation:** Trivial extension to R15. Add `expected_codec` to `R15.config`; assert in V₁/V₂/V₂'. Priority: P2.

**Bucket D subtotal:** 1/5 caught (D-01 if R05 threshold is tight). 4/5 currently blind spots.

---

## 6. Summary Table & Priority Triage

### 6.1 Per-attack scoreboard

| ID | Bucket | Severity | Residual | Caught now? | Caught after planned? | Notes |
|---|---|---|---|---|---|---|
| A-01 | A | High | R02 | YES (4/4) | YES | Identity rotation flip caught universally. |
| A-02 | A | Critical | R09→R13 | NO (0/4) | YES | R13 closes the gap; original blind spot. |
| A-03 | A | High | R12+R15 | YES (3/4) | YES (4/4) | R15 adds cross-modal honesty. |
| A-04 | A | Medium | R07 | YES (3/4) | YES | Schema violation. |
| A-05 | A | High | R08 | YES (4/4) | YES | Symmetric focal length. |
| B-01 | B | High | R02→R14 | NO | YES (with R14) | Self-consistent oula+quat needs R14. |
| B-02 | B | High | R03→R17 | NO | NO (R17 not spec'd) | Sub-V_max coordinated pos+speed. **Gap.** |
| B-03 | B | Critical | V₄ only | NO | NO (V₄ scope) | Dual-side mutation; documented limit. |
| B-04 | B | Medium | R15 | NO | YES (with R15) | time+fps dilation. |
| B-05 | B | Critical | R13–R16+R18 | Partial | YES (with R18) | Frankenstein needs session_id binding. |
| C-01 | C | Medium | R01→R19 | NO | NO (R19 not spec'd) | Cumulative norm drift. |
| C-02 | C | Medium | R04→R20 | NO | NO (R20 not spec'd) | Cumulative mouse drift. |
| C-03 | C | Low-Med | R12 spec | NO (ambiguous) | YES (with spec lock) | Boundary semantics. |
| C-04 | C | Low | R06 spec | NO (ambiguous) | YES (with spec lock) | Same as C-03. |
| C-05 | C | High | R10 per-game | NO | NO (per-game V_max not in spec) | 49.99 m/s absurd, slips. |
| D-01 | D | High | R05 | YES (if tight) | YES | Audit R05 tolerance. |
| D-02 | D | Critical | R21 | NO | NO (R21 not spec'd) | Frame reordering; **egregious gap.** |
| D-03 | D | High | R13 amendment | NO (ABSTAIN) | YES (with FAIL semantics) | Truncated inputs. |
| D-04 | D | High | R22 / depth manifest | NO | NO | Depth shuffle; manifest path easiest. |
| D-05 | D | Medium | R23 (R15 ext) | NO | NO | Codec lock. |

### 6.2 Aggregate counts

```
Total attacks:              20
Caught today:                5 / 20  =  25%   (A-01,A-03 partial,A-04,A-05,D-01)
Caught after R13–R16 land:  10 / 20  =  50%   (+A-02,B-01,B-04,B-05 partial,A-03 full)
Caught after Top-3 land:    14 / 20  =  70%
Remaining gaps:              6 / 20  =  30%   (B-02,B-03,C-01,C-02,C-05,D-04,D-05)
```

(C-03/C-04 collapse to one item once boundary semantics are documented.)

### 6.3 Priority new defenses (Top 3 to implement)

| Rank | Defense | Closes | Effort | Severity |
|---|---|---|---|---|
| **1** | **R21 — monotonic frame index** | D-02 | 1 LOC + sentinel | Critical |
| **2** | **R18 — session_id manifest binding** | B-05 (Frankenstein), D-04 (depth shuffle when extended) | ~50 LOC producer + ~20 LOC verifier | Critical |
| **3** | **R13 truncation semantics + boundary-spec lock** | D-03, C-03, C-04 | spec amendment + sentinel tests | High |

**Rationale for ranking:**

1. **R21 is one line of code** and closes an egregious gap (D-02 frame reorder ⇒ Critical). No reason this isn't already in. **Land before next sprint.**
2. **R18 (session_id binding)** is a cryptographic single-source-of-truth that closes one Critical (B-05) and lays groundwork for D-04 depth manifest binding. Producer changes are minimal; the cost is one-time.
3. **R13 truncation + boundary-spec lock** is the lowest-LOC win for the most semantic ambiguity. D-03 (truncated inputs) is a real producer failure mode that ABSTAIN currently masks. C-03/C-04 boundary disagreement breaks BFT diversity guarantees.

### 6.4 Defenses deliberately deprioritized

| Defense | Why deferred |
|---|---|
| R17 (optical-flow ↔ speed) | Computationally heavy; B-02 sophistication threshold low in practice. Land after R21/R18. |
| R19/R20 (cumulative drift) | Real-world incidence low; clean spec but moderate effort. Sprint+1. |
| R22 (depth↔video content) | Subsumed by R18-pattern depth manifest. Skip pixel-level check; bind by hash. |
| R23 (codec lock) | One-line addition to R15 once R15 lands. Roll up. |
| Per-game V_max | Buyer-spec config knob; not residual code change. Coordinate with `BUYER_SPEC_V1.md`. |

---

## 7. ISC — Ideal State Criteria for Red-Team Coverage

Each criterion binary (YES/NO ≤ 1 second).

### 7.1 Coverage criteria

- **[C-RT-01]** All 5 Bucket A attacks (FI-01..FI-05) are in `bin/bft_adversarial_harness.py`. *Evidence:* harness AST grep for FI-01..FI-05 ⇒ 5.
- **[C-RT-02]** All 5 Bucket B attacks have entries in `bin/bft_adversarial_harness.py` post-R13/R14/R15/R16/R18 sprint. *Evidence:* harness FI-B-01..FI-B-05 register.
- **[C-RT-03]** All 5 Bucket C attacks have sentinel tests in `tests/sentinels/`. *Evidence:* file presence + pytest collect.
- **[C-RT-04]** All 5 Bucket D attacks have entries in adversarial harness. *Evidence:* FI-D-01..FI-D-05 register.

### 7.2 Top-3 defense criteria

- **[C-RT-05]** R21 implemented in V₁ AND V₂. *Evidence:* AST grep for `r21_monotonic_frame_idx`.
- **[C-RT-06]** R18 producer emits `manifest.json` with `session_id`; verifier checks cross-artifact equality. *Evidence:* manifest schema + `r18_session_id_binding` callable.
- **[C-RT-07]** R13 spec amendment adopted: truncation ⇒ FAIL. *Evidence:* `SPEC_R13_MULTIMODAL.md` § 3.5 amended; `R13.config.require_full_coverage` defaulted true.

### 7.3 Anti-criteria — must remain false

- **[A-RT-01]** No attack in this taxonomy may PASS BFT consensus end-to-end after Top-3 lands. *Evidence:* harness exit code on each FI-*.
- **[A-RT-02]** No new residual may default to `passed=True` on absent artifacts (IL10 enforcement). *Evidence:* `bin/audit_artifact_honesty.py` exit 0.
- **[A-RT-03]** No verifier (V₁/V₂/V₂') may disagree with another on a sentinel boundary case (C-03/C-04 class). *Evidence:* `tests/sentinels/test_boundary_consistency.py` all PASS.

### 7.4 ISC Tracker

```
┌─ ISC: Ideal State Criteria ───────────────────────┐
│ Phase: PLAN (red-team taxonomy + defense triage)  │
│ Criteria: 0 → 7   (+7)                            │
│ Anti:     0 → 3   (+3)                            │
├───────────────────────────────────────────────────┤
│ + [C-RT-01..04] Bucket coverage in harness        │
│ + [C-RT-05]    R21 monotonic frame index          │
│ + [C-RT-06]    R18 session_id manifest binding    │
│ + [C-RT-07]    R13 truncation FAIL semantics      │
│ + [A-RT-01..03] Defense honesty anti-criteria     │
└───────────────────────────────────────────────────┘
```

---

## 8. Document Provenance

- Authored by Vera Sterling (Algorithm Agent), 2026-05-06, Stream R.
- Source-of-truth: `bin/bft_adversarial_harness.py` (FI-01..FI-05),
  `docs/SPEC_R13_MULTIMODAL.md` (R13–R16), `docs/ARCH_BFT_CONSENSUS.md`
  (V₁/V₂/V₂'/V₃ + PBFT), `docs/PRD_FORMULAS.md` (R01–R12 thresholds),
  `docs/BFT_TRUST_REPORT_FOR_BUYER.md` (V₄ scope boundary).
- Sibling specs NOT modified: any `bin/verify_*.py`,
  `bin/bft_adversarial_harness.py`, parallel-stream blue-side specs.
- Scope: 4-bucket attack taxonomy, blind-spot identification, top-3
  defense triage, ISC. **Out of scope:** Python implementation of
  R17–R23, V₄ buyer-reference workflow, per-game V_max calibration
  values.

*End of RED_TEAM_TAXONOMY.md.*
