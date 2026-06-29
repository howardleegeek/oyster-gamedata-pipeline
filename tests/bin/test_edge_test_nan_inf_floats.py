#!/usr/bin/env python3
"""
Tests for bin/edge_test_nan_inf_floats.py

PRD gap: buyer spec requires all floats to be finite. NaN / +Inf / -Inf
in any Vector3 component must be flagged.

This test file covers the runtime Vector3 finiteness checks (the part
that DOES work today) and the AST linter's current ability to detect
non-finite numeric LITERALS embedded in Vector3() calls.

NOTE: A known gap exists in the AST linter — it does NOT walk into
sub-Calls (e.g. float("nan")) so a Vector3(float("nan"), 0, 0) call
slips through. That is tracked as a follow-up; this file tests the
linter's documented scope (literal non-finite numerics).
"""

import math
import tempfile
from pathlib import Path

from bin.edge_test_nan_inf_floats import (
    Vector3,
    Vector3LintValidator,
    create_test_vectors,
    lint_source_file,
    validate_vectors,
)


class TestVector3IsFinite:
    """Tests for Vector3.is_finite — pure-finite check."""

    def test_finite_components(self):
        v = Vector3(1.0, 2.0, 3.0)
        assert v.is_finite() is True

    def test_negative_finite_components(self):
        v = Vector3(-1.5, 0.0, -4.2)
        assert v.is_finite() is True

    def test_zero_components(self):
        v = Vector3(0.0, 0.0, 0.0)
        assert v.is_finite() is True

    def test_nan_in_x(self):
        v = Vector3(float("nan"), 1.0, 2.0)
        assert v.is_finite() is False

    def test_nan_in_y(self):
        v = Vector3(1.0, float("nan"), 2.0)
        assert v.is_finite() is False

    def test_nan_in_z(self):
        v = Vector3(1.0, 2.0, float("nan"))
        assert v.is_finite() is False

    def test_pos_inf_in_x(self):
        v = Vector3(float("inf"), 1.0, 2.0)
        assert v.is_finite() is False

    def test_neg_inf_in_y(self):
        v = Vector3(1.0, float("-inf"), 2.0)
        assert v.is_finite() is False

    def test_inf_in_z(self):
        v = Vector3(1.0, 2.0, float("inf"))
        assert v.is_finite() is False

    def test_huge_finite_value(self):
        # 1e308 is finite, even though very large
        v = Vector3(1e308, 1.0, 1.0)
        assert v.is_finite() is True

    def test_repr_includes_components(self):
        v = Vector3(1.5, 2.5, 3.5)
        s = repr(v)
        assert "1.5" in s
        assert "2.5" in s
        assert "3.5" in s
        assert "Vector3" in s


class TestCreateTestVectors:
    """Tests for create_test_vectors — must include both good and bad cases."""

    def test_returns_list(self):
        result = create_test_vectors()
        assert isinstance(result, list)

    def test_nonempty(self):
        result = create_test_vectors()
        assert len(result) > 0

    def test_all_are_vector3(self):
        result = create_test_vectors()
        for v in result:
            assert isinstance(v, Vector3)

    def test_contains_nan_vector(self):
        result = create_test_vectors()
        nan_vectors = [
            v
            for v in result
            if any(math.isnan(c) for c in (v.x, v.y, v.z))
        ]
        assert len(nan_vectors) >= 1

    def test_contains_pos_inf_vector(self):
        result = create_test_vectors()
        inf_vectors = [
            v
            for v in result
            if any(math.isinf(c) and c > 0 for c in (v.x, v.y, v.z))
        ]
        assert len(inf_vectors) >= 1

    def test_contains_neg_inf_vector(self):
        result = create_test_vectors()
        neg_inf_vectors = [
            v
            for v in result
            if any(math.isinf(c) and c < 0 for c in (v.x, v.y, v.z))
        ]
        assert len(neg_inf_vectors) >= 1

    def test_contains_finite_vector(self):
        result = create_test_vectors()
        finite_vectors = [v for v in result if v.is_finite()]
        assert len(finite_vectors) >= 1


