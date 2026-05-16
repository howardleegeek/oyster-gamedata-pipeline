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
                found = True
        
        if not found:
            raise FileNotFoundError(f"No input file found for {test_name} in {session_dir}")
    
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
                # Fall back to session directory or expected subdirectory
                # For video_dir, fall back to session directory
                if dir_arg == "video_dir":
                    fallback_dir = session_dir
                else:
                    # For depth_dir, fall back to session_dir/depth (even if it doesn't exist)
                    # This allows the test to run and handle the missing directory gracefully
                    fallback_dir = session_dir / dir_options[0]  # First option, e.g., "depth"
                args.extend([f"--{dir_arg.replace('_', '-')}", str(fallback_dir)])
    
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


def run_test(test_path: Path, session_dir: Path, timeout: int = 30) -> TestResult:
    """Run a single PRD test and return its result."""
    test_name = test_path.stem
    start_time = datetime.now()
    
    try:
        # Get arguments for this test
        args = get_test_arguments(test_name, session_dir)
        
        # Run the test
        cmd = [sys.executable, str(test_path)] + args
        if test_name == "prd_test_30min_scene_cap":
            # This test has a sleep, cap it
            cmd.extend(["--duration", "0.1"])
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=session_dir
        )
        
        duration = (datetime.now() - start_time).total_seconds()
        
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            duration_seconds=duration
        )
        
    except FileNotFoundError as e:
        # Handle missing required files/directories
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=1,
            error=str(e),
            duration_seconds=duration
        )
    except subprocess.TimeoutExpired:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=124,  # Standard timeout exit code
            error=f"Test timed out after {timeout} seconds",
            duration_seconds=duration
        )
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name=test_name,
            test_path=test_path,
            passed=False,
            exit_code=2,
            error=f"Unexpected error: {e}",
            duration_seconds=duration,
            metadata={"traceback": traceback.format_exc()}
        )


