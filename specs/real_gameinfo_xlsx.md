# Real gameinfo.xlsx — replace OOXML stub with real generator

## Goal
Replace the placeholder gameinfo.xlsx writer in
`bin/recorder_consumer_lite.py:~1771-1793` (currently emits an OOXML
sheet with the literal cell value `"placeholder - stop-gap recorder"`)
with a real PRD-conforming generator.

Output file: `bin/recorder_gameinfo_real.py` providing:

```python
def write_gameinfo_xlsx(target_path: Path, *, recording_meta: dict,
                       per_frame_records: list[dict]) -> None:
    """Write a buyer-spec-conforming gameinfo.xlsx.

    Args:
        target_path: where to write the .xlsx
        recording_meta: session-level metadata (game, version, mod list,
                        seed, world type, recording_resolution, etc.)
        per_frame_records: same list passed to action_camera writer
                           (used to compute per-second aggregates).

    Raises:
        ValueError: if any required field is missing — NEVER silently
                    fill placeholders.
    """
```

## Hard requirements

1. Use `openpyxl` (already in `pyproject.toml [project.optional-dependencies] xlsx`).
2. The xlsx must conform to `docs/BUYER_SPEC_V1.md` gameinfo schema
   (see "## Acceptance gates → gameinfo gate" section). Read that file
   in the repo to extract the exact column names and order.
3. NO placeholder cells. NO "TBD" cells. NO empty cells unless the spec
   explicitly allows null for that field.
4. If a required input field is missing from `recording_meta` or
   `per_frame_records`, raise `ValueError` with the specific missing
   field name. NEVER fill a default.
5. Multi-sheet support: if PRD specifies multiple sheets (Session /
   Frames / Events), implement all of them.

## Constraints

- Pure Python via `openpyxl`. No `subprocess` / no `xlsxwriter`.
- Must work on Windows + macOS + Linux.
- File size must stay under 5 MB for a typical 6-min recording (PRD).

## Acceptance

- [ ] `bin/recorder_gameinfo_real.py` created.
- [ ] `bin/recorder_consumer_lite.py` imports + calls it; the OOXML
  stub at lines ~1700-1771 is DELETED.
- [ ] Unit test `tests/test_recorder_gameinfo_real.py`:
  - happy path: full recording_meta + 60s of frames → valid .xlsx
  - missing field: raises ValueError with specific field name
  - opens-in-excel test: file is parseable by openpyxl reload
  - schema test: column headers exactly match BUYER_SPEC_V1.md
- [ ] `python3 -m py_compile` clean for both files.

## Don't do

- Don't accept partial input and fill with empty string. Raise.
- Don't write the literal string "placeholder" anywhere.
- Don't keep the hand-rolled OOXML stub as a fallback.
