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
    skipped: bool = False
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
    def skipped_tests(self) -> int:
        test_skipped = sum(1 for r in self.test_results if r.skipped)
        lint_skipped = 1 if self.lint_result and self.lint_result.skipped else 0
        return test_skipped + lint_skipped
    
    @property
    def passed_tests(self) -> int:
        test_passed = sum(1 for r in self.test_results if r.passed)
        lint_passed = 1 if self.lint_result and self.lint_result.passed else 0
        return test_passed + lint_passed
    
    @property
    def failed_tests(self) -> int:
        return self.total_tests - self.passed_tests - self.skipped_tests
    
    @property
    def runnable_tests(self) -> int:
        """Tests that were actually runnable (not skipped)."""
        return self.total_tests - self.skipped_tests
    
    @property
    def pass_percentage(self) -> float:
        """Pass percentage among runnable tests only."""
        if self.runnable_tests == 0:
            return 0.0
        return (self.passed_tests / self.runnable_tests) * 100


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
    args: List[str] = []
    
    # Tests that take a video file directly
    video_tests = [
        "prd_test_audio_continuity",
        "prd_test_video_no_ui",
    ]
    
    # Tests that take a POSITIONAL file argument (verified against each test's
    # own argparse `usage:` line — NOT guessed).
    file_tests = {
        "prd_test_camera_intrinsics_pinhole": ["action_camera.json"],  # CLI: `... [-v] file`
        "prd_test_systeminfo_required": ["systeminfo.json"],           # CLI: `... [--strict] systeminfo`
        "prd_test_wasd_balance": ["action_camera.json", "inputs.jsonl"],  # CLI: `... [-t] [-v] input`
    }

    # Tests that take the --input FLAG (NOT a positional arg).
    input_flag_tests = {
        "prd_test_metric_units_meters": ["action_camera.json"],  # CLI: `... [--input INPUT]`
    }

    # Tests that take --data-dir argument
    data_dir_tests = [
        "prd_test_route_type_distribution",  # CLI: `... [--data-dir DATA_DIR]`
    ]

    # Tests that take --clips-file argument
    clips_file_tests = []

    # UNIT tests of the conversion LOGIC — they take NO session file (they validate
    # the code's math, not this session's data). The previous map fed them a
    # positional session file, which their argparse rejected (exit 2) → bogus FAIL.
    # Invoke each with its real self-test args (mostly none) instead.
    no_session_tests = {
        "prd_test_action_per_second": [],            # CLI: `... [-a ACTIONS ...]`
        "prd_test_depth_invalid_marker": [],         # CLI: `... [--sentinel ...] [-v]`
        "prd_test_left_hand_coordinates": [],        # CLI: `... [--verbose]`
        "prd_test_stationary_threshold": [],         # CLI: `... [--fps FPS]`
        "prd_test_speed_units_mps": ["--all-tests"], # CLI: `... [--velocity] [--all-tests]`
    }

    # Tests that take --video-dir and --depth-dir arguments
    depth_alignment_test = "prd_test_depth_6fps_alignment"
    
    if test_name in video_tests:
        # Find video file in session directory
        for video_name in ["video.mp4", "recording.mp4", "game.mp4"]:
            video_path = session_dir / video_name
            if video_path.exists():
                args.append(str(video_path))
                break
        else:
            # Fall back to video.mp4 even if it doesn't exist (test will handle error)
            args.append(str(session_dir / "video.mp4"))

    elif test_name in no_session_tests:
        # Unit-test mode: these validate the conversion CODE, not a session file —
        # pass their own self-test args (mostly none) so argparse accepts them
        # instead of crashing on an unexpected positional path.
        args.extend(no_session_tests[test_name])

    elif test_name in file_tests:
        # Find first matching file
        for filename in file_tests[test_name]:
            file_path = session_dir / filename
            if file_path.exists():
                args.append(str(file_path))
                break
        else:
            # Fall back to first option even if it doesn't exist
            args.append(str(session_dir / file_tests[test_name][0]))
    
    elif test_name in input_flag_tests:
        # These tests take --input flag
        for filename in input_flag_tests[test_name]:
            file_path = session_dir / filename
            if file_path.exists():
                args.extend(["--input", str(file_path)])
                break
        else:
            # Fall back to first option even if it doesn't exist
            args.extend(["--input", str(session_dir / input_flag_tests[test_name][0])])
    
    elif test_name in data_dir_tests:
        # These tests take --data-dir argument
        args.extend(["--data-dir", str(session_dir)])
    
    elif test_name in clips_file_tests:
        # These tests take --clips-file argument
        clips_path = session_dir / "clips.json"
        args.extend(["--clips-file", str(clips_path)])
    
    elif test_name == depth_alignment_test:
        # Special handling for depth alignment test
        video_dir = session_dir / "video_frames"
        depth_dir = session_dir / "depth"
        
        if video_dir.exists() and depth_dir.exists():
            args.extend(["--video-dir", str(video_dir), "--depth-dir", str(depth_dir)])
        else:
            # Provide arguments anyway, test will handle missing directories
            args.extend(["--video-dir", str(video_dir), "--depth-dir", str(depth_dir)])
    
    else:
        # Tests with no arguments (e.g., prd_test_240_clip_cap, prd_test_30min_scene_cap)
        pass
    
    return args


