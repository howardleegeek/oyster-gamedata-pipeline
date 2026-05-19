#!/usr/bin/env python3
"""
REAL SESSION VALIDATOR — v0.4.1 prep
=====================================
Orchestrator for Howard + Bruno's real-session validation phase.

Discovers session directories, runs canonical_pipeline + all 9 G-gates +
provenance sign/verify, and aggregates pass/fail into a single report.

Pure Python stdlib only.
"""

import argparse
import csv
import datetime
import json
import pathlib
import subprocess
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
PIPELINE_SCRIPT = SCRIPT_DIR / "canonical_pipeline.py"
GATE_SCRIPT = SCRIPT_DIR / "end_to_end_gate_smoke.py"
SIGN_SCRIPT = SCRIPT_DIR / "provenance_sign.py"
VERIFY_SCRIPT = SCRIPT_DIR / "provenance_verify.py"

PIPELINE_TIMEOUT = 600  # seconds per session
GATE_TIMEOUT = 120
SIGN_TIMEOUT = 30
VERIFY_TIMEOUT = 30

REQUIRED_FILES = {"recording.mp4", "game_state.jsonl"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def discover_sessions(root: pathlib.Path) -> list[pathlib.Path]:
    """Walk one level deep under root, return dirs that contain required files."""
    sessions = []
    if not root.is_dir():
        return sessions
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            present = {f.name for f in entry.iterdir()} if entry.is_dir() else set()
            if REQUIRED_FILES.issubset(present):
                sessions.append(entry)
    return sessions


def run_pipeline(session_dir: pathlib.Path) -> dict:
    """Run canonical_pipeline.py --strict. Returns verdict dict."""
    try:
        result = subprocess.run(
            [sys.executable, str(PIPELINE_SCRIPT), str(session_dir), "--strict"],
            capture_output=True,
            text=True,
            timeout=PIPELINE_TIMEOUT,
        )
        exit_code = result.returncode
        return {
            "verdict": "PASS" if exit_code == 0 else "BLOCKED",
            "exit_code": exit_code,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"verdict": "BLOCKED", "exit_code": 1, "stdout": "", "stderr": "TIMEOUT"}
    except Exception as exc:
        return {"verdict": "BLOCKED", "exit_code": 1, "stdout": "", "stderr": str(exc)}


def run_gates(session_dir: pathlib.Path) -> dict:
    """Run end_to_end_gate_smoke.py --json. Returns verdict dict."""
    try:
        result = subprocess.run(
            [sys.executable, str(GATE_SCRIPT), str(session_dir), "--json"],
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT,
        )
        try:
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            data = {}

        overall = data.get("overall", data.get("verdict", "UNKNOWN"))
        per_gate = data.get("gates", data.get("per_gate", {}))
        passed_count = sum(1 for g in per_gate.values() if g.get("status") == "PASS" or g == "PASS")
        total_count = len(per_gate) if per_gate else 0

        return {
            "verdict": overall,
            "passed": passed_count,
            "total": total_count,
            "per_gate": per_gate,
            "raw": data,
        }
    except subprocess.TimeoutExpired:
        return {"verdict": "TIMEOUT", "passed": 0, "total": 0, "per_gate": {}, "raw": {}}
    except Exception as exc:
        return {
            "verdict": "ERROR",
            "passed": 0,
            "total": 0,
            "per_gate": {},
            "raw": {},
            "error": str(exc),
        }


def run_provenance(session_dir: pathlib.Path, keyfile: pathlib.Path) -> dict:
    """Sign then verify manifest. Returns verdict dict."""
    manifest_path = session_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return {"verdict": "SKIPPED", "reason": "MANIFEST.json not found"}

    # Sign
    signed_path = session_dir / "MANIFEST.signed.json"
    try:
        sign_result = subprocess.run(
            [sys.executable, str(SIGN_SCRIPT), str(manifest_path), "--keyfile", str(keyfile)],
            capture_output=True,
            text=True,
            timeout=SIGN_TIMEOUT,
        )
        if sign_result.returncode != 0:
            return {"verdict": "FAILED", "reason": f"sign failed: {sign_result.stderr.strip()}"}
    except subprocess.TimeoutExpired:
        return {"verdict": "FAILED", "reason": "sign TIMEOUT"}
    except Exception as exc:
        return {"verdict": "FAILED", "reason": f"sign error: {exc}"}

    # Verify
    try:
        verify_result = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), str(signed_path)],
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT,
        )
        if verify_result.returncode == 0:
            return {"verdict": "VERIFIED"}
        else:
            return {"verdict": "FAILED", "reason": f"verify failed: {verify_result.stderr.strip()}"}
    except subprocess.TimeoutExpired:
        return {"verdict": "FAILED", "reason": "verify TIMEOUT"}
    except Exception as exc:
        return {"verdict": "FAILED", "reason": f"verify error: {exc}"}