def run_lint(session_dir: Path, timeout: int = 60) -> TestResult:
    """Run the PRD lint script."""
    lint_path = Path(__file__).parent / "lint_v3_prd_grounded.py"
    start_time = datetime.now()
    
    try:
        result = subprocess.run(
            [sys.executable, str(lint_path), str(session_dir), "--strict=false"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=session_dir
        )
        
        duration = (datetime.now() - start_time).total_seconds()
        
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            duration_seconds=duration
        )
        
    except subprocess.TimeoutExpired:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=False,
            exit_code=124,
            error=f"Lint timed out after {timeout} seconds",
            duration_seconds=duration
        )
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        return TestResult(
            test_name="lint_v3_prd_grounded",
            test_path=lint_path,
            passed=False,
            exit_code=2,
            error=f"Unexpected error: {e}",
            duration_seconds=duration,
            metadata={"traceback": traceback.format_exc()}
        )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_markdown_report(report: AcceptanceReport, output_path: Path) -> None:
    """Generate a markdown report from acceptance test results."""
    with open(output_path, "w") as f:
        f.write("# PRD Acceptance Test Report\n\n")
        f.write(f"**Session Directory:** `{report.session_dir}`\n")
        f.write(f"**Report Generated:** {report.timestamp.isoformat()}\n")
        f.write(f"**Overall Score:** {report.pass_percentage:.1f}% ({report.passed_tests}/{report.total_tests} tests passed)\n\n")
        
        f.write("## Summary\n\n")
        f.write("| Test | Status | Duration | Exit Code |\n")
        f.write("|------|--------|----------|-----------|\n")
        
        # Add test results
        for test_result in report.test_results:
            status = "✅ PASS" if test_result.passed else "❌ FAIL"
            f.write(f"| `{test_result.test_name}` | {status} | {test_result.duration_seconds:.1f}s | {test_result.exit_code} |\n")
        
        # Add lint result
        if report.lint_result:
            status = "✅ PASS" if report.lint_result.passed else "❌ FAIL"
            f.write(f"| `{report.lint_result.test_name}` | {status} | {report.lint_result.duration_seconds:.1f}s | {report.lint_result.exit_code} |\n")
        
        f.write("\n")
        
        # Customer-shareable summary
        f.write("## Customer-Shareable Summary\n\n")
        f.write(f"**Overall Acceptance:** {report.pass_percentage:.1f}%\n\n")
        
        f.write("### Test Results\n\n")
        
        # Passing tests
        passing_tests = [r for r in report.test_results if r.passed]
        if passing_tests:
            f.write("**✅ Passing Tests:**\n")
            for test_result in passing_tests:
                f.write(f"- `{test_result.test_name}`\n")
            f.write("\n")
        
        # Failing tests
        failing_tests = [r for r in report.test_results if not r.passed]
        if failing_tests:
            f.write("**❌ Failing Tests:**\n")
            for test_result in failing_tests:
                f.write(f"- `{test_result.test_name}`\n")
                if test_result.error:
                    f.write(f"  - Error: {test_result.error}\n")
                elif test_result.stderr:
                    # Try to extract first line of stderr as error summary
                    first_line = test_result.stderr.split('\n')[0].strip()
                    if first_line:
                        f.write(f"  - Error: {first_line}\n")
            f.write("\n")
        
        # Lint result
        if report.lint_result:
            f.write("### PRD Lint Results\n\n")
            if report.lint_result.passed:
                f.write("✅ **PRD Lint: PASSED**\n")
            else:
                f.write("❌ **PRD Lint: FAILED**\n")
                if report.lint_result.error:
                    f.write(f"- Error: {report.lint_result.error}\n")
                elif report.lint_result.stderr:
                    first_line = report.lint_result.stderr.split('\n')[0].strip()
                    if first_line:
                        f.write(f"- Error: {first_line}\n")
            f.write("\n")
        
        # Detailed results
        f.write("## Detailed Results\n\n")
        
        for test_result in report.test_results:
            f.write(f"### `{test_result.test_name}`\n\n")
            f.write(f"- **Status:** {'✅ PASS' if test_result.passed else '❌ FAIL'}\n")
            f.write(f"- **Exit Code:** {test_result.exit_code}\n")
            f.write(f"- **Duration:** {test_result.duration_seconds:.1f}s\n")
            f.write(f"- **Test Path:** `{test_result.test_path}`\n")
            
            if test_result.stdout:
                f.write("- **Output:**\n```\n")
                f.write(test_result.stdout)
                f.write("\n```\n")
            
            if test_result.error:
                f.write(f"- **Error:** {test_result.error}\n")
            
            f.write("\n")
        
        # Lint detailed result
        if report.lint_result:
            f.write(f"### `{report.lint_result.test_name}`\n\n")
            f.write(f"- **Status:** {'✅ PASS' if report.lint_result.passed else '❌ FAIL'}\n")
            f.write(f"- **Exit Code:** {report.lint_result.exit_code}\n")
            f.write(f"- **Duration:** {report.lint_result.duration_seconds:.1f}s\n")
            f.write(f"- **Test Path:** `{report.lint_result.test_path}`\n")
            
            if report.lint_result.stdout:
                f.write("- **Output:**\n```\n")
                f.write(report.lint_result.stdout)
                f.write("\n```\n")
            
            if report.lint_result.error:
                f.write(f"- **Error:** {report.lint_result.error}\n")
            
            f.write("\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point for the PRD acceptance test runner."""
    parser = argparse.ArgumentParser(description="Run PRD acceptance tests on a session directory.")
    parser.add_argument("session_dir", type=Path, help="Session directory to test")
    parser.add_argument("--output", "-o", type=Path, default=Path("PRD-ACCEPTANCE-REPORT.md"),
                       help="Output report path (default: PRD-ACCEPTANCE-REPORT.md)")
    parser.add_argument("--timeout", "-t", type=int, default=30,
                       help="Timeout per test in seconds (default: 30)")
    parser.add_argument("--lint-timeout", type=int, default=60,
                       help="Timeout for lint in seconds (default: 60)")
    parser.add_argument("--skip-lint", action="store_true",
                       help="Skip running the PRD lint")
    args = parser.parse_args()
    
    # Validate session directory
    if not args.session_dir.exists():
        print(f"Error: Session directory not found: {args.session_dir}", file=sys.stderr)
        return 1
    
    if not args.session_dir.is_dir():
        print(f"Error: Not a directory: {args.session_dir}", file=sys.stderr)
        return 1
    
    print(f"\nRunning PRD acceptance tests on {args.session_dir}...\n")
    
    # Find all PRD tests
    bin_dir = Path(__file__).parent
    test_paths = find_prd_tests(bin_dir)
    
    if not test_paths:
        print("Error: No PRD tests found in bin/ directory", file=sys.stderr)
        return 1
    
    print(f"Found {len(test_paths)} PRD tests\n")
    
    # Run all tests
    test_results = []
    for test_path in test_paths:
        test_name = test_path.stem
        print(f"Running {test_name}...", end=" ", flush=True)
        result = run_test(test_path, args.session_dir, args.timeout)
        test_results.append(result)
        print("✅" if result.passed else "❌")
    
    # Run lint (unless skipped)
    lint_result = None
    if not args.skip_lint:
        print(f"\nRunning lint_v3_prd_grounded...", end=" ", flush=True)
        lint_result = run_lint(args.session_dir, args.lint_timeout)
        print("✅" if lint_result.passed else "❌")
    
    # Create report
    report = AcceptanceReport(
        session_dir=args.session_dir,
        timestamp=datetime.now(),
        test_results=test_results,
        lint_result=lint_result
    )
    
    # Generate report
    generate_markdown_report(report, args.output)
    
    print(f"\nReport written to: {args.output}")
    print(f"Summary: {report.passed_tests}/{report.total_tests} tests passed ({report.pass_percentage:.1f}%)")
    
    return 0 if report.passed_tests == report.total_tests else 1


if __name__ == "__main__":
    sys.exit(main())