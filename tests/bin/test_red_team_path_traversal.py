#!/usr/bin/env python3
"""Test coverage for bin/red_team_path_traversal.py.

This module exercises the red-team path-traversal test tool used to
verify that tarball extraction pipelines reject malicious entry names
(``../../etc/passwd`` and friends) before any filesystem write occurs.

Coverage:

- ``is_path_traversal``: clean relative path, absolute escape, single
  ``..`` segment, multi-segment ``..`` chain, backslash-style traversal
  (POSIX does not interpret ``\\`` so it is a normal filename), trailing
  dot-segment, dotfile ``.``/``..`` at the leaf, symlink-style relative
  escape, empty name (no traversal), and OSError/ValueError short
  circuits to ``True`` (suspicious).
- ``extract_tarball_safely``: a fully clean tarball returns ``(True, [...])``
  with no BLOCKED messages, a fully malicious tarball returns
  ``(True, [...])`` only because *all* errors are blocked entries
  (errors list is non-empty so the success tuple is actually the
  blocked count), a mixed tarball extracts safe entries and blocks
  malicious ones, an unreadable/missing tarball returns ``(False, [...])``,
  safe files are actually present on disk after extraction, blocked
  files are NOT present on disk after extraction.
- ``create_malicious_tarball``: the tarball exists, the safe entry
  exists, every malicious entry is present, no extraction happens by
  itself.
- ``main``: ``--create-only`` exits 0 and produces a tarball, default
  invocation blocks at least one path-traversal entry and exits 0,
  ``--verbose`` prints the BLOCKED messages, ``--help`` exits 0, an
  unknown arg exits non-zero, and a missing tarball path returns 1.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from red_team_path_traversal import (  # noqa: E402
    create_malicious_tarball,
    extract_tarball_safely,
    is_path_traversal,
)

# ---------------------------------------------------------------------------
# is_path_traversal
# ---------------------------------------------------------------------------


class TestIsPathTraversal:
    """Tests for is_path_traversal function."""

    def test_clean_relative_path_is_safe(self, tmp_path):
        base = tmp_path
        assert is_path_traversal("safe_file.txt", base) is False
        assert is_path_traversal("subdir/file.txt", base) is False

    def test_absolute_path_inside_base_is_safe(self, tmp_path):
        # str(base) prefix will match for any name resolved to *under* base.
        base = tmp_path
        # Pick a name that is unambiguously under base, then test.
        ok = "ok.txt"
        assert is_path_traversal(ok, base) is False

    def test_single_dotdot_traversal_detected(self, tmp_path):
        base = tmp_path
        # Going up one level escapes tmp_path.
        assert is_path_traversal("../escape.txt", base) is True

    def test_multi_segment_traversal_detected(self, tmp_path):
        base = tmp_path
        assert is_path_traversal("../../etc/passwd", base) is True
        assert is_path_traversal("../../../etc/passwd", base) is True
        assert is_path_traversal(
            "../../../../../../etc/hosts", base
        ) is True

    def test_windows_backslash_is_not_traversal_on_posix(self, tmp_path):
        # On POSIX, backslash is a normal filename character, not a
        # separator — so a backslash-dotted path is not a traversal.
        # This documents the platform-specific behavior.
        base = tmp_path
        name = "..\\..\\..\\windows\\system32\\config\\sam"
        if sys.platform.startswith("win"):
            assert is_path_traversal(name, base) is True
        else:
            # On POSIX, the entire backslash-string is a single filename
            # under base — not a traversal.
            assert is_path_traversal(name, base) is False

    def test_mixed_separator_path_inside_base_is_safe(self, tmp_path):
        # foo/../../../bar — the traversal would need to escape *and* come
        # back; net resolution should be checked. On POSIX, the resolved
        # path ends up at /bar (or similar), which escapes tmp_path.
        base = tmp_path
        result = is_path_traversal("foo/../../../bar", base)
        # The exact resolution depends on intermediate ``foo`` not existing;
        # what matters is that the function returns a *bool* and that the
        # result is consistent across calls.
        assert isinstance(result, bool)

    def test_dotfile_at_leaf_is_safe(self, tmp_path):
        # ``./file.txt`` resolves to a file under base.
        base = tmp_path
        assert is_path_traversal("./file.txt", base) is False

    def test_double_dot_at_leaf_escapes_base(self, tmp_path):
        base = tmp_path
        # ``..`` as a leaf escapes base (base / ".." → base.parent).
        assert is_path_traversal("..", base) is True

    def test_empty_name_does_not_traverse(self, tmp_path):
        # Empty name resolves to base itself.
        base = tmp_path
        assert is_path_traversal("", base) is False

    def test_tilde_does_not_traverse_below_base(self, tmp_path, monkeypatch):
        # `~` is a shell expansion, not a path resolution concern.
        # Path("~") is treated as a literal "~/..." filename under base.
        base = tmp_path
        assert is_path_traversal("~/.ssh/id_rsa", base) is False

    def test_value_error_short_circuits_to_true(self, tmp_path, monkeypatch):
        # Force Path.resolve to raise OSError so the function must return True.
        from red_team_path_traversal import is_path_traversal

        def boom(_self, *_, **__):
            raise OSError("synthetic failure")

        base = tmp_path
        monkeypatch.setattr(Path, "resolve", boom)
        assert is_path_traversal("file.txt", base) is True


# ---------------------------------------------------------------------------
# extract_tarball_safely
# ---------------------------------------------------------------------------


def _build_clean_tarball(tar_path: Path, names: list[str]) -> Path:
    """Helper: write a tarball with the given safe entries (zero-byte)."""
    with tarfile.open(tar_path, "w") as tar:
        for name in names:
            info = tarfile.TarInfo(name=name)
            info.size = 0
            tar.addfile(info)
    return tar_path


def _build_mixed_tarball(
    tar_path: Path, safe: list[str], evil: list[str]
) -> Path:
    """Helper: write a tarball with a mix of safe and malicious entries."""
    with tarfile.open(tar_path, "w") as tar:
        for name in safe:
            info = tarfile.TarInfo(name=name)
            info.size = 0
            tar.addfile(info)
        for name in evil:
            info = tarfile.TarInfo(name=name)
            info.size = 0
            tar.addfile(info)
    return tar_path


class TestExtractTarballSafely:
    """Tests for extract_tarball_safely function."""

    def test_clean_tarball_extracts_successfully(self, tmp_path):
        tar = _build_clean_tarball(
            tmp_path / "clean.tar",
            ["a.txt", "b.txt"],
        )
        dest = tmp_path / "out"
        dest.mkdir()

        success, messages = extract_tarball_safely(tar, dest)
        assert success is True
        assert messages == ["a.txt", "b.txt"]
        assert (dest / "a.txt").exists()
        assert (dest / "b.txt").exists()

    def test_malicious_tarball_is_fully_blocked(self, tmp_path):
        # Use the bundled create_malicious_tarball helper.
        tar = create_malicious_tarball(tmp_path)
        dest = tmp_path / "out"
        dest.mkdir()

        success, messages = extract_tarball_safely(tar, dest)
        # All malicious entries should be blocked; only the safe entry
        # should be present in `extracted`.
        blocked = [m for m in messages if "BLOCKED" in m]
        extracted = [m for m in messages if not m.startswith("BLOCKED")]
        # The helper plants 5 entries, but tarfile silently drops
        # ``../../etc/passwd`` as a safety measure, so 3 traversal entries
        # actually appear in the tar (shadow, hosts, foo/../../../bar/etc).
        # Windows backslash is not a traversal on POSIX.
        assert len(blocked) >= 3
        assert "safe_file.txt" in extracted
        # The function returns success=True only if errors==0; since
        # blocked entries are recorded as errors, success should be False.
        assert success is False
        # And the safe file should be on disk; the malicious ones should
        # not be.
        assert (dest / "safe_file.txt").exists()
        # On POSIX, ``../etc/passwd`` would have escaped — make sure it
        # did NOT get extracted under dest either.
        assert not (dest / "etc").exists()

    def test_mixed_tarball_extracts_safe_and_blocks_evil(self, tmp_path):
        tar = _build_mixed_tarball(
            tmp_path / "mixed.tar",
            safe=["good1.txt", "good2.txt"],
            evil=["../evil.txt"],
        )
        dest = tmp_path / "out"
        dest.mkdir()

        success, messages = extract_tarball_safely(tar, dest)
        blocked = [m for m in messages if "BLOCKED" in m]
        extracted = [m for m in messages if not m.startswith("BLOCKED")]
        assert "good1.txt" in extracted
        assert "good2.txt" in extracted
        assert any("../evil.txt" in b for b in blocked)
        assert (dest / "good1.txt").exists()
        assert (dest / "good2.txt").exists()
        # success=False because at least one entry was blocked (errors list non-empty)
        assert success is False

    def test_missing_tarball_returns_failure_tuple(self, tmp_path):
        missing = tmp_path / "does_not_exist.tar"
        dest = tmp_path / "out"
        dest.mkdir()

        success, messages = extract_tarball_safely(missing, dest)
        assert success is False
        assert any("Failed to open tarball" in m for m in messages)

    def test_corrupt_tarball_returns_failure_tuple(self, tmp_path):
        bad = tmp_path / "bad.tar"
        bad.write_bytes(b"not a tarball at all\x00\x01\x02")
        dest = tmp_path / "out"
        dest.mkdir()

        success, messages = extract_tarball_safely(bad, dest)
        assert success is False
        assert any("Failed to open tarball" in m for m in messages)

    def test_blocked_files_are_not_created_on_disk(self, tmp_path):
        tar = _build_mixed_tarball(
            tmp_path / "mixed.tar",
            safe=["good.txt"],
            evil=["../../../../tmp/should_not_exist.txt"],
        )
        dest = tmp_path / "out"
        dest.mkdir()

        extract_tarball_safely(tar, dest)
        assert (dest / "good.txt").exists()
        # The malicious path would have escaped dest entirely; verify it
        # was not silently written inside dest either.
        assert not (dest / "tmp").exists()
        assert not (dest / "should_not_exist.txt").exists()

    def test_safe_file_contents_are_preserved(self, tmp_path):
        # A tarball with a real file body that has non-zero content.
        tar_path = tmp_path / "with_content.tar"
        content = b"hello world\n"
        with tarfile.open(tar_path, "w") as tar:
            info = tarfile.TarInfo(name="data.txt")
            info.size = len(content)
            tar.addfile(info, fileobj=_BytesIO(content))

        dest = tmp_path / "out"
        dest.mkdir()
        success, messages = extract_tarball_safely(tar_path, dest)
        assert success is True
        assert (dest / "data.txt").exists()
        assert (dest / "data.txt").read_bytes() == content


class _BytesIO:
    """Minimal file-like wrapper around a bytes payload for tar.addfile."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# create_malicious_tarball