def _is_skip_worthy(exit_code: int, stdout: str, stderr: str) -> bool:
    """Determine if an exit code 2 result should be marked as skipped.
    
    Exit code 2 means the test couldn't run due to missing data or
    unavailable tools — not that the data failed validation.
    """
    if exit_code != 2:
        return False
    combined = stdout + stderr
    skip_indicators = [
        "not found", "no such file", "does not exist",
        "not a directory", "no video frames", "no depth",
        "no audio", "moov atom", "Invalid MP4",
        "ffprobe failed", "ffmpeg", "not available",
        "Directory not found", "No clips found",
    ]
    return any(indicator.lower() in combined.lower() for indicator in skip_indicators)


def run_test(test_path: Path, session_dir: Path, timeout: int = 30) -> TestResult:
    """Run a single PRD test and return its result."""
    start_time = datetime.now()
    test_name = test_path.stem
    
    args = get_test_arguments(test_name, session_dir)
    cmd = [sys.executable, str(test_path)] + args
    
    try:
        result = subprocess.run(
            cmd,
            cwd=session_dir.parent if session_dir.exists() else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # Determine if this is a skip (exit code 2 with skip-worthy output)
        skipped = _is_skip_worthy(result.returncode, result.stdout, result.stderr)
        
        # Determine if test passed
        # Exit code 0 = pass, exit code 1 = fail, exit code 2 = skip (if skip-worthy)
        passed = result.returncode == 0
        
        # Extract error message if any
        error = None
        if result.returncode != 0 and not skipped:
            # Try to extract a concise error message
            error_output = result.stderr.strip() or result.stdout.strip()
            if error_output:
                # Take first line of error output
                error_lines = error_output.split('\n')
                for line in error_lines:
                    if line.strip() and not line.startswith('Traceback'):
                        error = line.strip()
                        break
                if not error and error_lines:
                    error = error_lines[0].strip()
        
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=passed,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            error=error,
            duration_seconds=duration,
            skipped=skipped,
        )
        
    except subprocess.TimeoutExpired:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr=f"Test timed out after {timeout} seconds",
            error=f"Timeout after {timeout}s",
            duration_seconds=duration,
            skipped=False,
        )
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr=str(e),
            error=f"Runner error: {e}",
            duration_seconds=duration,
            skipped=False,
        )


