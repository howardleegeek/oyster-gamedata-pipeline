# D9 — Cross-machine bundle comparator

Implement `bin/bundle_compare.py bundle_a.tar.gz bundle_b.tar.gz`. Cross-
checks two bundles produced by separate machines (mac-1 vs minipc) for
schema-level agreement: same fields, same value types, same row/frame
counts. Flags drift between cluster nodes.

Pure stdlib + openpyxl. Tests: identical bundles → match, mismatched → diff.
