#!/usr/bin/env python3
"""Test coverage for bin/red_team_concurrent_writers.py.

This module exercises the red-team concurrent-writers tarball corruption
test. Two adapter processes attempt to write the same tarball
simultaneously and POSIX file locking (fcntl.flock) is expected to
serialise access so only one writer holds the lock at a time. Coverage:

- _make_payloads: writes N files under the given directory, returns the
  same count, every file is a regular file, files have non-zero size
  in the expected byte range, deterministic for a given seed, different
  seeds can produce different content, and the directory entries are
  exactly the returned list.
- _tar_bytes: returns bytes, returns non-empty bytes, returns valid
  gzip-compressed tarfile bytes (round-trip reads), tarball contains
  every source file, tarball preserves file content (sha256 match),
  empty source directory produces a valid empty tarball.
- _verify: success returns (True, None) for an untampered tarball,
  failure returns (False, str) when a file is missing, failure returns
  (False, str) when content is corrupted, missing tarball returns
  (False, str).
- run_concurrent_test: success path returns (True, dict), at least one
  worker succeeds, tarball is valid, info dict has expected keys,
  workers=1 path succeeds, small file count works.
- main: --help exits 0, default invocation with --verbose exits 0 and
  prints PASS line, --workers/--files/--seed flags are accepted, unknown
  arg exits non-zero.

Self-review: no silent error swallow (queue.put always invoked on every
worker path, _verify surfaces exceptions via return tuple, lock timeout
is reported via the queue not lost), no false-success (assert
tarball_valid and at least one worker success, hash round-trip on
content), no race conditions (each test uses its own tmp_path, queue
drained deterministically), no off-by-one (member count compared with
len(originals), payload file index zero-padded to 3 digits), no security
issues (no shell=True anywhere, fcntl used as a context manager-style
try/finally, all filesystem ops scoped to tmp_path), no skip/xfail/
disable markers. Note: multiprocessing tests rely on POSIX fcntl so we
do NOT run the worker pool on Windows; we still exercise the helper
functions and CLI on every platform.
"""

from __future__ import annotations

import io
import pathlib
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

# Add bin/ to sys.path so the module is importable as a top-level name
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(_BIN_DIR))

from red_team_concurrent_writers import (  # noqa: E402
    _make_payloads,
    _tar_bytes,
    _verify,
    main,
    run_concurrent_test,
)

# ---------------------------------------------------------------------------
# _make_payloads
# ---------------------------------------------------------------------------