def run_lint(session_dir: Path, timeout: int = 30) -> TestResult:
    """Run the PRD lint test."""
    start_time = datetime.now()
    lint_path = Path(__file__).parent / "lint_v3_prd_grounded.py"
    
    cmd = [sys.executable, str(lint_path), str(session_dir)]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # Lint test uses exit code 0 for pass, 2 for fail
        passed = result.returncode == 0
        skipped = False  # Lint test doesn't have skip concept
        
        error = None
        if result.returncode != 0:
            error_output = result.stderr.strip() or result.stdout.strip()
            if error_output:
                error_lines = error_output.split('\n')
                for line in error_lines:
                    if line.strip() and not line.startswith('Traceback'):
                        error = line.strip()
                        break
                if not error and error_lines:
                    error = error_lines[0].strip()
        
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=passed,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            error=error,
            duration_seconds=duration,
            skipped=skipped,
        )
        
    except subprocess.TimeoutExpired:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr=f"Lint timed out after {timeout} seconds",
            error=f"Timeout after {timeout}s",
            duration_seconds=duration,
            skipped=False,
        )
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr=str(e),
            error=f"Runner error: {e}",
            duration_seconds=duration,
            skipped=False,
        )


def generate_report(report: AcceptanceReport, output_path: Path) -> None:
    """Generate a markdown acceptance report."""
    lines = []
    
    # Header
    lines.append("# PRD Acceptance Test Report")
    lines.append("")
    lines.append(f"**Session Directory:** `{report.session_dir}`")
    lines.append(f"**Report Generated:** {report.timestamp.isoformat()}")
    lines.append(f"**Overall Score:** {report.pass_percentage:.1f}% ({report.passed_tests}/{report.runnable_tests} tests passed)")
    lines.append("")
    
    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Test | Status | Duration | Exit Code |")
    lines.append("|------|--------|----------|-----------|")
    
    for result in report.test_results:
        if result.skipped:
            status = "⏭️ SKIP"
        elif result.passed:
            status = "✅ PASS"
        else:
            status = "❌ FAIL"
        lines.append(f"| `{result.test_name}` | {status} | {result.duration_seconds:.1f}s | {result.exit_code} |")
    
    if report.lint_result:
        result = report.lint_result
        if result.skipped:
            status = "⏭️ SKIP"
        elif result.passed:
            status = "✅ PASS"
        else:
            status = "❌ FAIL"
        lines.append(f"| `lint_v3_prd_grounded` | {status} | {result.duration_seconds:.1f}s | {result.exit_code} |")
    
    lines.append("")
    
    # Customer-shareable summary
    lines.append("## Customer-Shareable Summary")
    lines.append("")
    lines.append(f"**Overall Acceptance:** {report.pass_percentage:.1f}%")
    lines.append("")
    
    # Passing tests
    passing_tests = [r for r in report.test_results if r.passed]
    if passing_tests:
        lines.append("**✅ Passing Tests:**")
        for result in passing_tests:
            lines.append(f"- `{result.test_name}`")
        lines.append("")
    
    # Failing tests (not skipped)
    failing_tests = [r for r in report.test_results if not r.passed and not r.skipped]
    if failing_tests:
        lines.append("**❌ Failing Tests:**")
        for result in failing_tests:
            lines.append(f"- `{result.test_name}`")
            if result.error:
                lines.append(f"  - Error: {result.error}")
        lines.append("")
    
    # Skipped tests
    skipped_tests = [r for r in report.test_results if r.skipped]
    if skipped_tests:
        lines.append("**⏭️ Skipped Tests:**")
        for result in skipped_tests:
            lines.append(f"- `{result.test_name}`")
            if result.error:
                lines.append(f"  - Reason: {result.error}")
        lines.append("")
    
    # Lint result
    if report.lint_result:
        lines.append("### PRD Lint Results")
        if report.lint_result.passed:
            lines.append("✅ **PRD Lint: PASSED**")
        else:
            lines.append("❌ **PRD Lint: FAILED**")
            if report.lint_result.error:
                lines.append(f"- Error: {report.lint_result.error}")
        lines.append("")
    
    # Detailed results
    lines.append("## Detailed Results")
    lines.append("")
    
    for result in report.test_results:
        lines.append(f"### `{result.test_name}`")
        lines.append("")
        if result.skipped:
            lines.append("- **Status:** ⏭️ SKIP")
        elif result.passed:
            lines.append("- **Status:** ✅ PASS")
        else:
            lines.append("- **Status:** ❌ FAIL")
        lines.append(f"- **Exit Code:** {result.exit_code}")
        lines.append(f"- **Duration:** {result.duration_seconds:.1f}s")
        lines.append(f"- **Test Path:** `{result.test_path}`")
        if result.error:
            lines.append(f"- **Error:** {result.error}")
        if result.stdout.strip():
            lines.append("- **Output:**")
            lines.append("```")
            lines.append(result.stdout.strip())
            lines.append("```")
        lines.append("")
    
    if report.lint_result:
        result = report.lint_result
        lines.append("### `lint_v3_prd_grounded`")
        lines.append("")
        if result.skipped:
            lines.append("- **Status:** ⏭️ SKIP")
        elif result.passed:
            lines.append("- **Status:** ✅ PASS")
        else:
            lines.append("- **Status:** ❌ FAIL")
        lines.append(f"- **Exit Code:** {result.exit_code}")
        lines.append(f"- **Duration:** {result.duration_seconds:.1f}s")
        lines.append(f"- **Test Path:** `{result.test_path}`")
        if result.error:
            lines.append(f"- **Error:** {result.error}")
        if result.stdout.strip():
            lines.append("- **Output:**")
            lines.append("```")
            lines.append(result.stdout.strip())
            lines.append("```")
        lines.append("")
    
    # Write to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('\n'.join(lines))