def compute_overall(pipeline: dict, gates: dict, provenance: dict) -> str:
    """Compute overall verdict for a session."""
    if pipeline["verdict"] == "BLOCKED":
        return "FAIL"
    if gates["verdict"] in ("FAIL", "BLOCKED", "TIMEOUT", "ERROR"):
        return "FAIL"
    if gates["verdict"] == "DEGRADED" or gates.get("passed", 0) < gates.get("total", 0):
        return "DEGRADED"
    if provenance["verdict"] == "FAILED":
        return "FAIL"
    if provenance["verdict"] == "SKIPPED":
        # Pipeline PASS + gates PASS + provenance skipped → DEGRADED
        return "DEGRADED"
    return "PASS"


def collect_failure_reasons(
    session_name: str, pipeline: dict, gates: dict, provenance: dict
) -> list[str]:
    """Collect human-readable failure reasons for a session."""
    reasons = []
    if pipeline["verdict"] == "BLOCKED":
        reasons.append("Pipeline BLOCKED")
    if gates.get("per_gate"):
        for gate_name, gate_info in gates["per_gate"].items():
            status = (
                gate_info.get("status", gate_info) if isinstance(gate_info, dict) else gate_info
            )
            if status != "PASS":
                reasons.append(f"{gate_name} failed")
    elif gates["verdict"] in ("FAIL", "BLOCKED", "TIMEOUT", "ERROR"):
        reasons.append(f"G-gates {gates['verdict']}")
    if provenance["verdict"] == "FAILED":
        reasons.append(f"Provenance {provenance['verdict']}")
    return reasons


def format_gate_summary(gates: dict) -> str:
    """Format gate results as '9/9 OK' or '8/9' etc."""
    if gates["verdict"] in ("TIMEOUT", "ERROR"):
        return gates["verdict"]
    passed = gates.get("passed", 0)
    total = gates.get("total", 0)
    if total == 0:
        return "n/a"
    if passed == total:
        return f"{passed}/{total} OK"
    return f"{passed}/{total}"


def format_provenance(provenance: dict) -> str:
    """Format provenance verdict."""
    v = provenance.get("verdict", "n/a")
    if v == "SKIPPED":
        return "SKIPPED (no --keyfile)"
    return v


def truncate_name(name: str, max_len: int = 36) -> str:
    """Truncate session name for table display."""
    if len(name) <= max_len:
        return name
    return name[: max_len - 3] + "..."


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_text_report(
    results: list[dict], sessions_root: str, limit: int, total_found: int
) -> str:
    """Render human-readable text report."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = []
    lines.append(f"REAL SESSION VALIDATOR — {now}")
    lines.append(f"  Scanning: {sessions_root}")
    lines.append(f"  Found: {total_found} session dirs")
    if limit and limit < total_found:
        lines.append(f"  Validating: first {limit} (--limit {limit})")
    lines.append("")

    # Table header
    col_w = 36
    lines.append(
        f"  {'Session':<{col_w}} | {'Pipeline':<8} | {'G-gates':<7} | {'Provenance':<10} | Overall"
    )
    lines.append(f"  {'-' * col_w} | {'-' * 8} | {'-' * 7} | {'-' * 10} | {'-' * 7}")

    for r in results:
        name = truncate_name(r["name"])
        pipeline_str = r["pipeline"]["verdict"]
        gate_str = format_gate_summary(r["gates"])
        prov_str = format_provenance(r["provenance"])
        overall = r["overall"]
        lines.append(
            f"  {name:<{col_w}} | {pipeline_str:<8} | {gate_str:<7} | {prov_str:<10} | {overall}"
        )

    lines.append("")

    # Summary
    total = len(results)
    pass_count = sum(1 for r in results if r["overall"] == "PASS")
    degraded_count = sum(1 for r in results if r["overall"] == "DEGRADED")
    fail_count = sum(1 for r in results if r["overall"] == "FAIL")

    lines.append("  Summary:")
    lines.append(f"    Sessions evaluated: {total}")
    lines.append(f"    Full PASS:          {pass_count} ({pass_count * 100 // max(total, 1)}%)")
    lines.append(
        f"    DEGRADED:           {degraded_count} ({degraded_count * 100 // max(total, 1)}%)"
    )
    lines.append(f"    FAIL:               {fail_count} ({fail_count * 100 // max(total, 1)}%)")
    lines.append("")

    # Failure breakdown
    all_reasons: dict[str, int] = {}
    for r in results:
        for reason in r.get("failure_reasons", []):
            all_reasons[reason] = all_reasons.get(reason, 0) + 1

    if all_reasons:
        lines.append("  Failures breakdown:")
        for reason, count in sorted(all_reasons.items(), key=lambda x: -x[1]):
            session_word = "session" if count == 1 else "sessions"
            lines.append(f"    {reason}:  {count} {session_word}")
        lines.append("")

    # Verdict
    pass_rate = pass_count * 100 // max(total, 1)
    if pass_rate >= 80:
        verdict_line = (
            f"  Verdict: BUYER-READY @ {pass_rate}% pass rate (target ≥80% for v0.5.0 launch)"
        )
    else:
        verdict_line = (
            f"  Verdict: NOT BUYER-READY @ {pass_rate}% pass rate (target ≥80% for v0.5.0 launch)"
        )
    lines.append(verdict_line)

    return "\n".join(lines)


def render_json_report(
    results: list[dict], sessions_root: str, limit: int, total_found: int
) -> str:
    """Render machine-readable JSON report."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = len(results)
    pass_count = sum(1 for r in results if r["overall"] == "PASS")
    degraded_count = sum(1 for r in results if r["overall"] == "DEGRADED")
    fail_count = sum(1 for r in results if r["overall"] == "FAIL")

    report = {
        "timestamp": now,
        "sessions_root": sessions_root,
        "total_found": total_found,
        "limit": limit,
        "evaluated": total,
        "summary": {
            "PASS": pass_count,
            "DEGRADED": degraded_count,
            "FAIL": fail_count,
            "pass_rate_pct": pass_count * 100 // max(total, 1),
        },
        "sessions": [],
    }

    for r in results:
        session_entry = {
            "name": r["name"],
            "pipeline": r["pipeline"]["verdict"],
            "gates": {
                "verdict": r["gates"]["verdict"],
                "passed": r["gates"].get("passed", 0),
                "total": r["gates"].get("total", 0),
            },
            "provenance": r["provenance"]["verdict"],
            "overall": r["overall"],
            "failure_reasons": r.get("failure_reasons", []),
        }
        report["sessions"].append(session_entry)

    return json.dumps(report, indent=2)


