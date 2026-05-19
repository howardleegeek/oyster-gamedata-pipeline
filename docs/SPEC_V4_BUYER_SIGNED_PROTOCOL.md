# SPEC_V4_BUYER_SIGNED_PROTOCOL — Buyer-Signed Reference Sample (YELLOW Node)

> **Project:** oyster-agent-runner | **Author:** Vera Sterling (Algorithm Agent), commissioned by Howard Li
> **Date:** 2026-05-06 — Stream L of wave-3 parallel push | **Status:** Spec / ISC — design only, no Python
> **Predecessors:** `ARCH_BFT_CONSENSUS.md` (§ 2.4 V₄ role, § 2.5 YELLOW),
> `SPEC_R13_MULTIMODAL.md` (IL10 ABSTAIN convention, § 8 V₄ scope),
> `RED_TEAM_TAXONOMY.md` (B-01, B-03 designated forever-uncatchable by V₁/V₂/V₃).

---

## 0. TL;DR

Wave-2 → wave-3 lifts detection 27% → 47% → ~80%. **Two CRITICAL gaps
remain forever-uncatchable by code-only verifiers:**

| Gap | Attack | Why V₁/V₂/V₃ cannot catch |
|---|---|---|
| **B-03** | Coordinated keyCode + `inputs.jsonl` W→B swap | Both producer artifacts lie consistently. R13 cross-check passes (both sides agree on the lie). |
| **B-01** | Self-consistent oula+quat Hamilton swap | R02 PASSes by construction. Coordinated `pos` defeats R03. |

Root cause: the producer controls every artifact LLM-authored verifiers
can read. V₄ breaks this with **byte-exact reference frames + video-frame
hashes signed by the buyer.** Trust anchor lives outside producer's reach.

V₄ is **YELLOW** per BFT § 2.5 — human attestation, machine replay.
Lift: closing B-01 + B-03 raises coverage 47% → **~73%**.

---

## 1. Iron Law Extension — IL12

> **IL12 — Buyer-Signed Reference Authority.** A V₄ vote is binding ONLY
> when (a) `buyer_reference.json` exists for the dataset's `session_id`,
> (b) its signature verifies against the configured buyer pubkey (or HMAC),
> AND (c) `signing_timestamp` precedes `manifest.created_at`. Any of
> (a)/(b)/(c) failing ⇒ V₄ MUST emit `decision: ABSTAIN`, reason-tagged
> per § 4.3. Orchestrator treats YELLOW-ABSTAIN per § 7.

IL10 covers artifact-missing ABSTAIN. IL12 adds: artifact present, signed,
AND temporally well-ordered. Enforced by `bin/audit_v4_signing_protocol.py`.

---

## 2. Reference Sample Selection

Buyer (Lark/Vera) curates **5 frames per dataset** — statistical floor (BFT
§ 8.5) and operational ceiling for 24-hour signing SLA.

### 2.1 The five mandated frame archetypes

| Slot | Selection rule | Rationale |
|---|---|---|
| **F1: Pure-key** | First frame with `keyCode == [87]` AND `Δyaw > 1.0°` | Catches B-03 directly: signed W=87, attacker's W→B swap breaks byte-diff. |
| **F2: Multi-key + diagonal** | First frame with `len(keyCode) ≥ 2` AND `\|Δyaw\|>0.5° AND \|Δpitch\|>0.5°` | Stress-tests coordinated mutation across keys+camera. Rare ⇒ attacker unlikely to anticipate. |
| **F3: Boundary-start** | `frame_idx == 0` | Anchors session start. Catches Frankenstein splice (B-05). |
| **F4: Boundary-mid** | `frame_idx == floor(N_frames / 2)` (typically 4500) | Mid-session anchor. Drift detectors miss this; byte-diff doesn't. |
| **F5: Boundary-end** | `frame_idx == N_frames − 1` (typically 8999) | Anchors session end. F3+F5 lock the temporal envelope. |

If no F1/F2 match, buyer falls back to closest analog and records the
relaxation in `buyer_reference.json.frame_selection_log[]`. Documented,
not silently swallowed.

### 2.2 Byte-exact action_camera snapshots

