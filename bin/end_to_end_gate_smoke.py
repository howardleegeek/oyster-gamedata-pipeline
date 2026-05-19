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

# Gates that MUST hard-PASS for a buyer-deliverable session.
STRICT_BUYER_REQUIRED = {
    "H8_depth_source",
    "S1_sync_tolerance",
    "V1_video_quality",
    "V2_video_artifacts",
    "B2_provenance",
}


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
            if status in (
                "PASS",
                "PASS_OK",
                "FAIL",
                "SKIP",
                "SKIP_honest",
                "PASS_DEGRADED",
            ):
                return {"status": status, "evidence": evidence}
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback: treat as ERROR
        stderr_snippet = (result.stderr or "").strip()[:200]
        return {
            "status": "ERROR",
            "evidence": f"exit code {result.returncode}: {stderr_snippet}",
        }

    # Zero exit → parse JSON
    try:
        data = json.loads(result.stdout.strip())
        return {
            "status": data.get("status", "PASS"),
            "evidence": data.get("evidence", "ok"),
        }
    except (json.JSONDecodeError, ValueError):
        return {
            "status": "ERROR",
            "evidence": "JSON parse error: gate returned non-JSON on stdout",
        }


def _run_b2_provenance(session_dir: str, skip_sign: bool = False) -> dict:
    """
    Run B2 provenance sign + verify round-trip.

    Returns:
        {"status": str, "evidence": str}
    """
    if skip_sign:
        return {"status": "SKIP", "evidence": "--skip-sign requested"}

    bin = _bin_dir()
    sign_script = bin / "provenance_sign.py"
    verify_script = bin / "provenance_verify.py"

    if not sign_script.exists() or not verify_script.exists():
        return {"status": "SKIP", "evidence": "sign/verify scripts not found"}

    # --- Sign ---
    manifest_path = os.path.join(session_dir, "MANIFEST.json")
    if not os.path.exists(manifest_path):
        return {"status": "FAIL", "evidence": "MANIFEST.json missing"}

    with tempfile.TemporaryDirectory() as tmpdir:
        sig_path = os.path.join(tmpdir, "manifest.sig")
        pub_path = os.path.join(tmpdir, "pub.pem")

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


# ---------------------------------------------------------------------------
# Evidence provenance detection (S06)
# ---------------------------------------------------------------------------


def _detect_h8_real(session_dir: Path) -> bool:
    """
    Rule 1: H8 marker has kind: engine_zbuffer AND EXR file total size > 1MB.
    """
    marker = session_dir / "depth" / ".source"
    if not marker.exists():
        return False

    try:
        text = marker.read_text()
        data = json.loads(text)
        kind = data.get("kind", "")
        if kind != "engine_zbuffer":
            return False
    except (json.JSONDecodeError, OSError):
        return False

    # Sum EXR file sizes in depth/
    depth_dir = session_dir / "depth"
    if not depth_dir.is_dir():
        return False

    total_exr_bytes = 0
    for f in depth_dir.iterdir():
        if f.suffix.lower() == ".exr":
            try:
                total_exr_bytes += f.stat().st_size
            except OSError:
                pass

    return total_exr_bytes > 1_000_000  # > 1MB


def _detect_video_non_integer_duration(session_dir: Path) -> bool:
    """
    Rule 2: Video file ffprobe duration is non-integer (synthetic are integer seconds).
    """
    recording = session_dir / "recording.mp4"
    if not recording.exists():
        return False

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(recording),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        duration_str = data.get("format", {}).get("duration", "")
        if not duration_str:
            return False
        duration = float(duration_str)
        # Non-integer duration → real
        return duration != int(duration)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError, ValueError):
        return False


def _detect_evidence_provenance(session_dir: str) -> str:
    """
    Determine evidence provenance for a session directory.

    Returns one of: "real", "synthetic", "unknown"

    Rules (by priority):
    1. H8 marker kind=engine_zbuffer + EXR total > 1MB → real
    2. Video ffprobe duration non-integer → real
    3. session_dir path contains "OysterClips/finalized/" → real
    4. session_dir path contains "tests/fixtures/" or "/tmp/" → synthetic
    5. Other → unknown (treated as synthetic)
    """
    session_path = Path(session_dir).resolve()
    session_str = str(session_path)

    # Rule 1: H8 engine_zbuffer + EXR > 1MB
    if _detect_h8_real(session_path):
        return "real"

    # Rule 2: Video non-integer duration
    if _detect_video_non_integer_duration(session_path):
        return "real"

    # Rule 3: OysterClips/finalized/ in path
    if "OysterClips/finalized/" in session_str:
        return "real"

    # Rule 4: tests/fixtures/ or /tmp/ in path → synthetic
    if "tests/fixtures/" in session_str or "/tmp/" in session_str:
        return "synthetic"

    # Rule 5: unknown → treated as synthetic
    return "unknown"


