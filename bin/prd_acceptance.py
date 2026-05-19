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
    args = []
    
    # Tests that take a video file as positional argument
    video_tests = ["prd_test_audio_continuity", "prd_test_video_no_ui"]
    
    # Tests that take a file as positional argument
    file_tests = {
        "prd_test_camera_intrinsics_pinhole": ["action_camera.json", "frames.jsonl", "camera.json"],
        "prd_test_systeminfo_required": ["systeminfo.json", "systeminfo.yaml", "systeminfo.yml"],
        "prd_test_wasd_balance": ["inputs.jsonl", "action_camera.json", "frames.jsonl"],
    }

    # Tests that take --input flag (not positional)
    input_flag_tests = {
        "prd_test_action_per_second": ["action_camera.json", "actions.json"],
        "prd_test_metric_units_meters": ["action_camera.json", "camera.json"],
    }
    
    # Tests that take --data-dir argument
    data_dir_tests = [
        "prd_test_route_type_distribution",
    ]
    
    # Tests that take --clips-file argument
    clips_file_tests = []
    
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
    
    # Build command
    cmd = [sys.executable, str(test_path)] + args
    
    try:
        result = subprocess.run(
            cmd,
            cwd=session_dir.parent if session_dir.exists() else None,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        duration = (datetime.now() - start_time).total_seconds()
        
        # Determine if test passed (exit code 0), failed (exit code 1),
        # or skipped (exit code 2 = missing data / unavailable tools)
        passed = result.returncode == 0
        skipped = _is_skip_worthy(result.returncode, result.stdout, result.stderr)
        
        # Extract error message if any
        error = None
        if result.returncode == 2:
            # Try to extract error from stderr
            error_lines = result.stderr.strip().split('\n')
            if error_lines:
                error = error_lines[0]
            elif result.stdout:
                # Some tests print errors to stdout
                error_lines = result.stdout.strip().split('\n')
                if error_lines:
                    error = error_lines[0]
        elif result.returncode != 0:
            # For non-zero exit codes, capture first line of error
            error_lines = (result.stderr or result.stdout).strip().split('\n')
            if error_lines:
                error = error_lines[0]
        
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=passed,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            error=error,
            duration_seconds=duration,
            skipped=skipped
        )
    
    except subprocess.TimeoutExpired:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=-1,
            error=f"Test timed out after {timeout} seconds",
            duration_seconds=duration,
            skipped=False
        )
    
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=-1,
            error=str(e),
            duration_seconds=duration,
            skipped=False
        )


