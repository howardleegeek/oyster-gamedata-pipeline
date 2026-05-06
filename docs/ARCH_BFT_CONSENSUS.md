# ARCH_BFT_CONSENSUS — Byzantine Fault Tolerant Consensus Layer

> **Project:** oyster-agent-runner (game-data producer + verifier)
> **Author:** Vera Sterling (Algorithm Agent), commissioned by Howard Li
> **Date:** 2026-05-05
> **Sibling spec:** `ARCH_PINNS_BUYER_SPEC.md` (PINNs physics residual + buyer-spec parameterization — owned by a different agent, do not edit from this file)
> **Status:** Architecture / ISC — no Python yet
> **Layer:** Sits ABOVE the PINNs verification layer; PINNs verifiers are *components* (V₁, V₂) inside this consensus mesh.

---

## 0. TL;DR — The Core Bet

We are about to ship a buyer-facing dataset whose correctness is asserted by verifiers
that are themselves written by Claude. **All Claude-authored verifiers share an
unknown common-mode failure surface** (training-data bias, identical idioms,
identical blind spots in field-name literalism, identical hallucinations of
"what looks reasonable"). A single LLM verifier — even one tuned with PINNs
residuals — cannot certify itself.

This is the classical **Byzantine Generals Problem**: nodes that look loyal can
be silently traitorous, and majority vote among loyal-looking nodes is *worse*
than worthless because they can collectively rationalize a wrong answer.
We adopt the standard answer: **N ≥ 3f + 1** redundancy, with a hard
**source-independence requirement** on the N nodes.

For our threat model — at most one shared-blind-spot LLM family acting
incorrectly per dataset — we set **N = 4, f = 1**:

| Node | Role | Source class | Independence color |
|---|---|---|---|
| V₁ | Claude-authored PINNs verifier on mac-1 | Claude (Anthropic) | RED — same family as primary path |
| V₂ | GLM-/Codex-authored PINNs verifier on mac-2/GCP | GLM-4.6 (Z.AI) or Codex (OpenAI) | GREEN — different LLM family |
| V₃ | Hand-tabulated physics oracle (no LLM) | Human + spec | GREEN — zero-LLM |
| V₄ | Byte-level diff vs. buyer-signed reference frames | Human-curated bytes | YELLOW — human signed, machine-replayed |

We require **≥ 2 GREEN** sources at all times. Loss of either GREEN node halts
acceptance until restored. PBFT 3-phase commit (pre-prepare / prepare / commit)
runs over the four votes; a 2–2 tie triggers a documented view-change to a
human arbiter pair (Howard + Vera).

Goal: drive the **false-PASS rate from "unknown / unbounded"** (single-verifier
self-attestation) **to < 1 %** under at least one Byzantine fault.

---

## 1. Threat Model — Why Single-Verifier PASS Is Worthless

### 1.1 The shared-blind-spot axiom

Every verifier in the current `bin/verify_*.py` set was authored or heavily
edited by Claude. The producer pipeline that generates the data they certify
was *also* authored or heavily edited by Claude. Both ends of the loop draw
from the same training distribution, the same coding idioms, the same
"reasonable-looking-but-wrong" attractors. Concretely we have already observed:

- Both producer and verifier silently accepted `camera_rotation_euler` as an
  alias for the spec-mandated `camera_rotation_oula` (PRD_FORMULAS § F.3).
- Both producer and verifier hardcoded numeric thresholds in source rather than
  loading from the canonical `PRD_FORMULAS.md`, allowing a typo'd constant to
  pass against itself.