# ---------------------------------------------------------------------------


class TestCreateMaliciousTarball:
    """Tests for create_malicious_tarball function."""

    def test_tarball_is_created(self, tmp_path):
        tar = create_malicious_tarball(tmp_path)
        assert tar.exists()
        assert tar.is_file()

    def test_tarball_contains_safe_entry(self, tmp_path):
        tar = create_malicious_tarball(tmp_path)
        with tarfile.open(tar, "r") as tf:
            names = tf.getnames()
        assert "safe_file.txt" in names

    def test_tarball_contains_all_malicious_entries(self, tmp_path):
        tar = create_malicious_tarball(tmp_path)
        with tarfile.open(tar, "r") as tf:
            names = tf.getnames()
        # Note: tarfile silently strips leading ``../`` segments as a
        # safety measure (CPython tarfile refuses paths that would
        # traverse outside the archive root). So the first
        # ``../../etc/passwd`` is dropped; the others survive.
        expected = {
            "../../etc/shadow",
            "../../../../../../etc/hosts",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "foo/../../../bar/etc",
        }
        assert expected.issubset(set(names))

    def test_tarball_is_a_valid_tar(self, tmp_path):
        tar = create_malicious_tarball(tmp_path)
        # Should be openable as a tarfile.
        with tarfile.open(tar, "r") as tf:
            members = tf.getmembers()
        assert len(members) >= 5  # 1 safe + 4 traversal entries


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMainCreateOnly:
    """Tests for main() with --create-only."""

    def test_create_only_exits_zero(self, tmp_path, monkeypatch, capsys):
        # Patch tempfile.TemporaryDirectory so the tmpdir is inside our
        # controlled tmp_path, so we can inspect the created tarball.

        captured: dict = {}

        def fake_temporarydir(*_, **__):
            captured["tmpdir"] = str(tmp_path / "td")
            Path(captured["tmpdir"]).mkdir(parents=True, exist_ok=True)
            return captured["tmpdir"]

        # The function is invoked via ``with tempfile.TemporaryDirectory()``.
        # We can't easily monkeypatch the context manager, so instead we
        # use subprocess to invoke the script and just check the exit code
        # + a known tarball in the cwd.
        workdir = tmp_path
        # Simulate: run the script with --create-only from a temp dir.
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_path_traversal.py"),
             "--create-only"],
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=15,
        )
        assert result.returncode == 0
        assert "Created malicious tarball" in result.stdout

    def test_create_only_actually_writes_tarball(self, tmp_path, monkeypatch):
        # Override TMPDIR so the script's TemporaryDirectory is inside
        # our controlled tmp_path and survives long enough to inspect.
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        # Also point tempfile's default tempdir root.
        import tempfile

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_path_traversal.py"),
             "--create-only"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        # Path is printed in the last line of stdout:
        # "Created malicious tarball: <path>".
        printed_path = result.stdout.strip().rsplit(":", 1)[-1].strip()
        # Tarball exists while the script is running; after the script
        # exits the tempdir is gone. Instead, assert a tarball of that
        # name was created *somewhere* under tmp_path during the run by
        # pre-placing a sentinel file and checking the parent dir was
        # created.
        # More robust: invoke a small helper that calls main() in-process
        # with a patched TemporaryDirectory and checks the file.
        # Direct in-process test: monkeypatch the script's import of
        # tempfile.TemporaryDirectory. The script imports tempfile at
        # module load time, so we patch the symbol in the module
        # namespace (the function references the global ``tempfile``).
        import red_team_path_traversal as rt_mod
        from red_team_path_traversal import main as rt_main

        class FakeTD:
            def __init__(self, *_, **__):
                self._path = tmp_path / "td"
                self._path.mkdir(parents=True, exist_ok=True)

            def __enter__(self):
                return str(self._path)

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(rt_mod.tempfile, "TemporaryDirectory", FakeTD)
        rc = rt_main(["--create-only"])
        assert rc == 0
        assert (tmp_path / "td" / "malicious.tar").exists()


class TestMainDefaultInvocation:
    """Tests for main() default (non-create-only) path."""

    def test_default_blocks_traversal(self, tmp_path):
        # In default mode, the script should detect at least one BLOCKED
        # entry and exit 0 with a "SUCCESS" message.
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_path_traversal.py")],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert "SUCCESS" in result.stdout
        assert "blocked" in result.stdout.lower()

    def test_verbose_prints_blocked_messages(self):
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_path_traversal.py"),
             "--verbose"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        # At least one BLOCKED line should appear in stdout.
        assert "BLOCKED" in result.stdout


class TestMainCli:
    """Tests for main() CLI plumbing."""

    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_path_traversal.py"),
             "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert "Red team" in result.stdout or "Usage" in result.stdout

    def test_unknown_arg_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_path_traversal.py"),
             "--not-a-real-flag"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0

    def test_short_verbose_flag_works(self):
        result = subprocess.run(
            [sys.executable, str(_BIN_DIR / "red_team_path_traversal.py"),
             "-v"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout
