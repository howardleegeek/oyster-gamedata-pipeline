"""Tests for scripts/iron_law_check.sh — mock git diff to verify all checks.

Each test creates a temporary git repo, stages files, and runs the shell
script with IRON_LAW_DIFF_BASE set to the base commit.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "iron_law_check.sh"


def _run_script(
    repo: Path, diff_base: str = "HEAD~1", env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    """Run iron_law_check.sh inside *repo* with IRON_LAW_DIFF_BASE set."""
    env = os.environ.copy()
    env["IRON_LAW_DIFF_BASE"] = diff_base
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp: Path) -> Path:
    """Create a minimal git repo at *tmp* and return it."""
    subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True, capture_output=True)
    # Create an initial commit so HEAD~1 works
    (tmp / "dummy.txt").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp, check=True, capture_output=True)
    return tmp


# ---------------------------------------------------------------------------
# Check 1: skip/xfail without comment
# ---------------------------------------------------------------------------


def test_skip_without_comment_is_blocked():
    """Adding @pytest.mark.skip without a trailing comment should fail."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        # Add a test file with skip but no comment
        test_file = repo / "test_new.py"
        test_file.write_text("@pytest.mark.skip\ndef test_foo(): pass\n")
        subprocess.run(["git", "add", str(test_file)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add skip test"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "skip" in result.stderr.lower() or "skip/xfail" in result.stderr.lower()


def test_skip_with_comment_is_allowed():
    """Adding @pytest.mark.skip with a trailing comment should pass."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        test_file = repo / "test_new.py"
        test_file.write_text("@pytest.mark.skip  # tracked in issue #123\ndef test_foo(): pass\n")
        subprocess.run(["git", "add", str(test_file)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add skip with comment"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
        )


def test_xfail_without_comment_is_blocked():
    """Adding @pytest.mark.xfail without a trailing comment should fail."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        test_file = repo / "test_new.py"
        test_file.write_text("@pytest.mark.xfail\ndef test_bar(): pass\n")
        subprocess.run(["git", "add", str(test_file)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add xfail test"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}\nstderr: {result.stderr}"
        )


def test_xfail_with_comment_is_allowed():
    """Adding @pytest.mark.xfail with a trailing comment should pass."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        test_file = repo / "test_new.py"
        test_file.write_text(
            "@pytest.mark.xfail(reason='bug-456')  # tracked\ndef test_bar(): pass\n"
        )
        subprocess.run(["git", "add", str(test_file)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add xfail with comment"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Check 2: placeholder markers
# ---------------------------------------------------------------------------


def test_todo_real_data_is_blocked():
    """Adding '# TODO real-data' should fail."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        src_file = repo / "data.py"
        src_file.write_text("# TODO real-data: replace with live API\nDATA = []\n")
        subprocess.run(["git", "add", str(src_file)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add placeholder"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "placeholder" in result.stderr.lower()


def test_clean_file_passes():
    """A clean file with no violations should pass."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        src_file = repo / "clean.py"
        src_file.write_text("def hello():\n    return 'world'\n")
        subprocess.run(["git", "add", str(src_file)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add clean file"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Check 3: collect_ignore growth
# ---------------------------------------------------------------------------


def test_collect_ignore_grow_is_blocked():
    """Growing collect_ignore should fail."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        # Create conftest.py with existing entries
        conftest = repo / "conftest.py"
        conftest.write_text('collect_ignore = ["test_old.py"]\n')
        subprocess.run(["git", "add", str(conftest)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add conftest"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Now grow it
        conftest.write_text('collect_ignore = ["test_old.py", "test_new.py"]\n')
        subprocess.run(["git", "add", str(conftest)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "grow collect_ignore"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "collect_ignore" in result.stderr.lower() and "grew" in result.stderr.lower()


def test_collect_ignore_shrink_is_allowed():
    """Shrinking collect_ignore should pass."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        conftest = repo / "conftest.py"
        conftest.write_text('collect_ignore = ["test_a.py", "test_b.py"]\n')
        subprocess.run(["git", "add", str(conftest)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add conftest"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Shrink it
        conftest.write_text('collect_ignore = ["test_a.py"]\n')
        subprocess.run(["git", "add", str(conftest)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "shrink collect_ignore"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
        )


def test_collect_ignore_unchanged_is_allowed():
    """Unchanged collect_ignore should pass."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        conftest = repo / "conftest.py"
        conftest.write_text('collect_ignore = ["test_a.py"]\n')
        subprocess.run(["git", "add", str(conftest)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add conftest"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Make a different change (not to conftest)
        other = repo / "other.py"
        other.write_text("x = 1\n")
        subprocess.run(["git", "add", str(other)], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add other"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        result = _run_script(repo)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Check 4: empty PR
# ---------------------------------------------------------------------------


def test_empty_pr_is_blocked():
    """A PR with no commits between base and HEAD should fail."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        # Use a diff_base that equals HEAD (no commits between)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        result = _run_script(repo, diff_base=head)
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "empty" in result.stderr.lower() or "no commits" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Exit code semantics
# ---------------------------------------------------------------------------


def test_exit_0_on_clean_repo():
    """A repo with no violations exits 0."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        # Add a second commit with clean changes
        clean = repo / "clean.py"
        clean.write_text("def foo(): pass\n")
        subprocess.run(["git", "add", str(clean)], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "clean"], cwd=repo, check=True, capture_output=True)

        result = _run_script(repo)
        assert result.returncode == 0


def test_exit_1_on_violation():
    """A repo with violations exits 1."""
    with tempfile.TemporaryDirectory() as td:
        repo = _init_repo(Path(td))
        test_file = repo / "test_bad.py"
        test_file.write_text("@pytest.mark.skip\ndef test_x(): pass\n")
        subprocess.run(["git", "add", str(test_file)], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "bad"], cwd=repo, check=True, capture_output=True)

        result = _run_script(repo)
        assert result.returncode == 1


def test_exit_2_when_not_git_repo():
    """Running outside a git repo exits 2."""
    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env["IRON_LAW_DIFF_BASE"] = "HEAD~1"
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=td,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