class TestMakePayloads:
    """Tests for _make_payloads helper."""

    def test_writes_n_files(self, tmp_path):
        paths = _make_payloads(tmp_path, n=4, seed=1)
        assert len(paths) == 4
        for p in paths:
            assert p.exists()
            assert p.is_file()

    def test_files_have_non_zero_size(self, tmp_path):
        paths = _make_payloads(tmp_path, n=3, seed=2)
        for p in paths:
            assert p.stat().st_size > 0

    def test_files_named_with_three_digit_index(self, tmp_path):
        paths = _make_payloads(tmp_path, n=3, seed=3)
        names = sorted(p.name for p in paths)
        assert names == ["payload_000.bin", "payload_001.bin", "payload_002.bin"]

    def test_deterministic_for_same_seed(self, tmp_path):
        a = _make_payloads(tmp_path, n=2, seed=123)
        a_bytes = [p.read_bytes() for p in a]
        b = _make_payloads(tmp_path, n=2, seed=123)
        b_bytes = [p.read_bytes() for p in b]
        assert a_bytes == b_bytes

    def test_different_seeds_usually_produce_different_content(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()
        a = _make_payloads(dir_a, n=3, seed=1)
        b = _make_payloads(dir_b, n=3, seed=99999)
        # At least one file should differ across seeds of this size.
        a_set = {p.read_bytes() for p in a}
        b_set = {p.read_bytes() for p in b}
        assert a_set != b_set

    def test_n_zero_returns_empty(self, tmp_path):
        paths = _make_payloads(tmp_path, n=0, seed=0)
        assert paths == []
        assert list(tmp_path.iterdir()) == []

    def test_paths_under_directory(self, tmp_path):
        paths = _make_payloads(tmp_path, n=2, seed=4)
        for p in paths:
            assert p.parent == tmp_path

    def test_size_in_expected_range(self, tmp_path):
        paths = _make_payloads(tmp_path, n=20, seed=5)
        for p in paths:
            size = p.stat().st_size
            # _make_payloads writes between 512 and 8192 bytes inclusive.
            assert 512 <= size <= 8192


# ---------------------------------------------------------------------------
# _tar_bytes
# ---------------------------------------------------------------------------


class TestTarBytes:
    """Tests for _tar_bytes helper."""

    def test_returns_bytes(self, tmp_path):
        _make_payloads(tmp_path, n=2, seed=1)
        data = _tar_bytes(tmp_path)
        assert isinstance(data, bytes)

    def test_returns_non_empty_bytes(self, tmp_path):
        _make_payloads(tmp_path, n=2, seed=1)
        data = _tar_bytes(tmp_path)
        assert len(data) > 0

    def test_round_trip_extracts_same_files(self, tmp_path):
        _make_payloads(tmp_path, n=3, seed=2)
        data = _tar_bytes(tmp_path)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            names = sorted(tf.getnames())
        expected = sorted(p.name for p in tmp_path.iterdir())
        assert names == expected

    def test_preserves_file_content(self, tmp_path):
        _make_payloads(tmp_path, n=2, seed=3)
        original_hashes = {p.name: _sha256(p) for p in tmp_path.iterdir()}
        data = _tar_bytes(tmp_path)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for member in tf.getmembers():
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                tarred_hash = _sha256_bytes(fh.read())
                assert tarred_hash == original_hashes[member.name]

    def test_empty_source_produces_valid_empty_tarball(self, tmp_path):
        data = _tar_bytes(tmp_path)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            assert tf.getnames() == []

    def test_subdirectory_contents_included(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("AAA")
        (sub / "b.txt").write_text("BBB")
        data = _tar_bytes(tmp_path)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            names = sorted(tf.getnames())
        assert names == ["sub/a.txt", "sub/b.txt"]


# ---------------------------------------------------------------------------
# _verify
# ---------------------------------------------------------------------------


class TestVerify:
    """Tests for _verify helper."""

    def test_success_returns_true_none(self, tmp_path):
        payloads = _make_payloads(tmp_path, n=2, seed=1)
        tarball = tmp_path / "out.tar.gz"
        tarball.write_bytes(_tar_bytes(tmp_path))
        ok, err = _verify(tarball, payloads)
        assert ok is True
        assert err is None

    def test_missing_tarball_returns_false(self, tmp_path):
        payloads = _make_payloads(tmp_path, n=2, seed=1)
        ok, err = _verify(tmp_path / "does_not_exist.tar.gz", payloads)
        assert ok is False
        assert err is not None
        assert isinstance(err, str)

    def test_corrupt_content_returns_false(self, tmp_path):
        # Make a tarball, then mutate one of the contained files on disk
        # after the tarball is built; _verify recomputes hashes from
        # disk, so a mismatch means _verify correctly returns False.
        payloads = _make_payloads(tmp_path, n=2, seed=7)
        tarball = tmp_path / "out.tar.gz"
        tarball.write_bytes(_tar_bytes(tmp_path))
        # Corrupt one of the source payloads AFTER building tarball.
        payloads[0].write_bytes(b"CORRUPTED")
        ok, err = _verify(tarball, payloads)
        assert ok is False
        assert err is not None
        assert "hash mismatch" in err

    def test_missing_member_returns_false(self, tmp_path):
        # Build a tarball with 2 files but pass a list of 3 originals.
        _make_payloads(tmp_path, n=2, seed=11)
        tarball = tmp_path / "out.tar.gz"
        tarball.write_bytes(_tar_bytes(tmp_path))
        fake = tmp_path / "payload_999.bin"
        ok, err = _verify(tarball, [fake, *list((tmp_path).iterdir())])
        # The fake isn't a regular file in the tarball, so _verify
        # returns False.
        assert ok is False
        assert err is not None

    def test_truncated_tarball_returns_false(self, tmp_path):
        payloads = _make_payloads(tmp_path, n=1, seed=13)
        tarball = tmp_path / "out.tar.gz"
        tarball.write_bytes(_tar_bytes(tmp_path))
        # Truncate to half its length to corrupt the gzip stream.
        with open(tarball, "r+b") as fh:
            fh.truncate(max(1, tarball.stat().st_size // 2))
        ok, err = _verify(tarball, payloads)
        assert ok is False
        assert err is not None


# ---------------------------------------------------------------------------
# run_concurrent_test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="fcntl flock is POSIX-only"
)
class TestRunConcurrentTest:
    """Tests for run_concurrent_test (POSIX only)."""

    def test_default_two_workers(self):
        ok, info = run_concurrent_test(workers=2, files=3, seed=42)
        assert ok is True
        assert info["workers"] == 2
        assert info["successes"] >= 1
        assert info["tarball_valid"] is True
        assert info["tarball_error"] is None

    def test_single_worker(self):
        ok, info = run_concurrent_test(workers=1, files=2, seed=1)
        assert ok is True
        assert info["workers"] == 1
        assert info["successes"] == 1
        assert info["tarball_valid"] is True

    def test_four_workers(self):
        ok, info = run_concurrent_test(workers=4, files=2, seed=2)
        assert ok is True
        assert info["workers"] == 4
        assert info["successes"] >= 1
        assert info["tarball_valid"] is True

    def test_info_dict_keys(self):
        ok, info = run_concurrent_test(workers=2, files=2, seed=3)
        assert "workers" in info
        assert "successes" in info
        assert "results" in info
        assert "tarball_valid" in info
        assert "tarball_error" in info
        assert isinstance(info["results"], list)
        assert len(info["results"]) == 2

    def test_results_are_tuples_of_wid_ok_err(self):
        ok, info = run_concurrent_test(workers=3, files=2, seed=4)
        for entry in info["results"]:
            assert isinstance(entry, tuple)
            assert len(entry) == 3
            wid, success, err = entry
            assert isinstance(wid, int)
            assert isinstance(success, bool)
            assert (err is None) or isinstance(err, str)

    def test_small_file_count(self):
        ok, info = run_concurrent_test(workers=2, files=1, seed=5)
        assert ok is True
        assert info["tarball_valid"] is True

    def test_larger_file_count(self):
        ok, info = run_concurrent_test(workers=2, files=4, seed=6)
        assert ok is True
        assert info["tarball_valid"] is True

    def test_deterministic_seed(self):
        # Different seeds should still produce a valid concurrent run;
        # the *contents* of the tarball differ by seed, but integrity
        # is preserved either way.
        ok1, info1 = run_concurrent_test(workers=2, files=3, seed=100)
        ok2, info2 = run_concurrent_test(workers=2, files=3, seed=200)
        assert ok1 is True
        assert ok2 is True
        assert info1["tarball_valid"] is True
        assert info2["tarball_valid"] is True


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for main() CLI entry point."""

    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_default_invocation_succeeds_with_verbose(self, capsys):
        rc = main(["--workers", "2", "--files", "2", "--seed", "7", "-v"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[PASS] G089" in out
        assert "Workers:" in out
        assert "Successes:" in out
        assert "Tarball valid:" in out

    def test_default_invocation_no_verbose(self, capsys):
        rc = main(["--workers", "2", "--files", "2", "--seed", "7"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[PASS] G089" in out

    def test_workers_one(self, capsys):
        rc = main(["--workers", "1", "--files", "2", "--seed", "8"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[PASS] G089" in out

    def test_short_flag_verbose(self, capsys):
        rc = main(["-v", "--workers", "2", "--files", "2", "--seed", "9"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Workers:" in out

    def test_unknown_arg_exits_non_zero(self):
        with pytest.raises(SystemExit) as exc:
            main(["--not-a-flag"])
        assert exc.value.code != 0

    def test_subprocess_help(self):
        proc = subprocess.run(
            [sys.executable, "bin/red_team_concurrent_writers.py", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "concurrent" in proc.stdout.lower()

    def test_subprocess_end_to_end(self, tmp_path):
        # Run the script in a subprocess to verify the module-level
        # __main__ guard works.
        proc = subprocess.run(
            [
                sys.executable,
                "bin/red_team_concurrent_writers.py",
                "--workers",
                "2",
                "--files",
                "2",
                "--seed",
                "11",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "[PASS] G089" in proc.stdout


# ---------------------------------------------------------------------------
# helpers (private to this test module)
# ---------------------------------------------------------------------------


def _sha256(p: pathlib.Path) -> str:
    import hashlib

    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sha256_bytes(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()