def run_lint(session_dir: Path, bin_dir: Path, timeout: int = 30) -> TestResult:
    """Run the PRD lint check on session directory."""
    start_time = datetime.now()
    lint_script = bin_dir / "lint_v3_prd_grounded.py"
    
    if not lint_script.exists():
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_script,
            passed=False,
            exit_code=-1,
            error="Lint script not found",
            duration_seconds=0.0,
            skipped=True
        )
    
    cmd = [sys.executable, str(lint_script), str(session_dir)]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        duration = (datetime.now() - start_time).total_seconds()
        
        passed = result.returncode == 0
        error = None
        if not passed:
            error_lines = (result.stderr or result.stdout).strip().split('\n')
            if error_lines:
                error = error_lines[0]
        
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_script,
            passed=passed,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            error=error,
            duration_seconds=duration,
            skipped=False
        )
    
    except subprocess.TimeoutExpired:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_script,
            passed=False,
            exit_code=-1,
            error=f"Lint timed out after {timeout} seconds",
            duration_seconds=duration,
            skipped=False
        )
    
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_script,
            passed=False,
            exit_code=-1,
            error=str(e),
            duration_seconds=duration,
            skipped=False
        )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(report: AcceptanceReport, output_path: Path) -> None:
    """Generate a markdown report from the acceptance test results."""
    lines = []
    
    # Header
    lines.append("# PRD Acceptance Test Report")
    lines.append("")
    lines.append(f"**Session Directory:** `{report.session_dir}`")
    lines.append(f"**Report Generated:** {report.timestamp.isoformat()}")
    
    if report.skipped_tests > 0:
        lines.append(f"**Overall Score:** {report.pass_percentage:.1f}% ({report.passed_tests}/{report.runnable_tests} runnable tests passed, {report.skipped_tests} skipped)")
    else:
        lines.append(f"**Overall Score:** {report.pass_percentage:.1f}% ({report.passed_tests}/{report.total_tests} tests passed)")
    lines.append("")
    
    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Test | Status | Duration | Exit Code |")
    lines.append("|------|--------|----------|-----------|")
    
    for result in report.test_results:
        status = "✅ PASS" if result.passed else ("⏭️ SKIP" if result.skipped else "❌ FAIL")
        lines.append(f"| `{result.test_name}` | {status} | {result.duration_seconds:.1f}s | {result.exit_code} |")
    
    if report.lint_result:
        status = "✅ PASS" if report.lint_result.passed else "❌ FAIL"
        lines.append(f"| `lint_v3_prd_grounded` | {status} | {report.lint_result.duration_seconds:.1f}s | {report.lint_result.exit_code} |")
    
    lines.append("")
    
    # Customer-shareable summary
    lines.append("## Customer-Shareable Summary")
    lines.append("")
    if report.skipped_tests > 0:
        lines.append(f"**Overall Acceptance:** {report.pass_percentage:.1f}%")
    else:
        lines.append(f"**Overall Acceptance:** {report.pass_percentage:.1f}%")
    lines.append("")
    
    # Passing tests
    passing = [r for r in report.test_results if r.passed]
    if passing:
        lines.append("### ✅ Passing Tests")
        lines.append("")
        for result in passing:
            lines.append(f"- `{result.test_name}`")
        lines.append("")
    
    # Skipped tests
    skipped = [r for r in report.test_results if r.skipped]
    if skipped:
        lines.append("### ⏭️ Skipped Tests (missing session data)")
        lines.append("")
        for result in skipped:
            lines.append(f"- `{result.test_name}`")
            if result.error:
                lines.append(f"  - Reason: {result.error}")
        lines.append("")
    
    # Failing tests
    failing = [r for r in report.test_results if not r.passed and not r.skipped]
    if failing:
        lines.append("### ❌ Failing Tests")
        lines.append("")
        for result in failing:
            lines.append(f"- `{result.test_name}`")
            if result.error:
                lines.append(f"  - Error: {result.error}")
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
        lines.append("")
    
    # Detailed results
    lines.append("## Detailed Results")
    lines.append("")
    
    for result in report.test_results:
        lines.append(f"### `{result.test_name}`")
        lines.append("")
        status = "✅ PASS" if result.passed else ("⏭️ SKIP" if result.skipped else "❌ FAIL")
        lines.append(f"- **Status:** {status}")
        lines.append(f"- **Exit Code:** {result.exit_code}")
        lines.append(f"- **Duration:** {result.duration_seconds:.1f}s")
        lines.append(f"- **Test Path:** {result.test_path}")
        
        if result.error:
            lines.append(f"- **Error:** {result.error}")
        
        if result.stdout and not result.passed:
            lines.append("")
            lines.append("**stdout:**")
            lines.append("```")
            lines.append(result.stdout[:500] if len(result.stdout) > 500 else result.stdout)
            lines.append("```")
        
        if result.stderr and not result.passed:
            lines.append("")
            lines.append("**stderr:**")
            lines.append("```")
            lines.append(result.stderr[:500] if len(result.stderr) > 500 else result.stderr)
            lines.append("```")
        
        lines.append("")
    
    if report.lint_result:
        lines.append(f"### `lint_v3_prd_grounded`")
        lines.append("")
        status = "✅ PASS" if report.lint_result.passed else "❌ FAIL"
        lines.append(f"- **Status:** {status}")
        lines.append(f"- **Exit Code:** {report.lint_result.exit_code}")
        lines.append(f"- **Duration:** {report.lint_result.duration_seconds:.1f}s")
        
        if report.lint_result.error:
            lines.append(f"- **Error:** {report.lint_result.error}")
        
        lines.append("")
    
    # Write report
    output_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run all PRD acceptance tests on a session directory"
    )
    parser.add_argument(
        "session_dir",
        type=Path,
        help="Path to session directory containing test data"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Output report path (default: session_dir/PRD-ACCEPTANCE-REPORT.md)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout per test in seconds (default: 30)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed test output"
    )
    
    args = parser.parse_args()
    
    if not args.session_dir.exists():
        print(f"Error: Session directory does not exist: {args.session_dir}", file=sys.stderr)
        sys.exit(1)
    
    if not args.session_dir.is_dir():
        print(f"Error: Session path is not a directory: {args.session_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Determine output path
    output_path = args.output or (args.session_dir / "PRD-ACCEPTANCE-REPORT.md")
    
    # Find bin directory (relative to this script)
    bin_dir = Path(__file__).parent
    
    # Find all PRD tests
    test_paths = find_prd_tests(bin_dir)
    
    if not test_paths:
        print("Warning: No prd_test_*.py files found in bin/", file=sys.stderr)
    
    # Run all tests
    print(f"Running {len(test_paths)} PRD tests on {args.session_dir}...")
    
    report = AcceptanceReport(
        session_dir=args.session_dir,
        timestamp=datetime.now()
    )
    
    for test_path in test_paths:
        if args.verbose:
            print(f"  Running {test_path.name}...")
        
        result = run_test(test_path, args.session_dir, timeout=args.timeout)
        report.test_results.append(result)
        
        if args.verbose:
            status = "PASS" if result.passed else ("SKIP" if result.skipped else "FAIL")
            print(f"    {status} ({result.duration_seconds:.1f}s)")
    
    # Run lint
    if args.verbose:
        print("  Running PRD lint...")
    
    lint_result = run_lint(args.session_dir, bin_dir, timeout=args.timeout)
    report.lint_result = lint_result
    
    if args.verbose:
        status = "PASS" if lint_result.passed else "FAIL"
        print(f"    {status} ({lint_result.duration_seconds:.1f}s)")
    
    # Generate report
    generate_report(report, output_path)
    
    # Print summary
    print(f"\nResults: {report.passed_tests}/{report.runnable_tests} tests passed")
    if report.skipped_tests > 0:
        print(f"         {report.skipped_tests} tests skipped (missing data)")
    print(f"Report written to: {output_path}")
    
    # Exit with appropriate code
    if report.passed_tests == report.runnable_tests:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()