#!/usr/bin/env python3
"""
PRD Acceptance Test Runner

Runs ALL prd_test_*.py scripts in bin/ (16 existing tests) and
lint_v3_prd_grounded.py on a session directory, then aggregates results
into a single PRD-ACCEPTANCE-REPORT.md.

Usage:
    python bin/prd_acceptance.py /path/to/session
"""

import argparse
import json
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    """Result of a single test execution."""
    test_name: str
    test_path: Path
    passed: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    duration_seconds: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AcceptanceReport:
    """Complete acceptance test report."""
    session_dir: Path
    timestamp: datetime
    test_results: List[TestResult] = field(default_factory=list)
    lint_result: Optional[TestResult] = None
    
    @property
    def total_tests(self) -> int:
        return len(self.test_results) + (1 if self.lint_result else 0)
    
    @property
    def passed_tests(self) -> int:
        test_passed = sum(1 for r in self.test_results if r.passed)
        lint_passed = 1 if self.lint_result and self.lint_result.passed else 0
        return test_passed + lint_passed
    
    @property
    def failed_tests(self) -> int:
        return self.total_tests - self.passed_tests
    
    @property
    def pass_percentage(self) -> float:
        if self.total_tests == 0:
            return 0.0
        return (self.passed_tests / self.total_tests) * 100


# ---------------------------------------------------------------------------
# Test discovery and execution
# ---------------------------------------------------------------------------

def find_prd_tests(bin_dir: Path) -> List[Path]:
    """Find all prd_test_*.py files in bin directory."""
    tests = []
    for test_file in bin_dir.glob("prd_test_*.py"):
        tests.append(test_file)
    return sorted(tests)


def get_test_arguments(test_name: str, session_dir: Path) -> List[str]:
    """Get command line arguments for a specific test based on session directory contents."""
    args = []
    
    # Tests that take a video file as positional argument
    video_tests = ["prd_test_audio_continuity", "prd_test_video_no_ui"]
    
    # Tests that take a file as positional argument
    file_tests = {
        "prd_test_camera_intrinsics_pinhole": ["action_camera.json", "frames.jsonl", "camera.json"],
        "prd_test_systeminfo_required": ["systeminfo.json", "systeminfo.yaml", "systeminfo.yml"],
        "prd_test_wasd_balance": ["inputs.jsonl", "action_camera.json", "frames.jsonl"],
    }
    
    # Tests that need specific directories
    directory_tests = {
        "prd_test_depth_6fps_alignment": {
            "video_dir": ["rgb", "."],  # Look for video in rgb dir or session root
            "depth_dir": ["depth"]
        }
    }
    
    # Tests that take --input or --data-dir arguments
    input_arg_tests = {
        "prd_test_action_per_second": {"arg": "--input", "files": ["action_camera.json", "frames.jsonl", "inputs.jsonl"]},
        "prd_test_metric_units_meters": {"arg": "--input", "files": ["action_camera.json", "frames.jsonl"]},
        "prd_test_route_type_distribution": {"arg": "--data-dir", "files": ["."]},  # Session dir itself
    }
    
    # Self-contained tests (no arguments needed)
    self_contained_tests = [
        "prd_test_240_clip_cap",
        "prd_test_30min_scene_cap", 
        "prd_test_depth_invalid_marker",
        "prd_test_left_hand_coordinates",
        "prd_test_speed_units_mps",
        "prd_test_stationary_threshold",
    ]
    
    # Handle video tests
    if test_name in video_tests:
        video_files = list(session_dir.glob("*.mp4")) + list(session_dir.glob("video.*"))
        if video_files:
            args.append(str(video_files[0]))
            if test_name == "prd_test_video_no_ui":
                args.extend(["--frames", "5"])  # Sample fewer frames for speed
        else:
            raise FileNotFoundError(f"No video file found in {session_dir}")
    
    # Handle file tests
    elif test_name in file_tests:
        found = False
        for pattern in file_tests[test_name]:
            files = list(session_dir.glob(pattern))
            if files:
                args.append(str(files[0]))
                found = True
                break
        
        if not found:
            # Try to find any JSON file
            json_files = list(session_dir.glob("*.json"))
            if json_files:
                args.append(str(json_files[0]))
            else:
                raise FileNotFoundError(f"No suitable input file found for {test_name} in {session_dir}")
    
    # Handle directory tests
    elif test_name in directory_tests:
        test_config = directory_tests[test_name]
        for dir_arg, dir_options in test_config.items():
            found_dir = None
            for dir_option in dir_options:
                dir_path = session_dir / dir_option
                if dir_path.exists() and dir_path.is_dir():
                    found_dir = dir_path
                    break
            
            if found_dir:
                args.extend([f"--{dir_arg.replace('_', '-')}", str(found_dir)])
            else:
                # For video_dir, fall back to session directory
                if dir_arg == "video_dir":
                    args.extend([f"--{dir_arg.replace('_', '-')}", str(session_dir)])
                else:
                    raise FileNotFoundError(f"Required directory '{dir_arg}' not found for {test_name}")
    
    # Handle input argument tests
    elif test_name in input_arg_tests:
        test_config = input_arg_tests[test_name]
        arg_name = test_config["arg"]
        
        found_file = None
        for file_pattern in test_config["files"]:
            if file_pattern == ".":
                # Special case: use session directory itself
                found_file = session_dir
                break
            
            files = list(session_dir.glob(file_pattern))
            if files:
                found_file = files[0]
                break
        
        if found_file:
            args.extend([arg_name, str(found_file)])
        else:
            raise FileNotFoundError(f"No input file found for {test_name} in {session_dir}")
    
    # Handle self-contained tests
    elif test_name in self_contained_tests:
        # No arguments needed
        pass
    
    else:
        # Default: try to pass session directory as argument
        args.append(str(session_dir))
    
    return args


