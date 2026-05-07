---
task_id: D21
project: oyster-gamedata-pipeline
priority: 3
estimated_minutes: 30
depends_on: []
modifies:
  - bin/spec_lint.py  (new)
  - tests/test_spec_lint.py  (new)
  - .github/workflows/ci.yml  (add spec-lint step)
must_not_touch:
  - existing specs (D1-D20) — they conform AFTER this lints, refactors come later
executor: glm
iron_law: REAL ONLY — lint MUST reject specs with placeholder/mock/TODO unless explicitly waived
iron_law_waived: spec body lists banned patterns as the linter regex set (definitional self-reference)
---

# D21: spec_lint.py — enforce spec template + REAL-only criterion

## 目标

Specs in `specs/` directory have grown to 21+ entries. Half don't have
acceptance criteria; some say "TODO: implement later". Build a linter
that:

1. Validates the YAML front-matter (task_id, project, modifies, executor)
2. Asserts the spec body has at least 3 acceptance criteria (`- [ ]` items)
3. **Iron-law check**: rejects any spec body containing `placeholder`,
   `mock`, `stub`, `fake`, `TODO`, `FIXME` outside of explicit "不要做"
   sections OR ones tagged `iron_law_waived: <reason>` in front-matter
4. Asserts every spec lists `must_not_touch` so executors don't sprawl

Run as a CI gate; refusing to merge specs that violate.

## 验收标准 (REAL ONLY)

- [ ] `bin/spec_lint.py` is a stand-alone Python 3.11+ script (stdlib
      only, no external deps)
- [ ] `python bin/spec_lint.py specs/` exit codes:
      - 0 = all specs pass
      - 1 = one or more specs fail; prints failure list with file + line
      - 2 = lint script bug (e.g. malformed YAML it can't parse)
- [ ] Validates YAML front-matter required keys: task_id, project,
      priority, estimated_minutes, modifies (list), executor
- [ ] Asserts ≥ 3 acceptance criteria (lines matching `^- \[ \]`)
- [ ] Iron-law regex: rejects `placeholder|mock|stub|fake|TODO|FIXME`
      in spec body (exclude lines under `## 不要做` heading and
      front-matter `iron_law_waived` waivers)
- [ ] `tests/test_spec_lint.py` has ≥ 6 cases:
      - valid spec passes
      - missing front-matter fails
      - missing acceptance criteria fails
      - placeholder in body fails
      - placeholder in 不要做 section passes (it's the BANNED list)
      - waived spec passes
- [ ] CI step in `.github/workflows/ci.yml` runs `python bin/spec_lint.py
      specs/` on every push, fails the build on exit ≠ 0
- [ ] Run on existing D1-D21 specs: ≥ 80% pass on first try (any failures
      fixable in same PR, document in PR description)

## REAL artifact criterion

- Lint script MUST run end-to-end on real spec files. No `--dry-run`
  fakery.
- Pass percentage on existing specs reported in CI summary.

## 不要做

- 不要 auto-fix specs (linter only — humans/cluster fix violations)
- 不要 add a YAML spec format dependency (stdlib only)
- 不要 deprecate specs that fail (just FAIL, list them, let the cluster
  spec-by-spec fix in follow-up tasks)