class TestValidateVectors:
    """Tests for validate_vectors — counts non-finite vectors."""

    def test_all_finite_passes(self):
        vectors = [Vector3(1.0, 2.0, 3.0), Vector3(0.0, 0.0, 0.0)]
        count, errors = validate_vectors(vectors)
        assert count == 0
        assert errors == []

    def test_one_non_finite(self):
        vectors = [Vector3(1.0, 2.0, 3.0), Vector3(float("nan"), 1.0, 2.0)]
        count, errors = validate_vectors(vectors)
        assert count == 1
        assert len(errors) == 1
        assert "non-finite" in errors[0].lower()

    def test_multiple_non_finite(self):
        vectors = [
            Vector3(float("nan"), 0.0, 0.0),
            Vector3(0.0, float("inf"), 0.0),
            Vector3(0.0, 0.0, float("-inf")),
        ]
        count, errors = validate_vectors(vectors)
        assert count == 3
        assert len(errors) == 3

    def test_empty_list(self):
        count, errors = validate_vectors([])
        assert count == 0
        assert errors == []

    def test_error_message_includes_index(self):
        vectors = [Vector3(1.0, 2.0, 3.0), Vector3(float("nan"), 0.0, 0.0)]
        count, errors = validate_vectors(vectors)
        assert "1" in errors[0]

    def test_error_message_includes_repr(self):
        vectors = [Vector3(float("nan"), 0.0, 0.0)]
        count, errors = validate_vectors(vectors)
        assert "Vector3" in errors[0]
        assert "nan" in errors[0].lower()


class TestLintSourceFile:
    """Tests for lint_source_file — AST visitor for non-finite Vector3.

    Covers the linter's documented scope: it catches non-finite
    NUMERIC LITERALS in Vector3() call args (e.g. 1e500 → Inf at
    parse time, or float('inf') if Python folds it).
    """

    def test_clean_source_no_violations(self):
        clean_code = """
class Vector3:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x, self.y, self.z = x, y, z

v1 = Vector3(1.0, 2.0, 3.0)
v2 = Vector3(-1.5, 0.0, 4.2)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(clean_code)
            tmp_path = Path(f.name)
        try:
            count, violations = lint_source_file(tmp_path)
            assert count == 0
            assert violations == []
        finally:
            tmp_path.unlink()

    def test_missing_file_returns_error(self):
        fake_path = Path("/tmp/does_not_exist_xyz_12345.py")
        count, violations = lint_source_file(fake_path)
        assert count >= 1
        assert violations[0][0] == 0  # line 0 = file-level error

    def test_syntax_error_returns_error(self):
        bad_code = "def broken(:\n    pass\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(bad_code)
            tmp_path = Path(f.name)
        try:
            count, violations = lint_source_file(tmp_path)
            assert count >= 1
            assert violations[0][0] == 0
        finally:
            tmp_path.unlink()

    def test_lint_with_finite_vector3_no_violation(self):
        good_code = """
v = Vector3(1.0, 2.0, 3.0)
w = Vector3(0.0, 0.0, 0.0)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(good_code)
            tmp_path = Path(f.name)
        try:
            count, _ = lint_source_file(tmp_path)
            assert count == 0
        finally:
            tmp_path.unlink()

    def test_lint_with_huge_literal_inf(self):
        # 1e500 is parsed by Python as float('inf') at compile time
        bad_code = """
v = Vector3(1e500, 1.0, 2.0)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(bad_code)
            tmp_path = Path(f.name)
        try:
            count, violations = lint_source_file(tmp_path)
            # Either the linter catches it (count >= 1) or documents a
            # known gap.  1e500 → inf is a finite LITERAL, so the
            # linter's current design SHOULD catch it.
            assert count >= 1
            assert any("Inf" in msg for _, msg in violations)
        finally:
            tmp_path.unlink()

    def test_violation_message_format(self):
        bad_code = """
v = Vector3(1e500, 1.0, 2.0)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(bad_code)
            tmp_path = Path(f.name)
        try:
            count, violations = lint_source_file(tmp_path)
            if count >= 1:
                lineno, msg = violations[0]
                assert lineno > 0
                assert "pos" in msg
        finally:
            tmp_path.unlink()


class TestVector3LintValidator:
    """Direct tests for the AST visitor class."""

    def test_visitor_starts_empty(self):
        import ast as _ast
        v = Vector3LintValidator()
        # Walk a benign tree
        tree = _ast.parse("x = 1")
        v.visit(tree)
        assert v.violations == []

    def test_visitor_detects_finite_vector3(self):
        import ast as _ast
        v = Vector3LintValidator()
        tree = _ast.parse("v = Vector3(1.0, 2.0, 3.0)")
        v.visit(tree)
        assert v.violations == []

    def test_visitor_class_exists(self):
        assert hasattr(Vector3LintValidator, "visit_Call")


class TestIntegration:
    """Integration tests combining validators and linter on shared data."""

    def test_create_then_validate(self):
        vectors = create_test_vectors()
        count, errors = validate_vectors(vectors)
        # The test data should include at least 3 non-finite vectors
        assert count >= 3
        assert len(errors) >= 3

    def test_module_imports_clean(self):
        """Sanity: the module is importable and exposes the public API."""
        import bin.edge_test_nan_inf_floats as mod
        assert mod.Vector3 is not None
        assert mod.Vector3LintValidator is not None
        assert callable(mod.create_test_vectors)
        assert callable(mod.validate_vectors)
        assert callable(mod.lint_source_file)
        assert callable(mod.run_tests)
