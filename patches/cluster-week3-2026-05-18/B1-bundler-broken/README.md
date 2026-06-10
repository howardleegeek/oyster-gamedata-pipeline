# B1 batch_bundler — partial / broken cluster output

Dispatched 2026-05-18 09:48 PT, deepseek-v3.2, 11 turns.

## Status
**Partial**: 3 of 7 pytest cases pass, 4 fail. Not landed in main tree.

## Failing tests

```
FAILED test_build_merkle_tree       — Merkle root mismatch for single-leaf case
                                       (cluster impl ≠ test spec: pad-with-zero-hash)
FAILED test_process_session         — assertion failure (downstream of merkle)
FAILED test_integration             — FileNotFoundError (likely tarball arcname issue)
FAILED test_cli_error_cases         — stderr/stdout assertion mismatch
```

## Root cause hypothesis

Cluster's Merkle implementation pads to power-of-2 differently than test spec.
Test expects: `single_hash → sha256(hash || sha256(b''))`.
Cluster implements: `single_hash → hash itself` (no padding when leaves==1).

Either the spec or the impl needs to align. Test is more spec-faithful.

## Next action

Re-dispatch as **B1v2** with the failure log + this README as context.
SPEC.md already exists in this dir; B1v2 SPEC will reference the bugs explicitly.

Alternatively: a 20-minute manual fix in the impl would suffice. Quicker than
re-dispatching. Howard's call.
