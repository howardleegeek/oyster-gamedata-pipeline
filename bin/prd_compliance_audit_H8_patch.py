"""H8 compliance audit patch — shim around grafted main-audit implementation.

v0.4.1 WIRE03 (2026-05-19): the H8 evaluation logic now lives in
`bin/prd_compliance_audit.py` as `_evaluate_h8`. This file remains as a
backward-compat shim — anything that imports `evaluate_h8` from here
still works.

Recommended new code:
    from bin.prd_compliance_audit import _evaluate_h8 as evaluate_h8
"""

from __future__ import annotations

# Import works either as `bin.prd_compliance_audit_H8_patch` (when repo root
# is on sys.path) or as bare `prd_compliance_audit_H8_patch` (when bin/ is
# on sys.path — e.g. when zbuffer_pipeline_smoke.py runs as a script).
try:
    from bin.prd_compliance_audit import _evaluate_h8 as evaluate_h8  # noqa: F401
except ImportError:
    from prd_compliance_audit import _evaluate_h8 as evaluate_h8  # noqa: F401

__all__ = ["evaluate_h8"]