def run_test(test_path: Path, session_dir: Path, timeout: int = 300) -> TestResult:
    """Run a single test script with appropriate arguments."""
    import time
    
    start_time = time.time()
    test_name = test_path.stem
    
    # Build command
    cmd = [sys.executable, str(test_path)]
    
    try:
        # Get test-specific arguments
        test_args = get_test_arguments(test_name, session_dir)
        cmd.extend(test_args)
        
    except FileNotFoundError as e:
        # Required file/directory not found
        duration = time.time() - start_time
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=1,
            error=str(e),
            duration_seconds=duration
        )
    except Exception as e:
        # Other error getting arguments
        duration = time.time() - start_time
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=1,
            error=f"Error preparing test arguments: {str(e)}",
            duration_seconds=duration
        )
    
    # Execute the test
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=session_dir
        )
        
        duration = time.time() - start_time
        
        # Determine if test passed based on exit code (0 = success)
        passed = result.returncode == 0
        
        # Try to parse JSON output if available
        metadata = {}
        if result.stdout.strip():
            try:
                metadata = json.loads(result.stdout)
            except:
                # Not JSON, store as text
                metadata = {"output": result.stdout[:500]}  # Limit size
        
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=passed,
            exit_code=result.returncode,
            stdout=result.stdout[:1000],  # Limit size
            stderr=result.stderr[:1000],  # Limit size
            duration_seconds=duration,
            metadata=metadata
        )
        
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=124,  # Standard timeout exit code
            error=f"Test timed out after {timeout} seconds",
            duration_seconds=duration
        )
    except Exception as e:
        duration = time.time() - start_time
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=1,
            error=f"Failed to execute test: {str(e)}",
            duration_seconds=duration
        )