- Quaternion order `[w, x, y, z]` vs. `[x, y, z, w]` (PDF acceptance #6) was
  inverted on *both* sides for two iterations and the test suite still went
  green.

Each is a **collective honesty defection** — the verifiers were truthful about
what they were checking, but what they were checking was wrong in the same
direction as what was being produced.

### 1.2 Why majority vote among "Claude verifiers" does not help

If all four nodes are Claude-derived, a vote is not a vote — it is a
single distribution sampled four times. The Byzantine Generals Problem
formalizes the lower bound: **at most ⌊(N − 1) / 3⌋ traitors can be tolerated
only if loyal nodes are independent.** Stack four correlated nodes and *f*
collapses to zero.

### 1.3 Adversary capability we assume

The "adversary" is not a malicious actor — it is **shared blind spot**, a
non-adversarial Byzantine fault. In one consensus round, the adversary can:

- Cause **at most one of {V₁, V₂}** to silently endorse a wrong artifact
  (single-LLM blind spot).
- Cause **at most one of {V₃, V₄}** to be *unavailable* (table not yet
  populated, buyer signature missing) — but **not** to actively lie. V₃ and V₄
  fail closed: if input is missing, vote is `ABSTAIN`, not `ACCEPT`.

The dual-LLM blind spot (V₁ ∧ V₂ both wrong) is treated as a **degraded mode**
that must produce a 2–2 tie, not a 4–0 false PASS. § 6.2 covers this case.

### 1.4 What we are NOT defending against

- Live network attacker compromising the consensus orchestrator. Out of scope —
  CI is on GitHub-hosted runners, threat model assumes trusted infrastructure.
- Insider rewriting V₃'s physics table after sign-off. Mitigated by git-signed
  commits to `bin/physics_oracle_table.json` (§ 4.3) but not by consensus
  layer.
- Buyer themselves being adversarial. Out of scope — buyer is the customer.

---

## 2. The Four Verifiers — Identity, Inputs, Outputs

### 2.1 V₁ — Claude PINNs Verifier (RED, mac-1)

| Attribute | Value |
|---|---|
| Author | Claude (primary repo workflow) |
| Runtime | mac-1, Python 3.11, native |
| Code path | `bin/verify_pinns_claude.py` (sibling spec defines internals) |
| Inputs | `dataset.tar.gz`, `PRD_FORMULAS.md` |
| Output | Vote JSON (§ 5) |
| Expected p(false-PASS \| dataset wrong) | High (shared blind spot with producer) — exact value unknown, treated as 1.0 in worst-case math |
| Independence color | RED |

V₁ is fast (~30 s/dataset) and full-featured. It is the productivity workhorse,
not the trust anchor.

### 2.2 V₂ — Non-Claude PINNs Verifier (GREEN, mac-2 or GCP)

| Attribute | Value |
|---|---|
| Author | GLM-4.6 (via `claude-glm` on mac-2) **OR** Codex (via `codex exec` on a GCP node) — must be re-authored from spec, not ported from V₁ |
| Runtime | mac-2 (preferred) or GCP `glm-node-2`, dispatched via `oyster/infra/dispatch` |
| Code path | `bin/verify_pinns_glm.py` — separate file, separate import graph |
| Inputs | `dataset.tar.gz`, `PRD_FORMULAS.md` (loaded from same source-of-truth file, that's intentional) |
| Output | Vote JSON (§ 5) |
| Hard constraint | **MUST NOT** `import` anything from V₁'s code path. Static import-graph audit (§ 4.2) blocks any PR that creates such an edge. |
| Independence color | GREEN |

The point of V₂ is *not* to be smarter than V₁. It is to be **wrong in a
different direction** when it is wrong. PINNs residual logic must be
re-derived from the formulas in `PRD_FORMULAS.md`, not paraphrased from
`verify_pinns_claude.py`.

### 2.3 V₃ — Physics Oracle Lookup Table (GREEN, CI)

| Attribute | Value |
|---|---|
| Author | **Howard + Vera, by hand**, signed via git commit |
| Runtime | CI runner (deterministic) |
| Code path | `bin/verify_physics_oracle.py` (logic) + `bin/physics_oracle_table.json` (data) |
| Logic complexity | < 200 LOC. Pure lookup + tolerance check. No regressions, no LLM, no neural. |
| Output | Vote JSON (§ 5) |

V₃'s only job is to assert a small set of **closed-form physics ground truths**
derived directly from `PRD_FORMULAS.md` § A and § B. Examples (illustrative; the
real table is filled per § 4.3):

| Test ID | Input | Expected output (in dataset) | Tolerance |
|---|---|---|---|
| ORACLE_A1_yaw90 | `*_rotation_oula = (0°, 90°, 0°)` | `*_rotation_quaternion ≈ (0, 0.7071068, 0, 0.7071068)` | ‖Δq‖ < 1e-4 |
| ORACLE_A1_pitch_-45 | `*_rotation_oula = (-45°, 0°, 0°)` | `*_rotation_quaternion ≈ (-0.3826834, 0, 0, 0.9238795)` | ‖Δq‖ < 1e-4 |
| ORACLE_A2_unit | any frame | `‖*_rotation_quaternion‖ ∈ [0.99, 1.01]` | per spec § A2 |
| ORACLE_A5_dt | consecutive frames | `Δt ∈ [28.33 ms, 38.33 ms]` | spec § A5 ±5 ms |
| ORACLE_B14_order | any frame | quaternion array length 4, ordered `[x, y, z, w]` per § B14 | exact |
| ORACLE_B15_intrinsics | any frame | `fx == fy` | exact |

The full table covers **all 12 PRD formulas / constants** in `PRD_FORMULAS.md`
(see ISC C-09). Each row has a PDF page citation in a `source` field.

V₃ has zero LLM in the loop after table population. Its blind spot is
**"the table itself is wrong"**, mitigated by:

1. The table is reviewed line-by-line by Howard before merge (§ 4.3).
2. Every entry must cite the PDF page it came from.
3. CI checks the table's SHA-256 against a value committed to `docs/PRD_DIGEST.md`.

### 2.4 V₄ — Buyer-Reference Byte Diff (YELLOW, CI)

| Attribute | Value |
|---|---|
| Author | Howard, with buyer in the loop |
| Runtime | CI runner |
| Code path | `bin/verify_buyer_reference_diff.py` |
| Reference asset | `tests/buyer_reference/sample_5frames.tar.gz` — 5 frames signed by buyer |
| Output | Vote JSON (§ 5) |

V₄ is the cheapest possible reality check: take 5 specific frames from any new
dataset (chosen by `frame_id % len(dataset) // 5`), and diff them byte-for-byte
(JSON canonicalized, EXR float32 binary) against the corresponding 5 frames
from a sample the buyer literally signed off on.

V₄'s blind spot is **only** the 5 sample frames — it cannot detect errors that
do not happen to fall on those frames. That is acceptable; V₄'s purpose is
*calibration*, not coverage. If V₄ disagrees, V₃ probably also disagrees
(because the buyer-signed sample passed V₃ at sign-off time), so 2 of 4 GREEN
nodes flip and the dataset is rejected even if V₁ and V₂ both endorsed.

### 2.5 Independence color summary and the 2-GREEN rule

```
RED ── shared family with primary path
YELLOW ── human attestation, machine replay
GREEN ── independently authored, no shared LLM family

Required at every consensus round:  count(GREEN) ≥ 2.
Loss of V₂ or V₃ → halt; do not run consensus.
Loss of V₁ or V₄ → degraded but allowed (warn + continue).
```

---

## 3. PBFT 3-Phase Pipeline

We adapt Castro–Liskov PBFT to a single-shot dataset acceptance flow. There is
**one decree per dataset** (`ACCEPT` or `REJECT`); no leader rotation between
decrees, no client retry across views.

```
                         ┌──────────────────────────────────┐
                         │      Producer (mac-1 / mac-2)    │
                         │   builds dataset.tar.gz + sha    │
                         └───────────────┬──────────────────┘
                                         │
                          PRE-PREPARE    │  S3 upload + manifest
                                         ▼
                         ┌──────────────────────────────────┐
                         │ Orchestrator (GitHub Actions)    │
                         │ matrix-fans-out vote requests    │
                         └────┬───────┬─────────┬──────────┬┘
                              │       │         │          │
                          ┌───▼─┐ ┌───▼─┐  ┌────▼─┐  ┌─────▼──┐
                          │ V₁  │ │ V₂  │  │  V₃  │  │   V₄   │
                          │mac-1│ │mac-2│  │  CI  │  │   CI   │
                          └───┬─┘ └───┬─┘  └────┬─┘  └─────┬──┘
                              │       │         │          │
                          PREPARE: each writes vote JSON to
                              s3://oyster-bft/<run-id>/vote_V*.json
                                       │
                                       ▼
                         ┌──────────────────────────────────┐
                         │ Orchestrator collects 4 votes    │
                         │ COMMIT iff #ACCEPT ≥ 3 of 4      │
                         └───────────────┬──────────────────┘
                                         │
                                         ▼
                                   ACCEPT / REJECT
                                   / VIEW_CHANGE
```

### 3.1 Pre-prepare

- Producer builds `dataset.tar.gz`, computes `SHA-256`, writes manifest:
  ```
  s3://oyster-bft/<run-id>/manifest.json   (signed by producer key)
  s3://oyster-bft/<run-id>/dataset.tar.gz  (read-only, immutable)
  ```
- Orchestrator validates manifest signature (rejects if forged) and emits
  `pre-prepare` event to the matrix.

### 3.2 Prepare

- Each verifier independently:
  1. Pulls `dataset.tar.gz` from S3 (no peer-to-peer chatter; verifiers do
     **not** see each other's votes during this phase — this is the
     independence-preserving rule).
  2. Runs its check.
  3. Writes vote JSON to `s3://oyster-bft/<run-id>/vote_V<n>.json`.
- Orchestrator monitors S3 for 4 votes.
- Per-verifier deadline: **5 min wall-clock** from `pre-prepare` event.
  Late vote = `ABSTAIN` (treated as REJECT for safety, see § 8.2).

### 3.3 Commit

- Orchestrator counts ACCEPT votes:
  - **#ACCEPT ≥ 3 ⇒ ACCEPT.** Ship dataset.
  - **#ACCEPT ≤ 1 ⇒ REJECT.** Discard dataset. Producer fixes upstream.
  - **#ACCEPT == 2 ⇒ VIEW_CHANGE.** Hand to human arbiters (§ 6.4).
- Decree is recorded in `s3://oyster-bft/<run-id>/decree.json` with all 4
  vote URIs and orchestrator's signed conclusion.

### 3.4 Why no leader / no view rotation between decrees

Each dataset is a fresh decree. There is no shared mutable state across
decrees, so the standard PBFT view-change-for-leader-failure machinery is
unnecessary. We retain the *name* "view-change" for the 2-2 tie path because
its remediation (escalate to a different decision authority) is structurally
identical.

---

## 4. Independence Audit Protocol

This is the protocol that makes the consensus layer worth the cost. Without
audited independence, four verifiers are computationally redundant but
epistemically a single node.

### 4.1 Static import-graph audit

A CI step (`bin/audit_verifier_independence.py`, < 100 LOC) runs on every PR
touching `bin/verify_*`:

1. Parse AST of each `bin/verify_*.py`.
2. Build an import graph rooted at each verifier entrypoint.
3. **Fail the PR** if any verifier's transitive import set intersects another
   verifier's transitive import set, *except* for an explicit allowlist:
   - `bin/load_prd_formulas.py` — the canonical formula loader. Shared by
     design (this is the only sanctioned shared dependency, and it loads from
     `docs/PRD_FORMULAS.md` which is the source of truth).
   - Standard library + numpy + pydantic for schema. Allowed but logged.
4. The allowlist itself requires Howard's git-signed commit to change.

### 4.2 LLM-author attribution audit

Each `bin/verify_*.py` has a header block:

```python
# AUTHOR_LLM: claude-opus-4.7
# AUTHORED_AT: 2026-05-04T12:34:56Z
# AUTHORED_FROM_SPEC: docs/PRD_FORMULAS.md@<sha>
# AUTHORED_NODE: mac-1
```

CI verifies:

- V₁'s `AUTHOR_LLM` is a Claude family identifier.
- V₂'s `AUTHOR_LLM` is **not** a Claude family identifier (must match `^(glm-|codex-|kimi-|deepseek-|minimax-|gpt-)`).
- V₃ has `AUTHOR_LLM: HUMAN` (no LLM allowed for table population).
- V₄ has `AUTHOR_LLM: HUMAN` for the diff harness; the reference frames have
  `BUYER_SIGNATURE: <pgp-key-fingerprint>`.

Tampering with these headers without a corresponding commit-author / signature
match fails the PR.

### 4.3 V₃ table population workflow

The hand-tabulation rule is the only thing keeping V₃ green. Workflow:

1. Vera (or the GLM-on-mac-2 acting as scribe) drafts a row in
   `bin/physics_oracle_table.json` citing the exact PDF page.
2. Howard reviews each row, signs the commit (`git commit -S`).
3. CI re-derives the expected value **using closed-form math**, not the
   verifier code, and refuses the merge if any row's computed value disagrees
   with the committed value.
4. Once merged, the table's SHA-256 is mirrored into `docs/PRD_DIGEST.md` so a
   later attacker who silently rewrites the table also has to forge the digest
   commit.

LLMs may **suggest** rows but never commit them. Detected via git-blame on
the JSON file: any commit whose author is an LLM-bot identity is rejected
post-merge by a nightly audit.

### 4.4 V₂ re-authoring rule

Every quarter (or every PRD revision, whichever is sooner), V₂ is re-authored
from scratch against the new `PRD_FORMULAS.md`, by a different non-Claude LLM
on a different node. The previous V₂ is archived to
`bin/archive/verify_pinns_glm_<date>.py`. This refreshes the independence
guarantee against LLM training-data drift.

---

## 5. Vote JSON Schema

All four verifiers emit identical JSON. The schema is intentionally narrow:
votes are evidence, not opinion.

```json
{
  "schema_version": "bft-vote/v1",
  "run_id": "2026-05-05T14:23:11Z-abc123",
  "verifier_id": "V2",
  "verifier_role": "non_claude_pinns",
  "verifier_runtime": {
    "node": "mac-2",
    "llm_author": "glm-4.6",
    "code_sha256": "5f8e...c1",
    "table_sha256": null
  },
  "dataset_uri": "s3://oyster-bft/2026-05-05T14:23:11Z-abc123/dataset.tar.gz",
  "dataset_sha256": "9b3c...77",
  "started_at": "2026-05-05T14:23:42Z",
  "finished_at": "2026-05-05T14:25:09Z",
  "decision": "ACCEPT | REJECT | ABSTAIN",
  "evidence": [
    {
      "check_id": "PINNS_RES_A1_yaw_to_quat",
      "result": "PASS | FAIL | SKIP",
      "frame_count": 9000,
      "fail_count": 0,
      "max_residual": 4.2e-5,
      "tolerance": 1e-4,
      "examples": []
    }
  ],
  "rejection_reasons": [],
  "signature": "ed25519:..."
}
```

**Critical fields:**

- `decision = "ABSTAIN"` is reserved for: missing input, runtime crash,
  table not yet populated for the formula in question, dependency timeout.
  ABSTAIN does **not** count toward the 3-of-4 ACCEPT threshold; it counts
  as REJECT for the conservative path (§ 8.2).
- `signature` is over the canonicalized JSON minus the `signature` field
  itself. Each verifier has a distinct ed25519 keypair stored as a CI secret.
  Forged votes fail orchestrator verification and are dropped.
- `code_sha256` / `table_sha256` lock the vote to the exact code that
  produced it, so post-hoc audits can detect "verifier was secretly hot-fixed
  during the run".

---

## 6. Byzantine Adversary Harness

This is where we *prove* the consensus layer works, not just that it runs.

### 6.1 Single-traitor scenario — V₁ alone defects

**Setup.** Inject a known-bad dataset built specifically to exploit Claude's
shared blind spot (e.g., uses `camera_rotation_euler` instead of
`camera_rotation_oula`). Verify against V₁ that does **not** check
field-name literalism strictly (the historical bug).

**Expected.** V₁ = ACCEPT (false). V₂ = REJECT (different LLM caught the
literal name). V₃ = REJECT (table-driven, no aliasing). V₄ = REJECT
(byte-diff on signed sample fails).

**Decree.** 1 ACCEPT + 3 REJECT ⇒ **REJECT.** Pass.

**False-PASS rate target.** 0/100 fault-injected runs may produce ACCEPT.

### 6.2 Worst-case LLM collusion — V₁ + V₂ both defect

**Setup.** Inject the dataset above. Force both V₁ and V₂ to share the same
training-data blind spot by ensuring V₂ was authored before the alias rule
was added to `PRD_FORMULAS.md`. (We deliberately use an *old V₂* from the
archive directory.)

**Expected.** V₁ = ACCEPT (false). V₂ = ACCEPT (false). V₃ = REJECT.
V₄ = REJECT.

**Decree.** 2 ACCEPT + 2 REJECT ⇒ **VIEW_CHANGE → human arbiters.**
Howard + Vera adjudicate, surely REJECT (they wrote the spec).
Net outcome: **REJECT.**

**This is the core robustness claim.** Even with maximum-correlated LLM
failure, the system still rejects, because GREEN nodes V₃ and V₄ do not depend
on either LLM family.

**Tie-trigger rate target.** ≤ 5 % across legitimate workflows. Higher means
verifiers genuinely disagree, which is itself a signal of systemic ambiguity.

### 6.3 GREEN-node bug — V₃ table is wrong

**Setup.** Inject a *correct* dataset, but seed V₃ with a typo'd table row
(e.g., wrong yaw90 quaternion).

**Expected.** V₁ = ACCEPT, V₂ = ACCEPT, V₃ = REJECT (false), V₄ = ACCEPT.

**Decree.** 3 ACCEPT + 1 REJECT ⇒ **ACCEPT.** Pass — system is robust to
single-table-error V₃ defection. The corrupted table row is then surfaced by
the post-decree audit (§ 4.3 sha mirror), which is what *should* drive V₃
table fixes, not consensus-blocked deliveries.

### 6.4 View-change to human arbiters

When the orchestrator records a 2-2 tie, it:

1. Tags the decree `VIEW_CHANGE_PENDING` and freezes acceptance.
2. Posts to a dedicated Slack/Telegram channel with all 4 vote JSONs and
   diffs of each verifier's `evidence[]`.
3. Howard and Vera independently produce verdicts. If they agree, the decree
   updates to that verdict. If they disagree, Howard wins (he is the buyer
   relationship owner) and Vera files a post-mortem ISC C-12.

SLA: human verdict within 24 hours of view-change post. Datasets sitting
longer count against operational ISC C-08.

### 6.5 Five canonical fault-injection cases

Required pre-merge tests (`tests/byzantine/`):

| Fault ID | Mutation | Expected nodes that REJECT | Expected decree |
|---|---|---|---|
| FI-01 | Swap quaternion order to `[w,x,y,z]` | V₂, V₃, V₄ | REJECT |
| FI-02 | Replace keyCode 87 ('W') with 119 ('w' lowercase) | V₂, V₃, V₄ | REJECT |
| FI-03 | Inject fps = 60 frames into a 30 fps stream | V₂, V₃, V₄ | REJECT |
| FI-04 | Convert speed m/s to km/h silently (×3.6) | V₂, V₃, V₄ | REJECT |
| FI-05 | Rename `camera_Follow Offset` → `camera_follow_offset` | V₂, V₃, V₄ | REJECT |

Each fault must have **false-PASS rate < 1 %** measured over 1000 randomized
trials with single-V₁ traitor injection (i.e., V₁ permitted to silently
endorse the mutation; consensus must still REJECT).

---

## 7. Operational Topology

### 7.1 Node placement

| Verifier | Host | Reason |
|---|---|---|
| V₁ | mac-1 | Co-located with primary producer, fast iteration |
| V₂ | mac-2 (preferred) or `glm-node-2` GCP | Different physical machine, different LLM author |
| V₃ | GitHub Actions runner | Determinism — pure-python, no environment state |
| V₄ | GitHub Actions runner | Same — byte-diff is deterministic |
| Orchestrator | GitHub Actions matrix job | Trusted CI infra, signed workflow |

### 7.2 Dispatch flow

V₂ on mac-2 is launched via the existing dispatch infrastructure:

```bash
ssh howard-mac2 'cd ~/oyster-agent-runner && \
  python3 bin/verify_pinns_glm.py \
    --dataset s3://oyster-bft/<run-id>/dataset.tar.gz \
    --vote-out s3://oyster-bft/<run-id>/vote_V2.json \
    --run-id <run-id>'
```

Or, if mac-2 is unavailable, fall over to GCP via `dispatch.py`. The fallback
is allowed because both nodes run the **same** V₂ binary; it is the LLM that
*authored* V₂ that matters for independence, not the LLM running it.

### 7.3 Communication — strictly via S3

Verifiers do **not** talk to each other. They only talk to S3:

- READ: `s3://oyster-bft/<run-id>/dataset.tar.gz` (immutable post-prepare)
- READ: `s3://oyster-bft/<run-id>/manifest.json`
- WRITE: `s3://oyster-bft/<run-id>/vote_V<n>.json` (append-only via
  S3 object lock)

This eliminates any pairwise channel where one verifier could leak intermediate
state to another and accidentally re-correlate them.

### 7.4 GitHub Actions matrix sketch

```yaml
jobs:
  consensus:
    strategy:
      matrix:
        verifier:
          - { id: V1, runs-on: self-hosted-mac1, llm: claude }
          - { id: V2, runs-on: self-hosted-mac2, llm: glm }
          - { id: V3, runs-on: ubuntu-latest,    llm: human }
          - { id: V4, runs-on: ubuntu-latest,    llm: human }
    steps:
      - name: Run verifier ${{ matrix.verifier.id }}
        run: bin/verify_${{ matrix.verifier.id }}.py ...
      - name: Upload vote
        run: aws s3 cp vote.json s3://...

  decree:
    needs: [consensus]
    steps:
      - name: Tally
        run: bin/orchestrator_tally.py --run-id ${{ github.run_id }}
```

---

## 8. Failure Modes

### 8.1 Network partition — V₂ unreachable

- Orchestrator's 5-min deadline elapses without V₂ vote.
- Orchestrator records `vote_V2.json` as `decision: ABSTAIN, reason: TIMEOUT`.
- Tally: 3 votes total. **Rule:** if any GREEN node ABSTAINS, the consensus
  round halts with `decree: PARTITION` (not REJECT, not ACCEPT). Producer
  re-queues; this is not a rejection of the dataset, it is a rejection of
  the consensus round.

### 8.2 Slow LLM API — V₂ exceeds 5 min

- Same as 8.1: ABSTAIN. We **do not** extend the deadline; predictable
  timeboxes are part of the operational ISC.
- Mitigation: V₂ tasks are dispatched with `MAX_TURNS=1` and prompt is
  pre-baked. Average runtime < 90 s in practice.

### 8.3 Orchestrator compromise

This is the asymmetric weakness — a single compromised orchestrator can
forge a decree. Defenses (in priority order):

1. **Decree signing.** Orchestrator's tally job signs the final decree with a
   key stored only in GitHub Actions OIDC — not exfiltratable from a single
   compromised job.
2. **Decree mirror.** A separate scheduled job (`bin/decree_audit.py`) re-tallies
   from raw vote JSON 1 hour after each decree and posts a **mismatch alert**
   if its independent tally disagrees with the committed decree.
3. **Buyer-visible audit log.** Every decree URI is appended to a public
   `decrees.jsonl` log with hash chaining (each entry includes the prev
   decree's SHA), so retroactive tampering is detectable by buyer.

The 2 + 3 combination means even a compromised orchestrator must also
compromise the audit job and the buyer's chain check, which is materially
harder.

### 8.4 V₃ table not yet covering a new formula

If `PRD_FORMULAS.md` adds a new constant before V₃'s table is updated, V₃
emits `ABSTAIN` for that specific check, **not** ACCEPT. Per § 8.1's rule,
GREEN-ABSTAIN halts the round.

### 8.5 Buyer-signed sample expires

V₄'s reference is fixed at sample sign-off. If the dataset distribution
drifts so far that the 5 reference frames are no longer representative,
V₄'s diff fails noisily — which is the *correct* signal that we need a
new buyer sample sign-off, not a bug.

### 8.6 Two-of-four nodes silent

Already covered by § 8.1 cascade: any GREEN ABSTAIN ⇒ PARTITION halt.
Two ABSTAIN ⇒ definite halt regardless of color.

---

## 9. Migration Plan — From Single Verifier to BFT Cluster

### 9.1 Current state (2026-05-05)

- `bin/verify_action_camera.py`, `bin/verify_prd_schema.py`,
  `bin/verify_round_trip.py`, `bin/verify_visual_diff.py` exist as
  Claude-authored single verifiers.
- They *are* run, but their PASS is taken as decisive — exactly the
  collective-honesty problem we are now correcting.

### 9.2 Phase A — Dual-run shadow (weeks 1–2)

- V₁ = wraps the existing four scripts as a single PINNs-aware aggregator.
- V₂ commissioned on mac-2 (GLM author).
- V₃ table populated, ~half the rows.
- V₄ buyer-signed sample acquired.
- Orchestrator runs in `shadow` mode: tallies but does **not** gate
  shipment. Existing single-verifier path still gates.
- We log every disagreement; each becomes a ticket.

**Exit criterion for Phase A (3 weeks):** ≥ 95 % of shadow runs have
4-vote agreement. Disagreements categorized and resolved.

### 9.3 Phase B — Active gating (weeks 3–6)

- Orchestrator decree becomes the gate. Single-verifier scripts kept as
  V₁ internals only.
- Old `bin/verify_*.py` files at top level are **deleted** (not just
  un-imported) to remove the temptation of bypass.

**Exit criterion:** 30 consecutive datasets shipped under BFT decree with
zero buyer-side complaints.

### 9.4 Phase C — Steady state

- Quarterly V₂ re-author rotation.
- Monthly V₃ table audit.
- Buyer-sample refresh on every PRD revision.

### 9.5 Rollback

If Phase B fails (false-REJECT rate > 10 %), revert to Phase A shadow mode
within 24 h via a feature flag in `oyster/infra/dispatch`. Producer continues
shipping under single-verifier; we diagnose and re-launch.

---

## 10. ISC — Ideal State Criteria (the main deliverable)

Every criterion below is binary (YES/NO verifiable in ≤ 1 second of
inspection) and maps to either a static repo state, a CI artifact, or a
recorded metric.

### 10.1 Independence criteria (C₁–C₄)

- **[C-01]** V₁ and V₂ have **disjoint transitive import sets** (intersection ⊆ allowlist of § 4.1). *Evidence:* `bin/audit_verifier_independence.py` exit 0 in latest CI run.
- **[C-02]** Authors-of-V₁/V₂/V₃/V₄ — read from `AUTHOR_LLM` headers — are 4 distinct identities, with V₁ Claude-family, V₂ non-Claude-family, V₃ and V₄ HUMAN. *Evidence:* `bin/audit_author_attribution.py` PASS.
- **[C-03]** Source-independence score across any pair {Vᵢ, Vⱼ}, computed as `1 − |shared_imports| / |max(imports_i, imports_j)|`, is **≥ 0.8**. *Evidence:* score field of audit job's JSON output ≥ 0.8 for all 6 pairs.
- **[C-04]** At every consensus round, `count(GREEN_nodes) ≥ 2` (i.e., V₂ and V₃ both reachable and not ABSTAIN). *Evidence:* `decree.json.green_count` ≥ 2 in 100 % of decrees over the last 30 days.

### 10.2 Coverage criteria (C₅–C₇)

- **[C-05]** V₃ physics oracle table covers **all 12 PRD residuals/constants** in `PRD_FORMULAS.md` § A + § B. *Evidence:* `bin/audit_oracle_coverage.py` reports 12/12.
- **[C-06]** All 5 fault-injection cases (FI-01 … FI-05 in § 6.5) exist as `tests/byzantine/test_FI_*.py` and pass on `main`. *Evidence:* CI green for those tests.
- **[C-07]** V₄'s buyer-signed reference frames carry a valid PGP signature whose fingerprint matches `BUYER_PUBKEY_FINGERPRINT` constant. *Evidence:* `bin/verify_buyer_signature.py` PASS.

### 10.3 Operational criteria (C₈–C₁₀)

- **[C-08]** Per-dataset consensus wall-clock time **< 5 minutes** at p95. *Evidence:* GitHub Actions consensus job duration histogram, last 50 runs.
- **[C-09]** False-PASS rate under single-Byzantine fault (V₁ traitor) **< 0.5 %** over 1000 fault-injected trials per fault category (FI-01..FI-05). *Evidence:* `tests/byzantine/false_pass_rate_report.json` from nightly job.
- **[C-10]** View-change trigger rate (2-2 tie) **≤ 5 %** over the last 100 legitimate (non-fault-injected) consensus rounds. *Evidence:* orchestrator metrics dashboard.

### 10.4 Audit & integrity criteria (C₁₁–C₁₂)

- **[C-11]** Every decree in the rolling 30-day window has a **matching independent re-tally** from `bin/decree_audit.py`. *Evidence:* zero entries in `audit_mismatch_log`.
- **[C-12]** All view-change events resolved by human arbiters within **24 hours**. *Evidence:* `view_change_log.jsonl` shows zero entries with `resolution_age > 86400s` over the last quarter.

### 10.5 Anti-criteria (A-01 … A-04) — must remain false

- **[A-01]** No `bin/verify_*.py` shall import from another `bin/verify_*.py`. (Restated as anti-criterion of C-01 — a 0-tolerance hard line, not a score threshold.)
- **[A-02]** No commit to `bin/physics_oracle_table.json` shall be authored by an LLM identity. (`git log --format="%an" bin/physics_oracle_table.json | grep -iE 'bot|claude|glm|opus|kimi'` must be empty.)
- **[A-03]** No vote JSON shall be accepted by orchestrator without a verified ed25519 signature.
- **[A-04]** No PR merging to `main` shall reduce the verifier independence score below 0.8.

### 10.6 ISC Tracker for this design

```
┌─ 🎯 ISC: Ideal State Criteria ────────────────────┐
│ Phase: PLAN (BFT consensus design)                │
│ ✅ Criteria: 0 → 12  (+12)                        │
│ ⛔ Anti:     0 → 4   (+4)                         │
├───────────────────────────────────────────────────┤
│ ➕ [C-01..C-04] Independence criteria             │
│ ➕ [C-05..C-07] Coverage criteria                 │
│ ➕ [C-08..C-10] Operational criteria              │
│ ➕ [C-11..C-12] Audit criteria                    │
│ ➕ [A-01..A-04] Anti-criteria                     │
└───────────────────────────────────────────────────┘
```

---

## 11. Open Questions (for sibling spec author + Howard)

1. **PINNs ↔ V₁/V₂ boundary.** This spec treats V₁ and V₂ as black-box
   "PINNs verifiers". The sibling `ARCH_PINNS_BUYER_SPEC.md` defines what
   *inside* them is. We must ensure: V₂ is re-authored from
   `PRD_FORMULAS.md`, not from V₁'s code. Cross-reference once that spec
   lands.

2. **V₂ author rotation cadence.** Quarterly is a guess. If the LLM market
   moves faster (new foundation model every month), we may want
   per-PRD-revision rotation instead. Recommend revisiting after 90 days
   of data.

3. **ABSTAIN counts as REJECT or as PARTITION.** Currently ABSTAIN has two
   different downstream effects (§ 5 says "counts as REJECT for safety" but
   § 8.1 says GREEN-ABSTAIN ⇒ PARTITION). The intended rule: GREEN-ABSTAIN
   ⇒ PARTITION halt; RED/YELLOW-ABSTAIN ⇒ counted as REJECT. We should
   make this explicit in the orchestrator code and in C-04.

4. **Buyer-signed sample size = 5.** Statistical floor; could go to 10 if
   buyer is willing to sign more frames. Larger samples raise V₄'s
   coverage materially (linearly in sample size for non-adversarial drift).

---

## 12. Weakest Byzantine Defense Point — Vera's Honest Assessment

**The weakest link is the orchestrator itself, full stop.**

Reasoning:

- V₁/V₂/V₃/V₄ have N=4, f=1 redundancy and audited independence.
- The orchestrator is N=1, f=0 — there is exactly one tally job per decree.
- A compromised orchestrator can forge any decree it wants. § 8.3's three
  defenses (signed decree, audit re-tally, buyer-visible chain) are all
  mitigations, not consensus.

This is the asymmetric attack surface. Concretely:

- An attacker who controls the GitHub Actions workflow definition file
  (`.github/workflows/bft.yml`) can rewrite the tally rule from "≥ 3 ACCEPT"
  to "≥ 1 ACCEPT" and ship false data on a single accomplice vote, with the
  decree audit only catching it 1 hour later.
- The decree audit job runs on the same GitHub Actions infrastructure;
  same-tenant compromise defeats both.
- The buyer-visible chain (§ 8.3 #3) is the only true cross-tenant defense,
  and it relies on the buyer actually checking the chain — which most buyers
  don't.

Recommended next-step hardening, **out of scope for this spec** but worth
queuing:

1. **Run the audit job on a different cloud** (e.g., `aliyun-cluster-only`
   per Howard's iron law) so a single GitHub compromise cannot tamper with
   both tally and audit.
2. **Sign the workflow definition file itself** with a key Howard holds
   offline; CI refuses to run a workflow whose definition's SHA is not
   on the signed allowlist.
3. **Push decrees to an immutable WORM store** the buyer also has read access
   to (e.g., a public S3 bucket with object lock + cross-account read).

The four-verifier consensus *is* robust. The orchestration *is not*. Howard
should know this, the sibling spec author should know this, and every
buyer-facing acceptance discussion should know this.

---

## 13. Document Provenance

- Authored by: Vera Sterling (Algorithm Agent), 2026-05-05
- Source-of-truth files cited:
  - `docs/PRD_FORMULAS.md` — § A1 quaternion, § A2 unit quat, § A5 Δt, § B14 order, § B15 intrinsics
  - `docs/PRD_DIGEST.md` — anti-cycle warning, iron laws #1–#10
  - PDF: `video+action+camera数据收集需求文档 - Lark Docs.pdf` (page numbers via PRD_FORMULAS index)
- Sibling spec NOT modified: `ARCH_PINNS_BUYER_SPEC.md` (owned by another
  agent; this file deliberately defers all PINNs-internals questions to it).
- File scope: BFT consensus only. PINNs internals, buyer-spec parameter
  definitions, training-loop integration are **out of scope**.
