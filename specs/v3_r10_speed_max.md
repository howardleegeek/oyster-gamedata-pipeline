# V₃ Physics-Oracle: R10 speed_max

## Goal
Implement `r10_speed_max(rec) -> OracleResult` in `bin/v3_physics_oracle/r10_speed_max.py`.

Hard upper-bound check on `|speed|` (Euclidean magnitude of the per-frame
3-vector velocity in m/s). This is V₃'s ZERO-LLM physics oracle — values come
from publicly-verifiable game-engine constants, not LLM derivation.

## Contract (must match exactly)

```python
from bin.v3_physics_oracle.residuals import OracleResult, Verdict

def r10_speed_max(rec: dict) -> OracleResult:
    """Validate frame's speed magnitude is within physical limits.

    Args:
        rec: per-frame record. Expected key 'speed' = list[float] length 3.

    Returns OracleResult(name='r10_speed_max', verdict=..., expected=..., actual=..., residual=..., note=...)
    """
```

## Hardcoded physics constants (DO NOT change without IL3 independence proof)

```python
# Minecraft vanilla movement upper bounds (seconds = m, since 1 block = 1m)
WALK_SPEED         = 4.317  # m/s
SPRINT_SPEED       = 5.612  # m/s
SPRINT_JUMP        = 7.127  # m/s  (sprinting + bunny-hop measured)
HORSE_MAX          = 14.23  # m/s  (max-stat horse galloping)
NORMAL_MOVE_CEIL   = 20.0   # m/s  above this = ABSTAIN (elytra/rocket/horse-with-mod territory)
ABSOLUTE_CEIL      = 50.0   # m/s  HARD ceiling — anything above is teleport/cheat/encoding error
```

## Verdict rules

1. Missing `speed` key OR not list OR not length 3 OR non-numeric  → **ABSTAIN**
   (IL10: artifact-absent must surface as ABSTAIN, never silent PASS)
2. `magnitude > ABSOLUTE_CEIL`                                      → **FAIL**
3. `NORMAL_MOVE_CEIL < magnitude <= ABSOLUTE_CEIL`                  → **ABSTAIN**
   (could be elytra+rocket / horse / cheat-mod; V₃ can't disambiguate without state)
4. Otherwise                                                        → **PASS**

`residual` field:
- PASS  → 0.0
- FAIL  → magnitude − ABSOLUTE_CEIL
- ABSTAIN → math.nan

`expected` field: ABSOLUTE_CEIL (50.0)
`actual`   field: the computed magnitude (or `None` if ABSTAIN-on-missing)

## Tests (must pass `pytest -q`)

Create `tests/test_r10_speed_max.py`:

```python
import math
import pytest
from bin.v3_physics_oracle.r10_speed_max import r10_speed_max
from bin.v3_physics_oracle.residuals import Verdict

def test_walking_pass():
    r = r10_speed_max({"speed": [4.0, 0.0, 0.0]})
    assert r.verdict == Verdict.PASS
    assert r.residual == 0.0

def test_pythagorean_345_pass():
    # 3-4-5 triangle: magnitude exactly 5
    r = r10_speed_max({"speed": [3.0, 4.0, 0.0]})
    assert r.verdict == Verdict.PASS

def test_sprint_jump_pass():
    r = r10_speed_max({"speed": [7.0, 0.0, 0.0]})
    assert r.verdict == Verdict.PASS

def test_elytra_abstain():
    # 25 m/s = clearly elytra territory; V3 can't disambiguate
    r = r10_speed_max({"speed": [25.0, 0.0, 0.0]})
    assert r.verdict == Verdict.ABSTAIN
    assert math.isnan(r.residual)

def test_teleport_fail():
    # 100 m/s = no vanilla mechanic produces this
    r = r10_speed_max({"speed": [100.0, 0.0, 0.0]})
    assert r.verdict == Verdict.FAIL
    assert r.residual == pytest.approx(100.0 - 50.0)

def test_missing_field_abstain():
    r = r10_speed_max({})
    assert r.verdict == Verdict.ABSTAIN

def test_wrong_length_abstain():
    r = r10_speed_max({"speed": [1.0, 2.0]})
    assert r.verdict == Verdict.ABSTAIN

def test_non_numeric_abstain():
    r = r10_speed_max({"speed": [1.0, "fast", 0.0]})
    assert r.verdict == Verdict.ABSTAIN

def test_negative_components_pass():
    # magnitude is direction-independent
    r = r10_speed_max({"speed": [-3.0, -4.0, 0.0]})
    assert r.verdict == Verdict.PASS

def test_ceiling_boundary_fail():
    # exactly at ceiling = abstain (rule 3 covers it)
    r = r10_speed_max({"speed": [50.001, 0.0, 0.0]})
    assert r.verdict == Verdict.FAIL
```

## Constraints

- Pure Python stdlib only (`math`, no numpy)
- No imports from `bin.v1_*`, `bin.v2_*`, `bin.v2prime_*` (BFT IL3: V₃ stays independent)
- No reading from disk, no network calls
- Function must be deterministic and side-effect free
- Must handle `None`, missing keys, type mismatches gracefully (return ABSTAIN)
- Must NOT crash on any malformed input

## Validation
- [ ] `python3 -m py_compile bin/v3_physics_oracle/r10_speed_max.py`
- [ ] `python3 -m pytest -q tests/test_r10_speed_max.py`
- [ ] All 10 tests pass

## Don't do
- Don't modify `bin/v3_physics_oracle/residuals.py`
- Don't add a CLI / main block
- Don't add logging
- Don't import from V₁/V₂/V₂'