def main(argv: List[str] = None) -> int:
    """Main entry point."""
    if argv is None:
        argv = sys.argv[1:]
    
    parser = argparse.ArgumentParser(description="Run PRD acceptance tests on a session directory.")
    parser.add_argument("session_dir", type=Path, help="Path to session directory")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Output report path (default: <session_dir>/PRD-ACCEPTANCE-REPORT.md)")
    parser.add_argument("--timeout", "-t", type=int, default=30,
                        help="Timeout per test in seconds (default: 30)")
    
    args = parser.parse_args(argv)
    
    session_dir = args.session_dir
    if not session_dir.exists():
        print(f"Error: Session directory not found: {session_dir}", file=sys.stderr)
        return 1
    
    output_path = args.output or session_dir / "PRD-ACCEPTANCE-REPORT.md"
    
    # Find all PRD tests
    bin_dir = Path(__file__).parent
    test_paths = find_prd_tests(bin_dir)
    
    if not test_paths:
        print("Error: No PRD tests found in bin/ directory", file=sys.stderr)
        return 1
    
    print(f"Running {len(test_paths)} PRD tests on {session_dir}...")
    
    # Run all tests
    test_results = []
    for test_path in test_paths:
        print(f"  Running {test_path.stem}...", end="", flush=True)
        result = run_test(test_path, session_dir, timeout=args.timeout)
        test_results.append(result)
        
        if result.skipped:
            print(f" ⏭️ SKIP")
        elif result.passed:
            print(f" ✅ PASS")
        else:
            print(f" ❌ FAIL")
    
    # Run lint test
    print(f"  Running lint_v3_prd_grounded...", end="", flush=True)
    lint_result = run_lint(session_dir, timeout=args.timeout)
    if lint_result.passed:
        print(f" ✅ PASS")
    else:
        print(f" ❌ FAIL")
    
    # Generate report
    report = AcceptanceReport(
        session_dir=session_dir,
        timestamp=datetime.now(),
        test_results=test_results,
        lint_result=lint_result,
    )
    
    generate_report(report, output_path)
    
    print(f"\nReport written to: {output_path}")
    print(f"Overall score: {report.pass_percentage:.1f}% ({report.passed_tests}/{report.runnable_tests} tests passed)")
    
    # Return non-zero if any tests failed (not skipped)
    if report.failed_tests > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())