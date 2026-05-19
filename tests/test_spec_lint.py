"""Tests for bin/spec_lint.py — covers all 6 cases the D21 spec promised."""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_LINT = REPO_ROOT / "bin" / "spec_lint.py"

# Load spec_lint as a module without making bin/ a package.
_spec = importlib.util.spec_from_file_location("spec_lint", SPEC_LINT)
spec_lint = importlib.util.module_from_spec(_spec)
sys.modules["spec_lint"] = spec_lint
_spec.loader.exec_module(spec_lint)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_valid_modern_spec_passes(tmp_path):
    p = _write(
        tmp_path,
        "Dxx_valid.md",
        """\
        ---
        task_id: Dxx
        project: oyster
        priority: 1
        estimated_minutes: 30
        modifies:
          - some/file.py
        executor: glm
        ---
        # Body
        - [ ] criterion one
        - [ ] criterion two
        - [ ] criterion three
        """,
    )
    res = spec_lint.lint_file(p)
    assert res.ok, f"expected pass; got failures={res.failures}"


def test_missing_front_matter_passes_with_warning(tmp_path):
    """Legacy specs (no front matter) are lenient — warn, don't fail."""
    p = _write(
        tmp_path,
        "Dxx_legacy.md",
        """\
        # Old spec
        Did some real work.
        """,
    )
    res = spec_lint.lint_file(p)
    assert res.ok, f"legacy specs should pass with warning, got {res.failures}"
    assert any("no YAML front-matter" in w for w in res.warnings)


def test_modern_spec_missing_required_keys_fails(tmp_path):
    p = _write(
        tmp_path,
        "Dxx_bad_fm.md",
        """\
        ---
        task_id: Dxx
        project: oyster
        ---
        - [ ] one
        - [ ] two
        - [ ] three
        """,
    )
    res = spec_lint.lint_file(p)
    assert not res.ok
    assert any("missing required keys" in f for f in res.failures)


def test_modern_spec_too_few_acceptance_criteria_fails(tmp_path):
    p = _write(
        tmp_path,
        "Dxx_too_few_ac.md",
        """\
        ---
        task_id: Dxx
        project: oyster
        priority: 1
        estimated_minutes: 30
        modifies:
          - x.py
        executor: glm
        ---
        - [ ] only one
        """,
    )
    res = spec_lint.lint_file(p)
    assert not res.ok
    assert any("acceptance criteria" in f for f in res.failures)


def test_banned_pattern_in_body_fails(tmp_path):
    p = _write(
        tmp_path,
        "Dxx_banned.md",
        """\
        ---
        task_id: Dxx
        project: oyster
        priority: 1
        estimated_minutes: 30
        modifies:
          - x.py
        executor: glm
        ---
        Implementation note: just write a placeholder for now.
        - [ ] one
        - [ ] two
        - [ ] three
        """,
    )
    res = spec_lint.lint_file(p)
    assert not res.ok
    assert any("banned pattern" in f for f in res.failures)


def test_banned_pattern_in_donot_section_passes(tmp_path):
    p = _write(
        tmp_path,
        "Dxx_donot.md",
        """\
        ---
        task_id: Dxx
        project: oyster
        priority: 1
        estimated_minutes: 30
        modifies:
          - x.py
        executor: glm
        ---
        - [ ] one
        - [ ] two
        - [ ] three
        ## 不要做
        - 不要 ship a placeholder
        - 不要 leave TODOs
        """,
    )
    res = spec_lint.lint_file(p)
    assert res.ok, f"banned in 不要做 should pass; got {res.failures}"


def test_iron_law_waived_passes(tmp_path):
    p = _write(
        tmp_path,
        "Dxx_waived.md",
        """\
        ---
        task_id: Dxx
        project: oyster
        priority: 1
        estimated_minutes: 30
        modifies:
          - x.py
        executor: glm
        iron_law_waived: discusses iron-law classifier behaviour
        ---
        Body talks about placeholder vs real classification freely.
        - [ ] one
        - [ ] two
        - [ ] three
        """,
    )
    res = spec_lint.lint_file(p)
    assert res.ok, f"waived should pass; got {res.failures}"


def test_lint_dir_exit_code_on_failures(tmp_path, capsys):
    # one passing + one failing
    _write(
        tmp_path,
        "good.md",
        """\
        ---
        task_id: Dxx
        project: oyster
        priority: 1
        estimated_minutes: 30
        modifies:
          - x.py
        executor: glm
        ---
        - [ ] one
        - [ ] two
        - [ ] three
        """,
    )
    _write(
        tmp_path,
        "bad.md",
        """\
        ---
        task_id: Dyy
        ---
        - [ ] only one
        """,
    )
    rc = spec_lint.lint_dir(tmp_path)
    assert rc == 1
