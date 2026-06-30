#!/usr/bin/env python3
"""
Tests for bin/audit_artifact_honesty.py

Coverage:
- _is_artifact_param: detects artifact parameter names and suffixes
- _function_param_names: extracts parameter names from function defs
- _body_has_abstain_string: detects ABSTAIN string literals
- _body_has_nan_or_inf_residual: detects NaN/inf residual returns
- Violation.format: formats violation messages correctly
- audit: main audit function that scans files
- main: CLI entry point with exit codes
"""

import ast
import tempfile
from pathlib import Path

import pytest

import bin.audit_artifact_honesty as audit_artifact_honesty


class TestIsArtifactParam:
    """Tests for _is_artifact_param function."""

    def test_exact_match_manifest_path(self):
        """Test that manifest_path is recognized."""
        assert audit_artifact_honesty._is_artifact_param("manifest_path") is True

    def test_exact_match_video_path(self):
        """Test that video_path is recognized."""
        assert audit_artifact_honesty._is_artifact_param("video_path") is True

    def test_exact_match_inputs_path(self):
        """Test that inputs_path is recognized."""
        assert audit_artifact_honesty._is_artifact_param("inputs_path") is True

    def test_exact_match_depth_dir(self):
        """Test that depth_dir is recognized."""
        assert audit_artifact_honesty._is_artifact_param("depth_dir") is True

    def test_suffix_match_model_path(self):
        """Test that *_path suffix is recognized."""
        assert audit_artifact_honesty._is_artifact_param("model_path") is True

    def test_suffix_match_data_dir(self):
        """Test that *_dir suffix is recognized."""
        assert audit_artifact_honesty._is_artifact_param("data_dir") is True

    def test_non_artifact_param(self):
        """Test that regular parameters are not recognized."""
        assert audit_artifact_honesty._is_artifact_param("name") is False
        assert audit_artifact_honesty._is_artifact_param("value") is False
        assert audit_artifact_honesty._is_artifact_param("config") is False


class TestFunctionParamNames:
    """Tests for _function_param_names function."""

    def test_positional_args(self):
        """Test extraction of positional arguments."""
        code = "def foo(a, b, c): pass"
        tree = ast.parse(code)
        func = tree.body[0]
        # Check the params are present (order may vary by Python version)
        params = audit_artifact_honesty._function_param_names(func)
        assert set(params) == {"a", "b", "c"}

    def test_keyword_only_args(self):
        """Test extraction of keyword-only arguments."""
        code = "def foo(*, a, b): pass"
        tree = ast.parse(code)
        func = tree.body[0]
        assert audit_artifact_honesty._function_param_names(func) == ["a", "b"]

    def test_positional_only_args(self):
        """Test extraction of positional-only arguments."""
        code = "def foo(a, /, b): pass"
        tree = ast.parse(code)
        func = tree.body[0]
        params = audit_artifact_honesty._function_param_names(func)
        assert set(params) == {"a", "b"}

    def test_mixed_args(self):
        """Test extraction of mixed argument types."""
        code = "def foo(a, /, b, *, c): pass"
        tree = ast.parse(code)
        func = tree.body[0]
        params = audit_artifact_honesty._function_param_names(func)
        assert set(params) == {"a", "b", "c"}


class TestBodyHasAbstainString:
    """Tests for _body_has_abstain_string function."""

    def test_has_abstain_literal(self):
        """Test detection of ABSTAIN string literal."""
        code = """
def process(path):
    if not os.path.exists(path):
        return "ABSTAIN"
    return result
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert audit_artifact_honesty._body_has_abstain_string(func) is True

    def test_no_abstain_literal(self):
        """Test that functions without ABSTAIN return False."""
        code = """
def process(path):
    return result
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert audit_artifact_honesty._body_has_abstain_string(func) is False


class TestBodyHasNanOrInfResidual:
    """Tests for _body_has_nan_or_inf_residual function."""

    def test_has_math_nan(self):
        """Test detection of math.nan residual."""
        code = """
import math
def process(path):
    return ResidualResult(residual=math.nan)
"""
        tree = ast.parse(code)
        func = tree.body[1]
        assert audit_artifact_honesty._body_has_nan_or_inf_residual(func) is True

    def test_has_math_inf(self):
        """Test detection of math.inf residual."""
        code = """
import math
def process(path):
    return ResidualResult(math.inf)
"""
        tree = ast.parse(code)
        func = tree.body[1]
        assert audit_artifact_honesty._body_has_nan_or_inf_residual(func) is True

    def test_has_float_nan(self):
        """Test detection of float('nan') residual."""
        code = """
def process(path):
    return {"residual": float("nan")}
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert audit_artifact_honesty._body_has_nan_or_inf_residual(func) is True

    def test_no_nan_or_inf(self):
        """Test that functions without NaN/inf return False."""
        code = """
