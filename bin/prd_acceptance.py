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
    
    # Tests that take --data-dir argument
    data_dir_tests = [
        "prd_test_route_type_distribution",
        "prd_test_depth_6fps_alignment",
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
    
    elif test_name in data_dir_tests:
        # These tests take --data-dir argument
        args.extend(["--data-dir", str(session_dir)])
    
    elif test_name in clips_file_tests:
        # These tests take --clips-file argument
        clips_file = session_dir / "clips.json"
        if clips_file.exists():
            args.extend(["--clips-file", str(clips_file)])
        else:
            args.extend(["--clips-file", str(clips_file)])  # Will fail gracefully
    
    elif test_name == depth_alignment_test:
        # This test needs --video-dir and --depth-dir
        video_dir = session_dir / "video_frames"
        depth_dir = session_dir / "depth"
        args.extend(["--video-dir", str(video_dir)])
        args.extend(["--depth-dir", str(depth_dir)])
    
    elif test_name == "prd_test_240_clip_cap":
        # Takes --clips-dir argument
        args.extend(["--clips-dir", str(session_dir)])
    
    elif test_name == "prd_test_30min_scene_cap":
        # Takes --scene-dir argument
        args.extend(["--scene-dir", str(session_dir)])
    
    elif test_name == "prd_test_action_per_second":
        # Takes --inputs-file argument
        inputs_file = session_dir / "inputs.jsonl"
        if not inputs_file.exists():
            inputs_file = session_dir / "action_camera.json"
        args.extend(["--inputs-file", str(inputs_file)])
    
    elif test_name == "prd_test_speed_units_mps":
        # Takes --speeds-file argument
        args.extend(["--speeds-file", str(session_dir / "speeds.json")])
    
    elif test_name == "prd_test_stationary_threshold":
        # Takes --positions-file argument
        args.extend(["--positions-file", str(session_dir / "positions.json")])
    
    elif test_name == "prd_test_left_hand_coordinates":
        # Takes --coords-file argument
        args.extend(["--coords-file", str(session_dir / "coordinates.json")])
    
    elif test_name == "prd_test_metric_units_meters":
        # Takes --camera-file argument
        args.extend(["--camera-file", str(session_dir / "action_camera.json")])
    
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
            [sys.executable, str(lint_path), str(session_dir)],
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
        for result in report.test_results:
            status = "✅ PASS" if result.passed else "❌ FAIL"
            f.write(f"| `{result.test_name}` | {status} | {result.duration_seconds:.1f}s | {result.exit_code} |\n")
        
        # Add lint result
        if report.lint_result:
            status = "✅ PASS" if report.lint_result.passed else "❌ FAIL"
            f.write(f"| `{report.lint_result.test_name}` | {status} | {report.lint_result.duration_seconds:.1f}s | {report.lint_result.exit_code} |\n")
        
        f.write("\n## Customer-Shareable Summary\n\n")
        f.write(f"**Overall Acceptance:** {report.pass_percentage:.1f}%\n\n")
        
        # Passing tests
        passing = [r for r in report.test_results if r.passed]
        if report.lint_result and report.lint_result.passed:
            passing.append(report.lint_result)
        
        if passing:
            f.write("### ✅ Passing Tests\n\n")
            for result in passing:
                f.write(f"- `{result.test_name}`\n")
        
        # Failing tests
        failing = [r for r in report.test_results if not r.passed]
        if report.lint_result and not report.lint_result.passed:
            failing.append(report.lint_result)
        
        if failing:
            f.write("\n### ❌ Failing Tests\n\n")
            for result in failing:
                f.write(f"- `{result.test_name}`\n")
                if result.error:
                    f.write(f"  - Error: {result.error}\n")
                elif result.stderr:
                    # Include first line of stderr
                    first_line = result.stderr.split("\n")[0][:100]
                    f.write(f"  - Error: {first_line}\n")
        
        f.write("\n---\n\n")
        f.write("*Generated by prd_acceptance.py*\n")


def generate_json_report(report: AcceptanceReport, output_path: Path) -> None:
    """Generate a JSON report from acceptance test results."""
    data = {
        "session_dir": str(report.session_dir),
        "timestamp": report.timestamp.isoformat(),
        "overall_score": report.pass_percentage,
        "passed_tests": report.passed_tests,
        "total_tests": report.total_tests,
        "test_results": [
            {
                "test_name": r.test_name,
                "passed": r.passed,
                "exit_code": r.exit_code,
                "duration_seconds": r.duration_seconds,
                "error": r.error,
            }
            for r in report.test_results
        ],
        "lint_result": None,
    }
    
    if report.lint_result:
        data["lint_result"] = {
            "test_name": report.lint_result.test_name,
            "passed": report.lint_result.passed,
            "exit_code": report.lint_result.exit_code,
            "duration_seconds": report.lint_result.duration_seconds,
            "error": report.lint_result.error,
        }
    
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)


def run_acceptance_tests(session_dir: Path, timeout: int = 30) -> AcceptanceReport:
    """Run all PRD acceptance tests on a session directory."""
    bin_dir = Path(__file__).parent
    
    report = AcceptanceReport(
        session_dir=session_dir,
        timestamp=datetime.now(),
    )
    
    # Find and run all PRD tests
    test_files = find_prd_tests(bin_dir)
    
    print(f"Running {len(test_files)} PRD tests on {session_dir}...")
    
    for test_file in test_files:
        print(f"  Running {test_file.name}...", end=" ", flush=True)
        result = run_test(test_file, session_dir, timeout=timeout)
        report.test_results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(status)
    
    # Run lint
    print("Running lint_v3_prd_grounded...", end=" ", flush=True)
    lint_result = run_lint(session_dir, timeout=timeout * 2)
    report.lint_result = lint_result
    status = "PASS" if lint_result.passed else "FAIL"
    print(status)
    
    return report


def main(argv: List[str] = None) -> int:
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
        default=None,
        help="Output report path (default: session_dir/PRD-ACCEPTANCE-REPORT.md)"
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Also output JSON report to this path"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds for each test (default: 30)"
    )
    
    args = parser.parse_args(argv)
    
    if not args.session_dir.exists():
        print(f"Error: Session directory not found: {args.session_dir}")
        return 1
    
    # Run tests
    report = run_acceptance_tests(args.session_dir, timeout=args.timeout)
    
    # Generate report
    output_path = args.output or (args.session_dir / "PRD-ACCEPTANCE-REPORT.md")
    generate_markdown_report(report, output_path)
    print(f"\nReport written to: {output_path}")
    
    if args.json:
        generate_json_report(report, args.json)
        print(f"JSON report written to: {args.json}")
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"PRD Acceptance Test Summary")
    print(f"{'='*60}")
    print(f"Session: {args.session_dir}")
    print(f"Tests: {report.passed_tests}/{report.total_tests} passed ({report.pass_percentage:.1f}%)")
    print(f"{'='*60}")
    
    # Return non-zero if any tests failed
    return 0 if report.failed_tests == 0 else 1


if __name__ == "__main__":
    sys.exit(main())