For each F_i, store the canonicalized JSON of the frame's full record:
keys sorted, floats via `json.dumps(..., allow_nan=False)` with
`repr(float)` precision, lists preserved, no trailing whitespace.
Producer's `action_camera.json[F_i]` must serialize **byte-identical** at
verify time. Intentionally strict — any drift means producer
nondeterminism (fix producer, not spec).

### 2.3 Video-frame raw-RGB hashes

Per F_i: decode `video.mp4` at `t = f_i / declared_fps` via
`ffmpeg -ss <t> -frames:v 1 -f rawvideo -pix_fmt rgb24`. SHA-256 the raw
stream. V₄ replays the same pipeline on the producer-shipped video.
Catches: B-03 HUD mismatch (if game renders input state), D-05 codec
swap, wholesale `video.mp4` substitution. Hash on **raw decoded RGB**
(not H.265 container) makes re-encoding fail by accident — the desired
property.

### 2.4 Why 5 frames

| Size | Trade-off |
|---|---|
| 1–2 | Trivially evaded — attacker mutates everywhere else. |
| **5** | Each archetype covers a distinct attack class; 24-hr signing feasible. |
| 10+ | SLA strain → rubber-stamping → insider-attack surface. |

**Coverage is V₁/V₂/V₃'s job (R01–R23).** V₄'s job: be the single
out-of-band reality check breaking LLM-only consensus when wrong.

### 2.5 White-box adversarial concern

White-box attacker knows the deterministic rule and predicts F1..F5.
v2 mitigation: `selection_seed = HMAC(buyer_secret, dataset_id)`,
re-rolling F1/F2 within their equivalence class. F3/F4/F5 stay
boundary-defined. v1 default = deterministic; randomization = open Q2.

---

## 3. Signing Protocol — `buyer_reference.json` Schema

### 3.1 Location

`s3://oyster-bft/<run-id>/buyer_reference.json` — same prefix as
`dataset.tar.gz` and `manifest.json`.

### 3.2 Canonical schema

```json
{
  "schema_version": "buyer-reference/v1",
  "dataset_id": "<UUID matching session_manifest.session_id>",
  "signing_timestamp": "2026-05-06T14:23:11.000Z",
  "signer_identity": "lark@buyer.example.com",
  "signer_pubkey_fingerprint": "ed25519:...",
  "signature_scheme": "ed25519",
  "selection_seed": null,
  "frames": [
    {
      "slot": "F1",
      "selection_rule": "first frame with keyCode==[87] AND |delta_yaw|>1.0",
      "frame_idx": 142,
      "action_camera_record_canonical": "{\"camera_position\":[0.0,1.62,0.0],...}",
      "action_camera_record_sha256": "5f8e...c1",
      "video_frame_rawrgb_sha256": "9b3c...77",
      "video_frame_pix_fmt": "rgb24",
      "video_decode_command": "ffmpeg -ss 4.7333 -i video.mp4 -frames:v 1 -f rawvideo -pix_fmt rgb24 -"
    },
    { "slot": "F2", "...": "..." },
    { "slot": "F3", "frame_idx": 0, "...": "..." },
    { "slot": "F4", "frame_idx": 4500, "...": "..." },
    { "slot": "F5", "frame_idx": 8999, "...": "..." }
  ],
  "frame_selection_log": [],
  "signature": "ed25519:..."
}
```

`signature` covers SHA-256 of canonicalized JSON minus `signature` field.

### 3.3 Signing scheme — ed25519 default, HMAC-SHA256 fallback

| Scheme | Compromise model |
|---|---|
| **ed25519** (default) | Buyer holds private key offline (YubiKey/HSM). Producer compromise cannot forge V₄. |
| **HMAC-SHA256** | Shared secret `BUYER_HMAC_SECRET` env. Faster bring-up; CI access ⇒ forgery possible. |

Default ed25519: removes producer pipeline from trust chain entirely.
HMAC only when buyer cannot manage a key pair (open Q1). Signature
commits to: `dataset_id`, 5 frame indices + canonical records, 5 video
hashes, `signing_timestamp`, signer identity. NOT to producer's
`manifest.json` or other frames.

---

## 4. V₄ Verifier Function Signature

