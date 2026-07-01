"""Tests for bin/audit_artifact_honesty.py."""

import ast
import tempfile
from pathlib import Path
from unittest.mock import patch

from bin.audit_artifact_honesty import (
    Violation,
    _body_has_abstain_string,
    _body_has_nan_or_inf_residual,
    _function_param_names,
    _is_artifact_param,
    audit,
    main,
)


class TestIsArtifactParam:
    """Tests for _is_artifact_param function."""

    def test_exact_match_manifest_path(self):
        """Exact match for manifest_path."""
        assert _is_artifact_param("manifest_path") is True

    def test_exact_match_video_path(self):
        """Exact match for video_path."""
        assert _is_artifact_param("video_path") is True

    def test_exact_match_inputs_path(self):
        """Exact match for inputs_path."""
        assert _is_artifact_param("inputs_path") is True

    def test_exact_match_depth_dir(self):
        """Exact match for depth_dir."""
        assert _is_artifact_param("depth_dir") is True

    def test_suffix_match_audio_path(self):
        """_path suffix matches audio_path."""
        assert _is_artifact_param("audio_path") is True

    def test_suffix_match_clip_dir(self):
        """_dir suffix matches clip_dir."""
        assert _is_artifact_param("clip_dir") is True

    def test_suffix_match_residual_path(self):
        """_path suffix matches residual_path."""
        assert _is_artifact_param("residual_path") is True

    def test_no_match_regular_param(self):
        """Regular parameter names return False."""
        assert _is_artifact_param("value") is False
        assert _is_artifact_param("count") is False
        assert _is_artifact_param("name") is False

    def test_no_match_internal_param(self):
        """Internal params like self/cls return False."""
        assert _is_artifact_param("self") is False
        assert _is_artifact_param("cls") is False


class TestFunctionParamNames:
    """Tests for _function_param_names function."""

    def test_simple_args(self):
        """Simple positional args."""
        code = "def f(a, b, c): pass"
        tree = ast.parse(code)
        func = tree.body[0]
        result = _function_param_names(func)
        assert set(result) == {"a", "b", "c"}

    def test_keyword_only_args(self):
        """Keyword-only args."""
        code = "def f(*, a, b): pass"
        tree = ast.parse(code)
        func = tree.body[0]
        assert _function_param_names(func) == ["a", "b"]

    def test_positional_only_args(self):
        """Positional-only args (Python 3.8+)."""
        code = "def f(a, /, b): pass"
        tree = ast.parse(code)
        func = tree.body[0]
        result = _function_param_names(func)
        assert set(result) == {"a", "b"}

    def test_mixed_args(self):
        """Mixed positional, keyword-only, and *args."""
        code = "def f(a, *args, b, **kwargs): pass"
        tree = ast.parse(code)
        func = tree.body[0]
        result = _function_param_names(func)
        assert set(result) == {"a", "b"}


class TestBodyHasAbstainString:
    """Tests for _body_has_abstain_string function."""

    def test_has_abstain_in_return_value(self):
        """Function with ABSTAIN as return value."""
        code = """
def process(manifest_path):
    if missing(manifest_path):
        return "ABSTAIN"
    return process_file(manifest_path)
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert _body_has_abstain_string(func) is True

    def test_no_abstain(self):
        """Function without ABSTAIN returns False."""
        code = """
def process(manifest_path):
    return process_file(manifest_path)
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert _body_has_abstain_string(func) is False


class TestBodyHasNanOrInfResidual:
    """Tests for _body_has_nan_or_inf_residual function."""

    def test_has_float_nan_dict(self):
        """Function with float('nan') in dict return."""
        code = """
def process(manifest_path):
    return {"residual": float("nan")}
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert _body_has_nan_or_inf_residual(func) is True

    def test_has_float_inf_dict(self):
        """Function with float('inf') in dict return."""
        code = """
def process(manifest_path):
    return {"residual": float("inf")}
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert _body_has_nan_or_inf_residual(func) is True

    def test_no_residual_return(self):
        """Function without residual NaN/inf returns False."""
        code = """
def process(manifest_path):
    return process_file(manifest_path)
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert _body_has_nan_or_inf_residual(func) is False


class TestViolation:
    """Tests for Violation dataclass."""

    def test_format_basic(self):
        """Basic format output with valid path."""
        # Use a path that's a subpath of the repo root
        v = Violation(
            file=Path("/Users/howardlee/Downloads/oyster-agent-runner/bin/test.py"),
            function="process",
            lineno=10,
            artifact_param="manifest_path",
        )
        output = v.format()
        assert "test.py:10" in output
        assert "process" in output
        assert "manifest_path" in output
        assert "ABSTAIN" in output
        assert "IL10" in output


class TestAudit:
    """Tests for audit function."""

    def test_audit_violation_without_abstain(self):
        """Function with artifact param but no ABSTAIN returns violation."""
        code = """
def process(manifest_path):
    return process_file(manifest_path)
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(code)
            f.flush()
            violations = audit(files=[Path(f.name)])

        assert len(violations) == 1
        assert violations[0].function == "process"
        assert violations[0].artifact_param == "manifest_path"

    def test_audit_no_violation_with_nan_residual(self):
        """Function with NaN residual has no violation."""
        code = """
def process(manifest_path):
    return {"residual": float("nan")}
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(code)
            f.flush()
            violations = audit(files=[Path(f.name)])

        assert len(violations) == 0

    def test_audit_no_artifact_params(self):
        """Function without artifact params has no violations."""
        code = """
def process(value, count):
    return value + count
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(code)
            f.flush()
            violations = audit(files=[Path(f.name)])

        assert len(violations) == 0

    def test_audit_empty_files_list(self):
        """Audit with empty files list returns empty."""
        violations = audit(files=[])
        assert violations == []


class TestMain:
    """Tests for main CLI function."""

    def test_main_empty_files(self):
        """Main with no files to audit exits 0."""
        with patch("bin.audit_artifact_honesty._iter_residual_files", return_value=[]):
            with patch("sys.argv", ["audit_artifact_honesty.py"]):
                exit_code = main()
        assert exit_code == 0