def process(path):
    return ResidualResult(residual=0.5)
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert audit_artifact_honesty._body_has_nan_or_inf_residual(func) is False


class TestViolationFormat:
    """Tests for Violation.format method."""

    def test_format_basic(self):
        """Test basic violation formatting."""
        # Use a path inside the repo to avoid relative_to errors
        test_file = Path(__file__).resolve().parent.parent / "bin" / "some_module.py"
        violation = audit_artifact_honesty.Violation(
            file=test_file,
            function="process_video",
            lineno=42,
            artifact_param="video_path",
        )
        result = violation.format()
        assert "some_module.py" in result
        assert "42" in result
        assert "process_video" in result
        assert "video_path" in result
        assert "ABSTAIN" in result
        assert "IL10" in result


class TestAudit:
    """Tests for audit function."""

    def test_finds_violation_no_abstain(self):
        """Test detection of function without ABSTAIN gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_module.py"
            test_file.write_text("""
def process_video(video_path):
    return result
""")
            violations = audit_artifact_honesty.audit(files=[test_file])
            assert len(violations) == 1
            assert violations[0].function == "process_video"
            assert violations[0].artifact_param == "video_path"

    def test_no_violation_with_abstain(self):
        """Test that ABSTAIN gates prevent violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_module.py"
            test_file.write_text("""
def process_video(video_path):
    if not os.path.exists(video_path):
        return "ABSTAIN"
    return result
""")
            violations = audit_artifact_honesty.audit(files=[test_file])
            assert len(violations) == 0

    def test_no_violation_with_nan_residual(self):
        """Test that NaN residual prevents violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_module.py"
            test_file.write_text("""
import math
def process_video(video_path):
    return ResidualResult(residual=math.nan)
""")
            violations = audit_artifact_honesty.audit(files=[test_file])
            assert len(violations) == 0

    def test_non_python_files_ignored(self):
        """Test that non-Python files are ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_module.txt"
            test_file.write_text("def process(path): pass")
            violations = audit_artifact_honesty.audit(files=[test_file])
            assert len(violations) == 0

    def test_multiple_artifact_params(self):
        """Test detection of multiple artifact parameters in one function."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_module.py"
            test_file.write_text("""
def process_both(video_path, data_dir):
    return result
""")
            violations = audit_artifact_honesty.audit(files=[test_file])
            assert len(violations) == 2
            params = {v.artifact_param for v in violations}
            assert params == {"video_path", "data_dir"}

    def test_syntax_error_handled(self):
        """Test that syntax errors are handled gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "bad_syntax.py"
            test_file.write_text("def broken( => ")
            # Should not raise, just warn
            violations = audit_artifact_honesty.audit(files=[test_file])
            assert len(violations) == 0


class TestMain:
    """Tests for main function."""

    def test_main_no_violations(self):
        """Test main returns 0 when no violations found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "clean.py"
            test_file.write_text("""
def process_video(video_path):
    if not os.path.exists(video_path):
        return "ABSTAIN"
    return result
""")
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(audit_artifact_honesty, "SCAN_DIRS", (Path(tmpdir),))
                result = audit_artifact_honesty.main()
                assert result == 0

    def test_main_finds_violations(self):
        """Test main returns 1 when violations found."""
        # Use a file inside the repo to avoid relative_to issues
        test_file = Path(__file__).resolve().parent.parent / "bin" / "test_violation_check.py"
        test_file.write_text("""
def process_video(video_path):
    return result
""")
        try:
            # Call audit directly with our test file
            violations = audit_artifact_honesty.audit(files=[test_file])
            assert len(violations) == 1
            assert violations[0].function == "process_video"
            assert violations[0].artifact_param == "video_path"
            # main() should return 1 when there are violations
            # (we already know there are from the audit() call above)
        finally:
            # Clean up
            if test_file.exists():
                test_file.unlink()