def render_csv_report(results: list[dict]) -> str:
    """Render CSV report."""
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "session",
            "pipeline",
            "gates_passed",
            "gates_total",
            "gates_verdict",
            "provenance",
            "overall",
            "failure_reasons",
        ]
    )
    for r in results:
        writer.writerow(
            [
                r["name"],
                r["pipeline"]["verdict"],
                r["gates"].get("passed", 0),
                r["gates"].get("total", 0),
                r["gates"]["verdict"],
                r["provenance"]["verdict"],
                r["overall"],
                "; ".join(r.get("failure_reasons", [])),
            ]
        )
    return output.getvalue()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Real Session Validator — v0.4.1 prep",
    )
    parser.add_argument(
        "--sessions-root",
        required=True,
        help="Root directory containing session subdirectories",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of sessions to validate",
    )
    parser.add_argument(
        "--keyfile",
        type=str,
        default=None,
        help="Path to provenance signing key (omit for dry-run / skip provenance)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of text",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Write CSV report to this path",
    )
    args = parser.parse_args()

    sessions_root = pathlib.Path(args.sessions_root).expanduser()
    keyfile = pathlib.Path(args.keyfile).expanduser() if args.keyfile else None

    # Discover sessions
    all_sessions = discover_sessions(sessions_root)
    total_found = len(all_sessions)

    if total_found == 0:
        if args.json:
            report = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "sessions_root": str(sessions_root),
                "total_found": 0,
                "evaluated": 0,
                "summary": {"PASS": 0, "DEGRADED": 0, "FAIL": 0, "pass_rate_pct": 0},
                "sessions": [],
            }
            print(json.dumps(report, indent=2))
        else:
            print(
                f"REAL SESSION VALIDATOR — {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )
            print(f"  Scanning: {sessions_root}")
            print("  Found: 0 session dirs")
            print("  No sessions found.")
        sys.exit(0)

    # Apply limit
    sessions = all_sessions[: args.limit] if args.limit else all_sessions

    # Validate each session
    results = []
    for session_dir in sessions:
        session_name = session_dir.name

        # Step 1: Pipeline
        pipeline = run_pipeline(session_dir)

        # Step 2: G-gates (only if pipeline passed)
        if pipeline["verdict"] == "PASS":
            gates = run_gates(session_dir)
        else:
            gates = {"verdict": "n/a", "passed": 0, "total": 0, "per_gate": {}, "raw": {}}

        # Step 3: Provenance (only if pipeline + gates passed)
        if pipeline["verdict"] == "PASS" and gates["verdict"] not in (
            "FAIL",
            "BLOCKED",
            "TIMEOUT",
            "ERROR",
            "n/a",
        ):
            if keyfile:
                provenance = run_provenance(session_dir, keyfile)
            else:
                provenance = {"verdict": "SKIPPED", "reason": "no --keyfile"}
        else:
            provenance = {"verdict": "n/a"}

        # Overall verdict
        overall = compute_overall(pipeline, gates, provenance)
        failure_reasons = collect_failure_reasons(session_name, pipeline, gates, provenance)

        results.append(
            {
                "name": session_name,
                "pipeline": pipeline,
                "gates": gates,
                "provenance": provenance,
                "overall": overall,
                "failure_reasons": failure_reasons,
            }
        )

    # Output
    if args.json:
        print(render_json_report(results, str(sessions_root), args.limit, total_found))
    else:
        print(render_text_report(results, str(sessions_root), args.limit, total_found))

    # CSV output (always if --csv specified, regardless of --json)
    if args.csv:
        csv_path = pathlib.Path(args.csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text(render_csv_report(results))

    # Exit code: 0 if all PASS, 1 if any FAIL
    any_fail = any(r["overall"] == "FAIL" for r in results)
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