```python
def v4_buyer_reference_diff(
    rec: dict,
    neighbor: dict | None = None,
    buyer_reference_path: str | Path | None = None,
    video_path: str | Path | None = None,
) -> ResidualResult:
    """Verify a frame matches its buyer-signed reference, if signed.

    Per-frame logic:
      1. buyer_reference_path is None -> ABSTAIN (per IL10/IL12).
      2. Load + sig-verify buyer_reference.json (cached per dataset).
         Failure -> ABSTAIN.
      3. rec['frame_idx'] not in {F1..F5} -> SKIP (passed=True,
         detail='not_a_reference_frame'; 8995-of-9000 majority case).
      4. rec['frame_idx'] in F1..F5:
         a. Canonicalize rec into the JSON form buyer signed.
         b. Byte-compare against reference[slot].action_camera_record_canonical.
         c. If video_path provided, decode at f_i/declared_fps, hash
            raw-RGB, compare against video_frame_rawrgb_sha256.
         d. PASS iff byte-diff AND video hash both match.
    """
```

### 4.1 Inputs

`neighbor` ignored (ABI uniformity). `buyer_reference_path=None` ⇒
ABSTAIN per IL12. `video_path=None` ⇒ byte-diff runs; hash skipped with
`detail='video_hash_skipped'`.

### 4.2 Verdict

`id="V4_buyer_reference_diff"`; `value`: 0 (PASS), 1 (FAIL byte), 2 (FAIL
hash), 3 (FAIL both), −1 (ABSTAIN); `threshold=0`; `passed = value==0 OR
detail=='not_a_reference_frame'`; `unit="discrete"`; on FAIL detail:
`"slot=F1 frame_idx=142 byte_diff_at_offset=312 video_hash_mismatch=true"`.

### 4.3 ABSTAIN encoding

| Reason | When |
|---|---|
| `ABSTAIN:reference_missing` | path None or file absent |
| `ABSTAIN:reference_unparseable` | JSON parse error |
| `ABSTAIN:reference_schema_mismatch` | required field missing |
| `ABSTAIN:signature_invalid` | signature does not verify |
| `ABSTAIN:dataset_id_mismatch` | `reference.dataset_id != manifest.session_id` |
| `ABSTAIN:signing_timestamp_after_creation` | per IL12, buyer signed AFTER producer |
| `ABSTAIN:pubkey_unknown` | signer fingerprint not in `BUYER_PUBKEYS.md` |

V₄ MUST NOT return `passed=True` in any ABSTAIN case.

### 4.4 Per-dataset caching

Reference loading + sig verification are O(1) per dataset. Entrypoint
loads once, passes parsed structure per-frame. Non-F1..F5 frames
short-circuit at step 3 ⇒ V₄ is effectively free for 8995/9000 frames.

---

## 5. What V₄ Catches That V₁/V₂/V₃ Cannot

### 5.1 B-03 — Coordinated keyCode + inputs.jsonl swap

`action_camera.json[142].keyCode = [88]` (lying); `inputs.jsonl` events
`key_down code=88` (lying coherently). R09 PASS (88 is valid VK), R13
PASS (modalities agree on the lie). **V₄ byte-diff FAILs** — signed
reference has `keyCode=[87]`, byte-diff at offset 312. V₄ video-hash
FAILs/PASSes depending on HUD; byte-diff alone catches.

Net: V₁ ACCEPT + V₂ ACCEPT + V₃ ABSTAIN + V₄ REJECT ⇒ < 3 ACCEPT ⇒
**REJECT** per BFT § 3.3.

### 5.2 B-01 — Self-consistent oula+quat Hamilton swap

`camera_rotation_oula` mutated; `camera_rotation_quaternion` re-derived
consistently. R02 PASS (designed-in), R03 PASS if attacker also
coordinates `pos`. **V₄ byte-diff FAILs** on F1..F5 (bytes differ from
signed reference). **V₄ video-hash FAILs** (on-screen camera angle
disagrees with mutated oula).

In the worst case where attacker coordinates *every* artifact, V₁/V₂/V₃
all PASS by construction. **V₄ is the only verifier that REJECTs.**

