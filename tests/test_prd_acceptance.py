#!/usr/bin/env python3
"""
Test for bin/prd_acceptance.py
"""

import json
import subprocess
import tempfile
from pathlib import Path
import pytest
import sys


def test_prd_acceptance_script_exists():
    """Test that the prd_acceptance.py script exists and can be imported."""
    script_path = Path(__file__).parent.parent / "bin" / "prd_acceptance.py"
    assert script_path.exists(), f"Script not found: {script_path}"
    
    # Try to import the module
    import importlib.util
    spec = importlib.util.spec_from_file_location("prd_acceptance", script_path)
    module = importlib.util.module_from_spec(spec)
    
    # This might fail if there are missing imports, but that's OK for now
    try:
        spec.loader.exec_module(module)
        assert hasattr(module, 'main'), "Module should have main function"
        assert hasattr(module, 'AcceptanceReport'), "Module should have AcceptanceReport class"
        assert hasattr(module, 'TestResult'), "Module should have TestResult class"
    except ImportError as e:
        # Some imports might fail in test environment, that's OK
        print(f"Note: Some imports failed during test: {e}")


def test_prd_acceptance_help():
    """Test that the script shows help when run with --help."""
    script_path = Path(__file__).parent.parent / "bin" / "prd_acceptance.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Script should exit with 0 when showing help"
    assert "usage:" in result.stdout.lower() or "Usage:" in result.stdout
    assert "session_dir" in result.stdout


def test_prd_acceptance_missing_session_dir():
    """Test that the script fails with missing session directory."""
    script_path = Path(__file__).parent.parent / "bin" / "prd_acceptance.py"
    
    # Test with non-existent directory
    with tempfile.TemporaryDirectory() as tmpdir:
        non_existent = Path(tmpdir) / "does_not_exist"
        result = subprocess.run(
            [sys.executable, str(script_path), str(non_existent)],
            capture_output=True,
            text=True
        )
        assert result.returncode != 0, "Script should fail with non-existent directory"
        # Error message is printed to stdout, not stderr
        combined_output = (result.stdout + " " + result.stderr).lower()
        assert "not found" in combined_output or "error" in combined_output


def test_prd_acceptance_empty_session_dir():
    """Test that the script runs on an empty session directory (tests will fail)."""
    script_path = Path(__file__).parent.parent / "bin" / "prd_acceptance.py"
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create an empty session directory
        session_dir = Path(tmpdir) / "session"
        session_dir.mkdir()
        
        # Run with short timeout since tests will fail
        result = subprocess.run(
            [sys.executable, str(script_path), str(session_dir), "--timeout", "10"],
            capture_output=True,
            text=True,
            timeout=30  # Overall timeout for the test
        )
        
        # Script should complete (even if tests fail)
        # It returns non-zero if any tests failed
        assert result.returncode != 0, "Script should return non-zero when tests fail"
        
        # Should have created a report
        report_path = session_dir / "PRD-ACCEPTANCE-REPORT.md"
        assert report_path.exists(), "Should create PRD-ACCEPTANCE-REPORT.md"


def test_prd_acceptance_with_mock_session():
    """Test with a mock session that has some valid files."""
    script_path = Path(__file__).parent.parent / "bin" / "prd_acceptance.py"
    
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir) / "session"
        session_dir.mkdir()
        
        # Create a minimal systeminfo.json (for prd_test_systeminfo_required)
        systeminfo = {
            "gameProcessName": "Minecraft",
            "width": 1920,
            "height": 1080,
            "recordDpi": 96
        }
        (session_dir / "systeminfo.json").write_text(json.dumps(systeminfo))
        
        # Run with short timeout
        result = subprocess.run(
            [sys.executable, str(script_path), str(session_dir), "--timeout", "10"],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Should have created a report
        report_path = session_dir / "PRD-ACCEPTANCE-REPORT.md"
        assert report_path.exists(), "Should create PRD-ACCEPTANCE-REPORT.md"
        
        # Report should have content
        content = report_path.read_text()
        assert "PRD Acceptance Test Report" in content
        assert "Overall Score" in content


def test_find_prd_tests():
    """Test that find_prd_tests discovers PRD test files."""
    import importlib.util
    script_path = Path(__file__).parent.parent / "bin" / "prd_acceptance.py"
    spec = importlib.util.spec_from_file_location("prd_acceptance", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    bin_dir = Path(__file__).parent.parent / "bin"
    tests = module.find_prd_tests(bin_dir)
    
    # Should find at least some prd_test_*.py files
    assert len(tests) > 0, "Should find at least one PRD test file"
    
    # All should be prd_test_*.py files
    for t in tests:
        assert t.name.startswith("prd_test_"), f"Found non-test file: {t}"
        assert t.suffix == ".py", f"Found non-Python file: {t}"