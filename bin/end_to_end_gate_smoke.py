#!/usr/bin/env python3
"""
END-TO-END GATE SMOKE — orchestrator

Calls all standalone gate CLIs via subprocess, aggregates results into a
unified JSON report + human-readable summary table.

Usage:
    python3 bin/end_to_end_gate_smoke.py <session_dir> [--json] [--skip-sign]

Any gate FAIL → overall FAIL.
Any gate ERROR (crash) → overall FAIL with crash note.
SKIP does NOT count as FAIL.
PASS_DEGRADED → overall PASS_DEGRADED (unless any FAIL/ERROR).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Gate definitions — each maps to a CLI entry-point under bin/
# ---------------------------------------------------------------------------
GATES = [
    {
        "key": "H8_depth_source",
        "label": "H8 depth source",
        "script": "prd_compliance_audit_H8_patch.py",
    },
    {
        "key": "S1_sync_tolerance",
        "label": "S1 sync tolerance",
        "script": "sync_tolerance_gate.py",
    },
    {
        "key": "S2_input_latency",
        "label": "S2 input latency",
        "script": "input_latency_analyzer.py",
    },
    {
        "key": "V1_video_quality",
        "label": "V1 video quality",
        "script": "video_quality_gate.py",
    },
    {
        "key": "V2_video_artifacts",
        "label": "V2 video artifacts",
        "script": "video_artifact_scanner.py",
    },
    {
        "key": "B2_provenance",
        "label": "B2 provenance",
        "script": None,  # special handling
    },
]

GATE_TIMEOUT = 120  # seconds per gate


def _bin_dir() -> Path:
    """Return the directory that contains this script (bin/)."""
    return Path(__file__).resolve().parent


def _run_gate(script_name: str, session_dir: str) -> dict:
    """
    Run a single gate CLI via subprocess and return parsed result dict.

    Returns:
        {"status": str, "evidence": str}
    """
    script_path = _bin_dir() / script_name
    cmd = [sys.executable, str(script_path), session_dir, "--json"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "ERROR",
            "evidence": f"timed out after {GATE_TIMEOUT}s",
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "evidence": f"subprocess exception: {exc}",
        }

    # Non-zero exit → try to parse JSON from stdout; if that fails, ERROR
    if result.returncode != 0:
        # Still try to parse JSON in case the gate wrote partial output
        try:
            data = json.loads(result.stdout.strip())
            status = data.get("status", "ERROR")
            evidence = data.get("evidence", f"exit code {result.returncode}")
            # If the gate itself reported a status, honour it
            if status in ("PASS", "FAIL", "SKIP", "PASS_OK", "PASS_DEGRADED"):
                return {"status": status, "evidence": evidence}
        except (json.JSONDecodeError, ValueError):
            pass
        stderr_snippet = (result.stderr or "").strip()
        if len(stderr_snippet) > 200:
            stderr_snippet = stderr_snippet[:200] + "…"
        return {
            "status": "ERROR",
            "evidence": f"exit code {result.returncode}"
            + (f" — {stderr_snippet}" if stderr_snippet else ""),
        }

    # Parse JSON output
    try:
        data = json.loads(result.stdout.strip())
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "ERROR",
            "evidence": f"JSON parse error: {exc}",
        }

    status = data.get("status", "ERROR")
    evidence = data.get("evidence", json.dumps(data))
    return {"status": status, "evidence": evidence}


def _run_b2_provenance(session_dir: str, skip_sign: bool) -> dict:
    """
    B2 provenance gate: sign + verify round-trip using ed25519.

    Creates a synthetic manifest, signs it, then verifies the signature.
    If --skip-sign is given, returns SKIP.
    """
    if skip_sign:
        return {"status": "SKIP", "evidence": "--skip-sign requested"}

    sign_script = _bin_dir() / "provenance_sign.py"
    verify_script = _bin_dir() / "provenance_verify.py"

    # Check both scripts exist
    if not sign_script.exists() or not verify_script.exists():
        return {
            "status": "SKIP",
            "evidence": "provenance_sign.py / provenance_verify.py not found",
        }

    # Create synthetic manifest
    manifest = {
        "batch_id": f"smoke-{int(time.time())}",
        "merkle_root": "0" * 64,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = os.path.join(tmpdir, "manifest.json")
        sig_path = os.path.join(tmpdir, "manifest.sig")
        pub_path = os.path.join(tmpdir, "key.pub")

        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        # --- Sign ---
        try:
            sign_result = subprocess.run(
                [
                    sys.executable,
                    str(sign_script),
                    manifest_path,
                    "--sig-out",
                    sig_path,
                    "--pub-out",
                    pub_path,
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=GATE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return {"status": "ERROR", "evidence": "sign timed out"}
        except Exception as exc:
            return {"status": "ERROR", "evidence": f"sign exception: {exc}"}

        if sign_result.returncode != 0:
            stderr_snippet = (sign_result.stderr or "").strip()[:200]
            return {
                "status": "ERROR",
                "evidence": f"sign failed (exit {sign_result.returncode}): {stderr_snippet}",
            }

        # --- Verify ---
        try:
            verify_result = subprocess.run(
                [
                    sys.executable,
                    str(verify_script),
                    manifest_path,
                    "--sig-in",
                    sig_path,
                    "--pub-in",
                    pub_path,
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=GATE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return {"status": "ERROR", "evidence": "verify timed out"}
        except Exception as exc:
            return {"status": "ERROR", "evidence": f"verify exception: {exc}"}

        if verify_result.returncode != 0:
            stderr_snippet = (verify_result.stderr or "").strip()[:200]
            return {
                "status": "ERROR",
                "evidence": f"verify failed (exit {verify_result.returncode}): {stderr_snippet}",
            }

        return {"status": "PASS", "evidence": "sign + verify round-trip OK"}


def _compute_verdict(gates_result: dict, strict_buyer: bool = False) -> dict:
    """
    Compute overall verdict from individual gate results.

    Default (demo) rules:
      - any FAIL → FAIL
      - any ERROR → FAIL (with crash note)
      - any PASS_DEGRADED → PASS_DEGRADED (unless FAIL/ERROR above)
      - all PASS/SKIP → PASS
    SKIP is treated as "no signal" — fine for demos.

    --strict-buyer rules (v0.4.1, Howard PM review 2026-05-18):
      In strict-buyer mode, the gates listed in `STRICT_BUYER_REQUIRED` MUST
      return PASS (or PASS_OK). SKIP / SKIP_honest / PASS_DEGRADED on those
      gates → FAIL. This prevents a session from looking PASS to a buyer when
      e.g. H8 depth source SKIPs as monocular_da_v2 fallback. Iron law: SKIP
      must not silently let a session ship in production.
    """
    # Gates that MUST hard-PASS for a buyer-deliverable session. SKIP on any
    # of these in strict-buyer mode = production block.
    STRICT_BUYER_REQUIRED = {
        "H8_depth_source",
        "S1_sync_tolerance",
        "V1_video_quality",
        "V2_video_artifacts",
        "B2_provenance",
    }

    statuses = [g["status"] for g in gates_result.values()]

    pass_count = sum(1 for s in statuses if s == "PASS")
    fail_count = sum(1 for s in statuses if s == "FAIL")
    skip_count = sum(1 for s in statuses if s in ("SKIP", "SKIP_honest"))
    error_count = sum(1 for s in statuses if s == "ERROR")
    degraded_count = sum(1 for s in statuses if s == "PASS_DEGRADED")
    pass_ok_count = sum(1 for s in statuses if s == "PASS_OK")

    # Count PASS_OK as pass for the summary
    effective_pass = pass_count + pass_ok_count

    # Strict-buyer mode: convert SKIP/PASS_DEGRADED on required gates to FAIL
    strict_violations = []
    if strict_buyer:
        for gate_id, g in gates_result.items():
            if gate_id in STRICT_BUYER_REQUIRED:
                if g["status"] not in ("PASS", "PASS_OK"):
                    strict_violations.append(
                        f"{gate_id}={g['status']} (strict-buyer requires PASS)"
                    )

    if fail_count > 0 or error_count > 0:
        verdict = "FAIL"
    elif strict_violations:
        verdict = "FAIL"
    elif degraded_count > 0:
        verdict = "PASS_DEGRADED"
    else:
        verdict = "PASS"

    result = {
        "pass": effective_pass,
        "fail": fail_count,
        "skip": skip_count,
        "verdict": verdict,
        "strict_buyer": bool(strict_buyer),
    }
    if strict_violations:
        result["strict_violations"] = strict_violations
    return result


def _format_table(gates_result: dict, summary: dict) -> str:
    """Format human-readable summary table."""
    lines = []
    lines.append(f"END-TO-END GATE SMOKE — {summary.get('session_id', 'unknown')}")
    lines.append("")

    # Column widths
    col1_w = 22  # Gate name
    col2_w = 14  # Status
    col3_w = 50  # Evidence

    header = f"  {'Gate':<{col1_w}}  {'Status':<{col2_w}}  {'Evidence':<{col3_w}}"
    sep = f"  {'─' * col1_w}  {'─' * col2_w}  {'─' * col3_w}"

    lines.append(header)
    lines.append(sep)

    for gate_def in GATES:
        key = gate_def["key"]
        label = gate_def["label"]
        result = gates_result.get(key, {"status": "SKIP", "evidence": "not executed"})
        status = result["status"]
        evidence = result["evidence"]

        # Truncate evidence if too long
        if len(evidence) > col3_w:
            evidence = evidence[: col3_w - 3] + "…"

        lines.append(f"  {label:<{col1_w}}  {status:<{col2_w}}  {evidence:<{col3_w}}")

    lines.append("")
    s = summary
    verdict_line = (
        f"  Overall verdict: {s['verdict']}  "
        f"({s['pass']} PASS / {s['fail']} FAIL / {s['skip']} SKIP)"
    )
    lines.append(verdict_line)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end gate smoke test — runs all gates against a session dir"
    )
    parser.add_argument("session_dir", help="Path to session directory")
    parser.add_argument(
        "--json", action="store_true", help="Output JSON instead of human-readable table"
    )
    parser.add_argument(
        "--skip-sign",
        action="store_true",
        help="Skip B2 provenance sign/verify round-trip",
    )
    parser.add_argument(
        "--strict-buyer",
        action="store_true",
        help=(
            "v0.4.1: BLOCK on SKIP/PASS_DEGRADED for H8/S1/V1/V2/B2 gates. "
            "Required for production buyer deliverables. Without this flag, "
            "the gate is in DEMO mode and SKIP is permitted (e.g. H8 monocular "
            "fallback won't block but also won't ship as production data)."
        ),
    )
    args = parser.parse_args()

    session_dir = args.session_dir
    if not os.path.isdir(session_dir):
        print(f"ERROR: session directory does not exist: {session_dir}", file=sys.stderr)
        sys.exit(1)

    # Derive session_id from directory name
    session_id = os.path.basename(os.path.abspath(session_dir))

    gates_result = {}

    for gate_def in GATES:
        key = gate_def["key"]
        script = gate_def["script"]

        if script is None:
            # B2 provenance — special handling
            gates_result[key] = _run_b2_provenance(session_dir, args.skip_sign)
        else:
            gates_result[key] = _run_gate(script, session_dir)

    summary = _compute_verdict(gates_result, strict_buyer=args.strict_buyer)
    summary["session_id"] = session_id

    if args.json:
        output = {
            "session_id": session_id,
            "gates": gates_result,
            "summary": {
                "pass": summary["pass"],
                "fail": summary["fail"],
                "skip": summary["skip"],
                "verdict": summary["verdict"],
            },
        }
        print(json.dumps(output, indent=2))
    else:
        print(_format_table(gates_result, summary))

    # Exit code: 0 for PASS/PASS_DEGRADED, 1 for FAIL
    if summary["verdict"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
