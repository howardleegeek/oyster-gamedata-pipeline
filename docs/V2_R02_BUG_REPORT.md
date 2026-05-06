# V₂ MiniMax R02 Sign-Convention Bug — Field Report 2026-05-05

> **Status**: 🔴 confirmed structural bug; V₂ FAILs R02 100% on PRD-correct sample
> **Detected by**: BFT prepare phase across 12 sampled frames of v0.20.0 sample tarball
> **Independent confirmation**: V₁ Claude PASS (Hamilton ZYX), V₃ Physics-Table 8/8 audited PASS

## Failure pattern

| Frame range tested | R02 verdict pattern | Disagreement |
|--------------------|--------------------|--------------|
| frames 0–9 (consecutive) | `PF-` | 12/12 |
| frame 4500 (middle) | `PF-` | yes |
| frame 8999 (end) | `PF-` | yes |

V₃ ABSTAINs on R02 because sample uses continuous ramp angles
(`yaw = 1.5·(i/30) % 360`) outside V₃'s 8-row textbook table.

## Root cause — axis assignment swap

V₂'s `r02_euler_quat_consistency` implements:

```python
qx = sr * cp * cy - cr * sp * sy
qy = cr * sp * cy + sr * cp * sy   # ← qy driven by sp (pitch), not sy (yaw)
qz = cr * cp * sy - sr * sp * cy   # ← qz driven by sy (yaw), should be 0 for pure yaw
qw = cr * cp * cy + sr * sp * sy
```

Substituting pure yaw rotation (yaw=90°, pitch=roll=0):
```
cp=1, sp=0, cr=1, sr=0, cy=cos(45°)=0.7071, sy=sin(45°)=0.7071
qx = 0
qy = 1·0·cy + 0·1·sy = 0      ← WRONG, should be 0.7071
qz = 1·1·sy - 0·0·cy = 0.7071  ← WRONG, should be 0
qw = 0.7071
```

**V₂ ended up writing pitch-as-Y-axis, yaw-as-Z-axis**, which contradicts
PRD page 3 "Pitch 绕 X 轴, Yaw 绕 Y 轴, Roll 绕 Z 轴".

## Correct ZYX intrinsic (V₁ Claude reference)

```python
qw = cp * cy * cr + sp * sy * sr
qx = sp * cy * cr - cp * sy * sr
qy = cp * sy * cr + sp * cy * sr   # ← qy correctly driven by sy
qz = cp * cy * sr - sp * sy * cr
```

For yaw=90°: `qy = 1·sy·1 + 0·... = 0.7071` ✓ matches V₃ table.

## Anti-circular value of this finding

This is **the first real test of the BFT N=3 architecture**: Howard's
worry "如果俩都有问题会一模一样" is empirically refuted because:

1. V₁ (Claude same-source) and V₂ (MiniMax independent LLM) **disagreed**,
   demonstrating LLM-source-independence is real
2. V₃ (zero-LLM textbook table) confirmed V₁ correct, **promoting V₂ to
   confirmed Byzantine**
3. The bug would have been undetectable in single-verifier (pre-v0.21)
   architecture — V₂ alone would have rejected valid data; V₁ alone would
   have rubber-stamped without independent check

## Repair plan

1. Dispatch MiniMax via opencode CLI with isolated workdir
   (independence preserved); input = this bug report + Hamilton formula reminder
2. Replace `bin/v2_minimax_residuals/residuals.py:r02_euler_quat_consistency`
3. Re-run BFT matrix; expect R02 pattern to flip from `PF-` to `PP-` (or `PPP`
   on table-covered frames)
4. Tag `recorder-v0.21.0-bft-prepare` once V₂ R02 fixed and matrix shows
   ≥ 95% unanimous PASS

## Open questions for Howard

- Q1: Should MinMax repair also adjust R03 (kinematics sign) and R04 (mouse_dx
  time index) per the same investigation? Both showed `PF` patterns in earlier
  R02-focused dry-run.
- Q2: For continuous ramp angles outside V₃'s 8-row table, should V₃ expand
  to 1°-step single-axis (1080 rows) or stay ABSTAIN-strict?
