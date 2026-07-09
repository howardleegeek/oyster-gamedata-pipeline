#!/usr/bin/env python3
"""
Cross-game integration test harness.
Enumerates registered environments, runs 30s mock trajectory, and lints.
Gates new game integrations.
"""

import argparse
import ast
import importlib
import inspect
import json
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Any


class TestHarness:
    """Cross-game integration test harness."""
    
    def __init__(self, verbose: bool = False, timeout: int = 30):
        self.verbose = verbose
        self.timeout = timeout
        self.results = []
        self.start = datetime.now()
    
    def log(self, msg: str, level: str = "INFO") -> None:
        """Print a timestamped log message to stderr.

        Only prints when verbose mode is enabled or the level is
        ERROR/WARNING.

        Args:
            msg: The log message text.
            level: Log severity level. Defaults to "INFO".
        """
        if self.verbose or level in ("ERROR", "WARNING"):
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] [{level}] {msg}", file=sys.stderr)
    
    def discover_envs(self) -> List[str]:
        """Discover registered environments."""
        self.log("Discovering environments...")
        
        # Check common paths
        paths = ["environments", "envs", "games", "src/envs"]
        found = []
        
        for path in paths:
            if os.path.isdir(path):
                init = os.path.join(path, "__init__.py")
                if os.path.exists(init):
                    found.append(path)
                    self.log(f"Found: {path}")
        
        # Check for RL libraries
        if not found:
            for lib in ["gym", "pettingzoo", "gymnasium"]:
                try:
                    importlib.import_module(lib)
                    found.append(f"{lib}_envs")
                except ImportError:
                    pass
        
        return found
    
    def validate_env(self, env_id: str) -> Dict[str, Any]:
        """Validate single environment."""
        self.log(f"Validating: {env_id}")
        
        result = {
            "env": env_id,
            "valid": False,
            "errors": [],
            "warnings": [],
            "tests": 0,
            "passed": 0,
            "time": 0.0,
        }
        
        start = time.time()
        
        try:
            # Test import
            result["tests"] += 1
            try:
                if "." in env_id:
                    mod, cls = env_id.rsplit(".", 1)
                    m = importlib.import_module(mod)
                    env_cls = getattr(m, cls)
                else:
                    m = importlib.import_module(env_id)
                    env_cls = None
                result["passed"] += 1
            except Exception as e:
                result["errors"].append(f"Import: {e}")
                return result
            
            # Test methods
            if env_cls and inspect.isclass(env_cls):
                result["tests"] += 1
                required = ["reset", "step", "render", "close"]
                missing = [m for m in required if not hasattr(env_cls, m)]
                if not missing:
                    result["passed"] += 1
                else:
                    result["errors"].append(f"Missing: {missing}")
            
            # Mock trajectory
            result["tests"] += 1
            try:
                self._mock_trajectory(env_id)
                result["passed"] += 1
            except Exception as e:
                result["errors"].append(f"Trajectory: {e}")
            
            # Lint check
            result["tests"] += 1
            issues = self._lint_check(env_id)
            if issues:
                result["warnings"].extend(issues)
            result["passed"] += 1
            
            result["valid"] = len(result["errors"]) == 0
            
        except Exception as e:
            result["errors"].append(f"Unexpected: {e}")
        
        result["time"] = time.time() - start
        return result
    
    def _mock_trajectory(self, env_id: str):
        """Run 30s mock trajectory."""
        self.log(f"Running trajectory for {env_id}")
        
        steps = 100
        step_time = 0.3  # 30s total
        
        for i in range(steps):
            if self.verbose and i % 20 == 0:
                self.log(f"  Step {i+1}/{steps}")
            time.sleep(step_time)
            
            if i * step_time > self.timeout:
                raise TimeoutError(f"Exceeded {self.timeout}s timeout")
        
        self.log(f"Done: {env_id}")
    
    def _lint_check(self, env_id: str) -> List[str]:
        """Check for linting issues."""
        issues = []
        
        # Try to find source file
        base = env_id.replace(".", "/")
        candidates = [
            f"{base}.py",
            f"src/{base}.py",
            f"environments/{base}.py",
        ]
        
        for cand in candidates:
            if os.path.exists(cand):
                try:
                    with open(cand, 'r') as f:
                        content = f.read()
                    
                    # Check syntax
                    try:
                        ast.parse(content)
                    except SyntaxError as e:
                        issues.append(f"Syntax: {e}")
                    
                    # Check for issues
                    for i, line in enumerate(content.split('\n'), 1):
                        if 'print(' in line and line.strip().startswith('print('):
                            issues.append(f"Line {i}: print()")
                        if any(x in line for x in ['/tmp/', 'C:\\', 'API_KEY', 'SECRET']):
                            issues.append(f"Line {i}: Suspicious")
                    
                except Exception as e:
                    issues.append(f"Read error: {e}")
                break
        
        return issues
    
    def run_tests(self) -> bool:
        """Run all tests."""
        self.log("Starting test harness")
        
        envs = self.discover_envs()
        
        if not envs:
            self.log("No envs found", "WARNING")
            self.results.append({
                "env": "none", "valid": False, "errors": ["No envs"],
                "warnings": [], "tests": 0, "passed": 0, "time": 0.0
            })
            return False
        
        self.log(f"Testing {len(envs)} env(s)")
        
        all_ok = True
        for env in envs:
            res = self.validate_env(env)
            self.results.append(res)
            
            if res["valid"]:
                self.log(f"✓ {env}: PASSED ({res['passed']}/{res['tests']})")
            else:
                self.log(f"✗ {env}: FAILED", "ERROR")
                for err in res["errors"]:
                    self.log(f"  - {err}", "ERROR")
                all_ok = False
            
            for warn in res["warnings"]:
                self.log(f"  ⚠ {warn}", "WARNING")
        
        return all_ok
    
    def get_report(self) -> Dict[str, Any]:
        """Generate report."""
        passed = [r for r in self.results if r["valid"]]
        failed = [r for r in self.results if not r["valid"]]
        
        return {
            "timestamp": self.start.isoformat(),
            "duration": (datetime.now() - self.start).total_seconds(),
            "envs_tested": len(self.results),
            "envs_passed": len(passed),
            "envs_failed": len(failed),
            "total_tests": sum(r["tests"] for r in self.results),
            "passed_tests": sum(r["passed"] for r in self.results),
            "failed_envs": [r["env"] for r in failed],
            "results": self.results,
        }
    
    def print_summary(self) -> None:
        """Print a formatted summary of test results to stdout.

        Retrieves the test report and displays environment counts,
        pass/fail statistics, and any failed environment names.
        """
        r = self.get_report()
        
        print("\n" + "=" * 50)
        print("CROSS-GAME TEST HARNESS")
        print("=" * 50)
        
        print(f"\nTime: {r['timestamp']}")
        print(f"Duration: {r['duration']:.1f}s")
        print(f"\nEnvironments: {r['envs_tested']} tested, "
              f"{r['envs_passed']} passed, {r['envs_failed']} failed")
        print(f"Tests: {r['passed_tests']}/{r['total_tests']} passed")
        
        if r['failed_envs']:
            print(f"\nFailed:")
            for env in r['failed_envs']:
                print(f"  - {env}")
        
        print("\n" + "=" * 50)
        if r['envs_failed'] == 0:
            print("✓ ALL PASSED")
        else:
            print("✗ FAILURES")
        print("=" * 50)


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for the cross-game integration test harness.

    Args:
        argv: Command-line arguments. If None, uses sys.argv.

    Returns:
        Exit code: 0 on success, 1 on test failures, 2 on usage error,
            130 on interrupt.
    """
    parser = argparse.ArgumentParser(
        description="Cross-game integration test harness"
    )
    
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="Verbose output")
    parser.add_argument("-t", "--timeout", type=int, default=30,
                       help="Timeout per env (default: 30s)")
    parser.add_argument("--json", type=str,
                       help="Output JSON report")
    parser.add_argument("--no-summary", action="store_true",
                       help="Skip summary")
    
    args = parser.parse_args(argv)
    
    if args.timeout <= 0:
        print("Error: Timeout must be positive", file=sys.stderr)
        return 2
    
    try:
        harness = TestHarness(verbose=args.verbose, timeout=args.timeout)
        ok = harness.run_tests()
        
        if args.json:
            try:
                with open(args.json, 'w') as f:
                    json.dump(harness.get_report(), f, indent=2, default=str)
                print(f"Report: {args.json}")
            except Exception as e:
                print(f"JSON error: {e}", file=sys.stderr)
        
        if not args.no_summary:
            harness.print_summary()
        
        return 0 if ok else 1
        
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())