### 5.3 Wholesale dataset replacement

`dataset_id` in `buyer_reference.json` ≠ substitute's
`manifest.session_id` ⇒ ABSTAIN(`dataset_id_mismatch`), which per IL12
counts as REJECT-for-safety. With R18 (session_id binding from
`RED_TEAM_TAXONOMY.md`), wholesale replacement becomes 4-of-4 REJECT.

### 5.4 Summary

| Attack | V₁ | V₂ | V₃ | V₄ | Net |
|---|---|---|---|---|---|
| B-01 | PASS | PASS | ABSTAIN | **REJECT** | REJECT |
| B-03 | PASS | PASS | ABSTAIN | **REJECT** | REJECT |
| Wholesale replace | PASS | PASS | PASS | **ABSTAIN→REJECT** | REJECT |

V₄ is **the** load-bearing defense for B-01 and B-03.

---

## 6. Threat Model — What V₄ Does NOT Catch

V₄ has explicit boundaries. Marketing it as covering more is exactly the
LLM-shared-blind-spot pathology this architecture avoids.

### 6.1 Attacker compromises buyer's signing key

Attacker with the buyer's private key produces arbitrary references
that verify. V₄'s trust anchor collapses. **V₅+:** hardware keys
(YubiKey/HSM); multi-signer threshold scheme (k-of-n: Lark + Vera +
buyer engineer); public append-only buyer-attestation log.

### 6.2 Buyer signs corrupted reference (insider attack)