def run_lint_v3(session_dir: Path, timeout: int = 600) -> TestResult:
    """Run lint_v3_prd_grounded.py on session directory."""
    import time
    
    start_time = time.time()
    lint_path = Path(__file__).parent / "lint_v3_prd_grounded.py"
    
    # Use absolute path for session directory
    abs_session_dir = session_dir.resolve()
    cmd = [sys.executable, str(lint_path), str(abs_session_dir), "--verbose"]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=abs_session_dir.parent if abs_session_dir.parent.exists() else None
        )
        
        duration = time.time() - start_time
        
        # Parse lint output
        metadata = {}
        if result.stdout.strip():
            try:
                metadata = json.loads(result.stdout)
            except:
                # Try to extract summary from text output
                metadata = {"output": result.stdout[:500]}
        
        # Lint passes if exit code is 0
        passed = result.returncode == 0
        
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=passed,
            exit_code=result.returncode,
            stdout=result.stdout[:2000],  # Lint output can be large
            stderr=result.stderr[:1000],
            duration_seconds=duration,
            metadata=metadata
        )
        
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=False,
            exit_code=124,
            error=f"Lint timed out after {timeout} seconds",
            duration_seconds=duration
        )
    except Exception as e:
        duration = time.time() - start_time
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=False,
            exit_code=1,
            error=f"Failed to execute lint: {str(e)}",
            duration_seconds=duration
        )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_markdown_report(report: AcceptanceReport) -> str:
    """Generate PRD-ACCEPTANCE-REPORT.md content."""
    lines = []
    
    # Header
    lines.append("# PRD Acceptance Test Report")
    lines.append("")
    lines.append(f"**Session Directory:** `{report.session_dir}`")
    lines.append(f"**Report Generated:** {report.timestamp.isoformat()}")
    lines.append(f"**Overall Score:** {report.pass_percentage:.1f}% ({report.passed_tests}/{report.total_tests} tests passed)")
    lines.append("")
    
    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Test | Status | Duration | Exit Code |")
    lines.append("|------|--------|----------|-----------|")
    
    # Add PRD test results
    for test_result in report.test_results:
        status = "✅ PASS" if test_result.passed else "❌ FAIL"
        duration = f"{test_result.duration_seconds:.1f}s"
        lines.append(f"| `{test_result.test_name}` | {status} | {duration} | {test_result.exit_code} |")
    
    # Add lint result
    if report.lint_result:
        status = "✅ PASS" if report.lint_result.passed else "❌ FAIL"
        duration = f"{report.lint_result.duration_seconds:.1f}s"
        lines.append(f"| `{report.lint_result.test_name}` | {status} | {duration} | {report.lint_result.exit_code} |")
    
    lines.append("")
    
    # Customer-shareable section
    lines.append("## Customer-Shareable Summary")
    lines.append("")
    lines.append(f"**Overall Acceptance:** {report.pass_percentage:.1f}%")
    lines.append("")
    lines.append("### Test Results")
    lines.append("")
    
    # Group by pass/fail
    passed_tests = [r for r in report.test_results if r.passed]
    failed_tests = [r for r in report.test_results if not r.passed]
    
    if passed_tests:
        lines.append("**✅ Passing Tests:**")
        for test_result in passed_tests:
            lines.append(f"- `{test_result.test_name}`")
        lines.append("")
    
    if failed_tests:
        lines.append("**❌ Failing Tests:**")
        for test_result in failed_tests:
            lines.append(f"- `{test_result.test_name}`")
            if test_result.error:
                lines.append(f"  - Error: {test_result.error}")
            elif test_result.stderr:
                # Take first line of stderr
                first_line = test_result.stderr.split('\n')[0].strip()
                if first_line:
                    lines.append(f"  - Error: {first_line}")
        lines.append("")
    
    # Lint results
    if report.lint_result:
        lines.append("### PRD Lint Results")
        lines.append("")
        if report.lint_result.passed:
            lines.append("✅ **PRD Lint: PASSED**")
        else:
            lines.append("❌ **PRD Lint: FAILED**")
            if report.lint_result.error:
                lines.append(f"- Error: {report.lint_result.error}")
            elif report.lint_result.stderr:
                first_line = report.lint_result.stderr.split('\n')[0].strip()
                if first_line:
                    lines.append(f"- Error: {first_line}")
        lines.append("")
    
    # Detailed results (for internal use)
    lines.append("## Detailed Results")
    lines.append("")
    
    for test_result in report.test_results:
        lines.append(f"### `{test_result.test_name}`")
        lines.append("")
        lines.append(f"- **Status:** {'✅ PASS' if test_result.passed else '❌ FAIL'}")
        lines.append(f"- **Exit Code:** {test_result.exit_code}")
        lines.append(f"- **Duration:** {test_result.duration_seconds:.1f}s")
        lines.append(f"- **Test Path:** `{test_result.test_path}`")
        
        if test_result.error:
            lines.append(f"- **Error:** {test_result.error}")
        
        if test_result.stdout:
            lines.append("- **Output:**")
            lines.append("```")
            lines.append(test_result.stdout[:500])  # Limit output size
            if len(test_result.stdout) > 500:
                lines.append("... (output truncated)")
            lines.append("```")
        
        if test_result.stderr:
            lines.append("- **Errors:**")
            lines.append("```")
            lines.append(test_result.stderr[:500])  # Limit error size
            if len(test_result.stderr) > 500:
                lines.append("... (errors truncated)")
            lines.append("```")
        
        lines.append("")
    
    # Lint detailed results
    if report.lint_result:
        lines.append(f"### `{report.lint_result.test_name}`")
        lines.append("")
        lines.append(f"- **Status:** {'✅ PASS' if report.lint_result.passed else '❌ FAIL'}")
        lines.append(f"- **Exit Code:** {report.lint_result.exit_code}")
        lines.append(f"- **Duration:** {report.lint_result.duration_seconds:.1f}s")
        
        if report.lint_result.error:
            lines.append(f"- **Error:** {report.lint_result.error}")
        
        # Try to show lint summary if available in metadata
        if report.lint_result.metadata and 'summary' in report.lint_result.metadata:
            summary = report.lint_result.metadata['summary']
            lines.append(f"- **Lint Summary:** {summary.get('passed', 0)}/{summary.get('total', 0)} checks passed")
        
        lines.append("")
    
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run PRD acceptance tests on a session directory"
    )
    parser.add_argument(
        "session_dir",
        type=Path,
        help="Path to session directory to test"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("PRD-ACCEPTANCE-REPORT.md"),
        help="Output report file path (default: PRD-ACCEPTANCE-REPORT.md)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout per test in seconds (default: 300)"
    )
    parser.add_argument(
        "--lint-timeout",
        type=int,
        default=600,
        help="Timeout for lint in seconds (default: 600)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    # Validate session directory
    if not args.session_dir.exists():
        print(f"Error: Session directory not found: {args.session_dir}", file=sys.stderr)
        return 1
    
    if not args.session_dir.is_dir():
        print(f"Error: Not a directory: {args.session_dir}", file=sys.stderr)
        return 1
    
    # Find PRD tests
    bin_dir = Path(__file__).parent
    test_files = find_prd_tests(bin_dir)
    
    if args.verbose:
        print(f"Found {len(test_files)} PRD test files:")
        for test_file in test_files:
            print(f"  - {test_file.name}")
        print()
    
    # Create report
    report = AcceptanceReport(
        session_dir=args.session_dir,
        timestamp=datetime.now()
    )
    
    # rc18.0.5 (Howard 2026-05-12 "继续补充"): run finalize_session.py BEFORE
    # any tests / lint so action_camera.json has quaternion + position backfilled
    # from game_state.jsonl, gameinfo.xlsx exists, and audio_check.json is
    # generated. Without this step, fresh recordings show:
    #   * action_camera quaternion/position fields NULL (recorder writes them
    #     before mc-mod's game_state.jsonl is in the session dir)
    #   * gameinfo.xlsx missing entirely
    #   * audio_check.json missing → lint #38 fails on "file not found"
    # finalize_session.py is idempotent — safe to re-run.
    finalize_path = bin_dir / "finalize_session.py"
    if finalize_path.exists():
        print(f"\nRunning finalize_session.py (rc18.0.5 pre-lint finalization)...")
        try:
            fin_result = subprocess.run(
                [sys.executable, str(finalize_path), str(args.session_dir),
                 "--verbose" if args.verbose else ""],
                capture_output=True, text=True, timeout=300,
            )
            if args.verbose:
                print(fin_result.stdout)
                if fin_result.returncode != 0:
                    print(f"  finalize exit={fin_result.returncode}", file=sys.stderr)
                    if fin_result.stderr.strip():
                        print(f"  stderr: {fin_result.stderr.strip()[:300]}", file=sys.stderr)
            else:
                # Just print the "Done in X.Xs — ..." summary line
                for line in fin_result.stdout.splitlines():
                    if line.startswith("Done in"):
                        print(f"  {line}")
                        break
        except Exception as e:
            print(f"  finalize_session.py exception (continuing): {e}", file=sys.stderr)

    # Run PRD tests
    print(f"\nRunning {len(test_files)} PRD tests on {args.session_dir}...")
    for i, test_file in enumerate(test_files, 1):
        if args.verbose:
            print(f"[{i}/{len(test_files)}] Running {test_file.name}...")
        
        test_result = run_test(test_file, args.session_dir, args.timeout)
        report.test_results.append(test_result)
        
        if args.verbose:
            status = "✅ PASS" if test_result.passed else "❌ FAIL"
            print(f"  {status} ({test_result.duration_seconds:.1f}s)")

    # rc18.0.4 (Howard 2026-05-12 PRD §6 lint #38 fix): run
    # audio_continuity_check.py against the session BEFORE lint_v3 so that
    # the lint criterion #38 ("Audio Continuity") has its audio_check.json
    # input. Without this step, #38 fails with "audio_check.json not found"
    # even when the recording's audio stream is perfectly fine.
    print(f"\nRunning audio_continuity_check.py (pre-lint #38 dependency)...")
    audio_check_path = bin_dir / "audio_continuity_check.py"
    if audio_check_path.exists():
        try:
            import time as _t
            _t0 = _t.time()
            video_files = (list(args.session_dir.glob("recording.mp4"))
                           + list(args.session_dir.glob("video.mp4")))
            if video_files:
                primary_video = video_files[0]
                out_json = args.session_dir / "audio_check.json"
                cmd = [sys.executable, str(audio_check_path),
                       str(primary_video), "--output", str(out_json)]
                ac_result = subprocess.run(cmd, capture_output=True,
                                           text=True, timeout=180,
                                           cwd=args.session_dir)
                _dt = _t.time() - _t0
                status = "✅ PASS" if ac_result.returncode == 0 else "⚠️ skip"
                if args.verbose:
                    print(f"  {status} ({_dt:.1f}s) -> {out_json.name}")
            else:
                if args.verbose:
                    print("  ⚠️ skip — no recording.mp4 / video.mp4 in session")
        except Exception as e:
            if args.verbose:
                print(f"  ⚠️ skip — exception: {e}")
    else:
        if args.verbose:
            print(f"  ⚠️ skip — {audio_check_path.name} not present")

    # Run lint
    print(f"\nRunning lint_v3_prd_grounded.py...")
    lint_result = run_lint_v3(args.session_dir, args.lint_timeout)
    report.lint_result = lint_result
    
    if args.verbose:
        status = "✅ PASS" if lint_result.passed else "❌ FAIL"
        print(f"  {status} ({lint_result.duration_seconds:.1f}s)")
    
    # Generate report
    print(f"\nGenerating report...")
    markdown_content = generate_markdown_report(report)
    
    # Write report
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    
    print(f"Report written to: {args.output}")
    print(f"\nSummary: {report.passed_tests}/{report.total_tests} tests passed ({report.pass_percentage:.1f}%)")
    
    # Return non-zero if any tests failed
    return 0 if report.failed_tests == 0 else 1


if __name__ == "__main__":
    sys.exit(main())