# ---------------------------------------------------------------------------
# Verdict computation
# ---------------------------------------------------------------------------


def _compute_verdict(gates_result: dict, strict_buyer: bool = False) -> dict:
    """
    Compute overall verdict from individual gate results.

    Default (demo) rules:
      - any FAIL → FAIL
      - any ERROR → FAIL (with crash note)
      - any PASS_DEGRADED → PASS_DEGRADED (unless FAIL/ERROR above)
      - all PASS/SKIP → PASS

    --strict-buyer rules (v0.4.1, Howard PM review 2026-05-18):
      In strict-buyer mode, the gates listed in `STRICT_BUYER_REQUIRED` MUST
      return PASS (or PASS_OK). SKIP / SKIP_honest / PASS_DEGRADED on those
      gates → FAIL. This prevents a session from looking PASS to a buyer when
      e.g. H8 depth source SKIPs as monocular_da_v2 fallback. Iron law: SKIP
      must not silently let a session ship in production.

    --strict-buyer evidence provenance (S06):
      When strict_buyer is True, the caller must also pass evidence_provenance.
      Three-tier verdict:
        - BUYER_READY: all strict gates PASS/PASS_OK AND evidence is real → exit 0
        - STRICT_GATES_PASS_SYNTHETIC: all strict gates PASS/PASS_OK but evidence
          synthetic/unknown → exit 2
        - STRICT_VIOLATIONS: any strict gate FAIL/SKIP/ERROR → exit 1
    """
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


def _compute_strict_buyer_verdict(gates_result: dict, evidence_provenance: str) -> dict:
    """
    Compute the three-tier strict-buyer verdict.

    Returns dict with:
      - verdict: "BUYER_READY" | "STRICT_GATES_PASS_SYNTHETIC" | "STRICT_VIOLATIONS"
      - exit_code: 0 | 2 | 1
      - evidence_provenance: "real" | "synthetic" | "unknown"
    """
    # Check for any strict gate violations (FAIL, SKIP, ERROR, PASS_DEGRADED)
    strict_violations = []
    for gate_id, g in gates_result.items():
        if gate_id in STRICT_BUYER_REQUIRED:
            if g["status"] not in ("PASS", "PASS_OK"):
                strict_violations.append(
                    f"{gate_id}={g['status']} (strict-buyer requires PASS)"
                )

    if strict_violations:
        return {
            "verdict": "STRICT_VIOLATIONS",
            "exit_code": 1,
            "evidence_provenance": evidence_provenance,
            "strict_violations": strict_violations,
        }

    # All strict gates PASS/PASS_OK — check evidence provenance
    if evidence_provenance == "real":
        return {
            "verdict": "BUYER_READY",
            "exit_code": 0,
            "evidence_provenance": evidence_provenance,
        }
    else:
        # synthetic or unknown → treated as synthetic
        return {
            "verdict": "STRICT_GATES_PASS_SYNTHETIC",
            "exit_code": 2,
            "evidence_provenance": evidence_provenance,
        }


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
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable table",
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
        print(
            f"ERROR: session directory does not exist: {session_dir}", file=sys.stderr
        )
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

    # Detect evidence provenance (S06)
    evidence_provenance = _detect_evidence_provenance(session_dir)

    if args.strict_buyer:
        # Three-tier strict-buyer verdict
        sb_verdict = _compute_strict_buyer_verdict(gates_result, evidence_provenance)
        summary = _compute_verdict(gates_result, strict_buyer=True)
        summary["session_id"] = session_id
        summary["strict_buyer_verdict"] = sb_verdict["verdict"]
        summary["evidence_provenance"] = sb_verdict["evidence_provenance"]
        summary["exit_code"] = sb_verdict["exit_code"]

        if args.json:
            output = {
                "session_id": session_id,
                "gates": gates_result,
                "summary": {
                    "pass": summary["pass"],
                    "fail": summary["fail"],
                    "skip": summary["skip"],
                    "verdict": summary["verdict"],
                    "strict_buyer_verdict": sb_verdict["verdict"],
                    "evidence_provenance": sb_verdict["evidence_provenance"],
                },
            }
            print(json.dumps(output, indent=2))
        else:
            print(_format_table(gates_result, summary))

        sys.exit(sb_verdict["exit_code"])
    else:
        # Standard (demo) mode
        summary = _compute_verdict(gates_result, strict_buyer=False)
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