Buyer (or LLM as buyer's agent) signs a reference containing the
attacker's mutation. V₄ PASSes the mutated dataset. **V₅+:** two-person
review against out-of-band ground truth (session notes, screen
recording); buyer-side V₁/V₂/V₃ pre-check before signing; statistical
drift on signature history.

### 6.3 Statistical attacks far from reference frames

V₄ checks 5 of 9000 frames. Attacker mutating only frames 100–4499 +
4501–8998 (avoiding F3/F4/F5 and data-dependent F1/F2) escapes V₄.
**V₄ is calibration, not coverage** — V₁/V₂/V₃ catch statistical attacks;
§ 2.5 randomization raises evasion cost.

### 6.4 Producer also runs the V₄ verifier

Compromised producer can hot-fix V₄ to skip byte-diff. **Mitigation:**
vote-signing per BFT § 5 (`code_sha256`, ed25519 vote sig);
`bin/decree_audit.py` re-runs V₄ on canonical CI runner. Inherits
orchestrator-compromise concern from BFT § 12.

### 6.5 Frame-selection oracle leak

Producer predicts F1/F2 from the deterministic rule and selectively
avoids mutating those frames. § 2.5 randomization defends; deterministic
v1 is intentionally a known weakness traded for simplicity.

---

## 7. Operational Protocol

### 7.1 Signing cadence

**Default: every shipping dataset signed.** Batch (10-dataset) signing
acceptable for shadow/non-shipping in Phase A. Sampled 1-per-N
insufficient for shipping; reject.

### 7.2 SLA

```
T+0   : Producer uploads dataset.tar.gz + manifest.json.
T+0   : Orchestrator notifies buyer-signer queue (Slack/Lark webhook).
T+24h : Buyer uploads signed buyer_reference.json or REJECT signal.
T+48h : No signature ⇒ V4 = ABSTAIN(signing_timeout); decree
        HUMAN_REVIEW_PENDING.
```

Target: 90% signed within 24h. p95 < 48h.

### 7.3 Failure modes

| Scenario | V₄ vote | Decree | Response |
|---|---|---|---|
| Signed in time, sig valid, frames match | ACCEPT | per BFT § 3.3 | ship |
| Signed in time, sig valid, frames mismatch | REJECT | REJECT | producer fixes upstream |
| Signed in time, sig INVALID | ABSTAIN | REJECT-for-safety | investigate signing pipeline |
| Late signing (>24h, ≤48h) | ACCEPT | proceed; log SLA miss | warn buyer team |
| No signature at 48h | ABSTAIN(timeout) | HUMAN_REVIEW per BFT § 6.4 | Howard + Vera adjudicate |
| Buyer explicit REJECT | (none) | REJECT | producer investigates |

---

## 8. Migration Plan

### 8.1 Phase A — Shadow + tooling (weeks 1–2)

Deliverables: `bin/buyer_reference_curator.py` (computes F1..F5,
exports canonical JSON + raw-RGB hashes); `bin/verify_buyer_reference_diff.py`
(V₄ impl per § 4); `bin/audit_v4_signing_protocol.py` (IL12 enforcement);
operational runbook for Lark/Vera. Orchestrator runs V₄ **shadow mode**
(tally, do not gate). Exit: ≥ 95% shadow runs produce V₄ ACCEPT.

### 8.2 Phase B — Active gating (weeks 3–4)

V₄ becomes one of 4 BFT consensus votes per § 3.3. HUMAN_REVIEW path
connected to Slack/Lark with 24h SLA. Exit: 30 consecutive shipping
datasets with zero false-REJECTs from floating-point/video-decode.

### 8.3 Phase C — Steady state

All shipping datasets signed. Quarterly buyer-key rotation. Monthly SLA
review. Annual frame-selection rule review.

### 8.4 Rollback

Phase B false-REJECT > 5% ⇒ revert to shadow mode within 24h via
feature flag. V₄ false-ACCEPT (V₄ PASSes a B-01/B-03 the harness
should have caught) ⇒ halt all shipping until root-caused.

### 8.5 Coexistence with existing 9 residuals

V₄ is **additive, not replacing.** R01–R12 stay as-is. R13–R16 land per
their spec. R17–R23 per `RED_TEAM_TAXONOMY.md`. V₄ is the 4th BFT node
pre-allocated per BFT § 2.4. Only change: orchestrator vote tally gains
a V₄ slot.

---

## 9. Estimated Detection Lift

Per `RED_TEAM_TAXONOMY.md` § 6.2:

```
Wave-2 baseline:                       27%
+ R13 (FI-02 closed):                 +20%  → 47%
+ V₄ (B-01 + B-03 closed):            +13%  → 60%
+ R20/R22/R23 (wave-3):               +13%  → 73%
+ R18/R21 (RT top-3 follow-up):       +12%  → 85%
```

The **47% → 73%** reflects V₄ + wave-3 combined lift in the same
release. V₄'s solo contribution: **~13% absolute** (B-01 + B-03).

Operationally: 27% slip past. Remaining gaps are mostly statistical/
config, not residual-implementation. Asymmetric weakness shifts from
"LLM blind spots" to "orchestrator compromise" (BFT § 12). Buyer trust
story is materially better: an independent attestation node catches the
worst coordinated attacks.

---

## 10. ISC — Ideal State Criteria for V₄

### 10.1 Existence

- **[C-V4-01]** `bin/verify_buyer_reference_diff.py` contains callable
  `v4_buyer_reference_diff` per § 4.1. *Evidence:* AST grep returns 1.
- **[C-V4-02]** `bin/buyer_reference_curator.py` exists. *Evidence:*
  `--help` returns nonzero on missing args.
- **[C-V4-03]** `bin/audit_v4_signing_protocol.py` enforces IL12.
  *Evidence:* CI step exits 0.

### 10.2 Coverage

- **[C-V4-04]** F1..F5 selection rules defined in § 2.1.
- **[C-V4-05]** Adversarial harness contains
  `tests/byzantine/test_FI_B_01.py` and `test_FI_B_03.py`. V₄ disabled:
  BFT PASSes; enabled: BFT REJECTs. *Evidence:* harness exit codes.
- **[C-V4-06]** Buyer pubkey fingerprints in `docs/BUYER_PUBKEYS.md`.
  *Evidence:* file with Lark + Vera fingerprints.

### 10.3 Operational

- **[C-V4-07]** Per-dataset V₄ vote latency p95 < 5s after reference
  upload. *Evidence:* metrics dashboard.
- **[C-V4-08]** 90% of shipping datasets signed within 24h. *Evidence:*
  `s3://oyster-bft/_metrics/v4_signing_latency.jsonl` p90 < 86400s.
- **[C-V4-09]** 100% of shipping datasets have V₄ vote within 72h.
  *Evidence:* zero entries in `v4_unresolved_log.jsonl` older than 72h.

### 10.4 Anti-criteria — must remain false

- **[A-V4-01]** V₄ never returns `passed=True` when signature fails.
  *Evidence:* `tests/v4/test_signature_invalid_abstains.py`.
- **[A-V4-02]** V₄ never skips byte-diff for any F1..F5 frame.
  *Evidence:* `tests/v4/test_all_reference_frames_checked.py`.
- **[A-V4-03]** No reference accepted whose `signing_timestamp` is after
  `manifest.created_at`. *Evidence:* `tests/v4/test_signing_order_enforced.py`.
- **[A-V4-04]** No commit to `bin/verify_buyer_reference_diff.py` by an
  LLM identity. *Evidence:* git-blame audit (mirrors BFT § 4.2).

### 10.5 ISC Tracker

```
ISC: Ideal State Criteria
Phase: PLAN (V4 buyer-signed protocol design)
Criteria: 0 -> 9   (+9)
Anti:     0 -> 4   (+4)
+ [C-V4-01..03] V4 existence
+ [C-V4-04..06] Coverage
+ [C-V4-07..09] Operational
+ [A-V4-01..04] Anti-criteria
```

---

## 11. Open Questions for Howard

1. **Signing scheme default.** Spec defaults to ed25519 (§ 3.3). YubiKey
   issuance requires lead time. Allow HMAC-SHA256 for the first 60 days
   of Phase A while we set up hardware keys, with mandatory ed25519
   migration before Phase B active gating? My rec: yes.

2. **Frame-selection randomization (F1/F2).** § 2.5 leaves it for v2.
   Worth `selection_seed = HMAC(buyer_secret, dataset_id)` in v1, or
   defer? Cost is low; benefit is real (raises white-box evasion cost).

3. **Buyer-pubkey allowlist governance.** Who has commit rights to
   `docs/BUYER_PUBKEYS.md`? My rec: Howard's git-signed commits only,
   mirroring V₃ table population per BFT § 4.3.

4. **Video raw-RGB hash determinism across platforms.** ffmpeg/libavcodec
   produces deterministic rawvideo on a given platform but may differ
   between mac (signing) and Linux CI (verification). Standardize on a
   Docker image for both, or accept platform variance?

5. **5-frame floor — when to revisit.** After 30 days, if attackers
   demonstrate F1..F5 evasion: (a) raise to 10 frames (linear coverage),
   (b) randomize F1/F2 (Q2), or (c) accept the gap and rely on
   V₁/V₂/V₃ for the bulk? My rec: (b) first, (a) only if (b) insufficient.

6. **Buyer-side V₁/V₂/V₃ pre-check.** § 6.2 suggests running residuals
   on candidate reference frames *before* signing (catches insider
   attack). Is the buyer willing to run a 30s pre-check, and does the
   producer repo expose a buyer-runnable lite variant?

7. **HUMAN_REVIEW SLA after 48h.** § 7.2 datasets without signature at
   48h enter HUMAN_REVIEW. Decree resolution inherited from BFT § 6.4
   is 24h post-VIEW_CHANGE — worst-case 72h end-to-end. Acceptable
   for shipping, or tighten?

---

## 12. Document Provenance

- Authored by Vera Sterling (Algorithm Agent), 2026-05-06, Stream L wave-3.
- Source-of-truth: `docs/ARCH_BFT_CONSENSUS.md` § 2.4–2.5, 3.3, 6.4, 8.5,
  9, 12; `docs/SPEC_R13_MULTIMODAL.md` § 1 (IL10), § 8 (V₄ scope);
  `docs/RED_TEAM_TAXONOMY.md` § 3 (B-01, B-03), § 6.2 (scoreboard);
  `docs/BFT_TRUST_REPORT_FOR_BUYER.md`.
- Sibling specs NOT modified: `bin/verify_*.py`,
  `bin/bft_adversarial_harness.py`, parallel-stream wave-3 specs.
- Scope: V₄ design, IL12, ISC. Out of scope: Python impl, multi-signer
  threshold schemes, statistical drift detection, V₅+ defenses.

*End of SPEC_V4_BUYER_SIGNED_PROTOCOL.md.*
