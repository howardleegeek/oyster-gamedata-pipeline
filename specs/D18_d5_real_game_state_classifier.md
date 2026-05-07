---
task_id: D18
project: oyster-gamedata-pipeline
priority: 2
estimated_minutes: 30
depends_on: [D5 authenticity validator, D15 client mod]
modifies:
  - bin/tarball_authenticity_check.py  (add real_game_state classifier branch)
  - tests/test_d5_real_game_state.py  (new)
must_not_touch:
  - other classifier branches (video, depth, gameinfo, systeminfo, README)
  - the verdict-aggregation logic
executor: glm
iron_law: REAL ONLY — classifier MUST distinguish placeholder from mod-driven real
iron_law_waived: spec body describes classifier branches that explicitly reference placeholder/mock as classification labels
---

# D18: D5 enhancement — detect real_game_state field in action_camera

## 目标

D5 today classifies action_camera as REAL when ≥10% of records are
non-padded with multi-field fingerprint variance. After D15+D17 land,
records will have a new `_real_game_state: true` field set by
`game_state_overlay.apply_to_record`. D5 must:

1. Recognise that flag and tier UP the verdict to "REAL (mod-driven)"
2. Tier DOWN to "PARTIAL" when records are non-padded but `_real_game_state`
   is absent — this catches the case where the mod ISN'T installed and
   action_camera is still using post-D15 metadata-derived placeholders

This stops 4/5 vs 5/6 vs 6/6 confusion in tester reports.

## 验收标准 (REAL ONLY)

- [ ] `tarball_authenticity_check.py:_classify_action_camera` adds new
      tier-1 check BEFORE existing tier-2 fingerprint check:
      ```
      if any(r.get("_real_game_state") is True for r in records):
          return REAL, "mod-driven real game state present in N/M records"
      ```
- [ ] When tier-1 misses but tier-2 hits: emit "PARTIAL" instead of "REAL"
      with message "metadata-derived but no mod overlay; install mc-mod
      for full fidelity"
- [ ] `tests/test_d5_real_game_state.py` covers 3 cases:
      1. action_camera with `_real_game_state=true` → REAL
      2. action_camera with no flag but multi-field fingerprint → PARTIAL
      3. action_camera with single fingerprint (stationary bot) →
         PLACEHOLDER (existing behaviour)
- [ ] Update `bin/tarball_authenticity_check.py` doc-string to mention
      the 3-tier classification
- [ ] D5 verdict on `oyster_REAL6_093357.tar.gz` (no mod, post-D15 cluster
      output) reports PARTIAL — confirming the flag works on existing data

## REAL artifact criterion

- No silent default-True for missing flag. Records WITHOUT the flag must
  go through tier-2/tier-3 honestly.
- `_real_game_state` is a boolean — accept `true`, reject `"true"` /
  `"yes"` / `1` to prevent type-confusion shipping placeholder as real.

## 不要做

- 不要 rename the existing `_classify_action_camera` interface (downstream
  callers depend on it)
- 不要 add a CLI flag — classifier auto-detects from record contents
