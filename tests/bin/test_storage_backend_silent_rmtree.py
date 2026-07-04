"""
Regression test: bin/storage_backend.py GitHubReleaseStorageBackend.upload
no longer silently swallows OSError during tempdir cleanup.

We monkeypatch shutil.rmtree to raise, then assert that the module logger
records a debug message that includes the tempdir path and the exception.
This guards against the previous `except OSError: pass` regression that
masked tmp-dir cleanup failures and left /tmp polluted silently.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

# Ensure repo root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bin.storage_backend import (  # noqa: E402
    GitHubReleaseStorageBackend,
    TarballMetadata,
    compute_sha256,
)


class _FakeGhRunner:
    """Minimal fake-gh-runner that records uploads without touching the network."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.assets: dict[str, int] = {}

    def __call__(self, args: list[str]):
        self.calls.append(list(args))
        if args and args[0] == "release" and args[1] == "upload":
            name = args[2].split("/")[-1]
            self.assets[name] = self.assets.get(name, 0) + 1
            return 0, "", ""
        if args and args[0] == "release" and args[1] == "view":
            asset_list = [
                {"name": n, "size": s, "id": f"id-{n}"}
                for n, s in self.assets.items()
            ]
            return 0, '{"assets": ' + str(asset_list).replace("'", '"') + "}", ""
        if args and args[0] == "release" and args[1] == "delete-asset":
            for n in list(self.assets):
                if n in args[2]:
                    del self.assets[n]
                    return 0, "", ""
            return 1, "", "not found"
        return 1, "", f"unhandled gh args: {args!r}"


@pytest.fixture
def fake_gh() -> _FakeGhRunner:
    return _FakeGhRunner()


@pytest.fixture
def backend(fake_gh: _FakeGhRunner) -> GitHubReleaseStorageBackend:
    return GitHubReleaseStorageBackend(
        repo="howardleegeek/test-repo",
        tag="test-tag",
        keep_newest=2,
        gh_runner=fake_gh,
    )


def _make_tarball_and_metadata(tmp_path: Path) -> tuple[Path, TarballMetadata]:
    import gzip
    p = tmp_path / "swarm_real_unit_test.tar.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(b"oyster-real-data-sample-payload-" * 64)
    metadata = TarballMetadata(
        tester_id="t-silent",
        sha256=compute_sha256(p),
        d5_verdict="REAL",
        size_bytes=p.stat().st_size,
    )
    return p, metadata


def test_github_upload_logs_rmtree_failure(
    tmp_path: Path,
    backend: GitHubReleaseStorageBackend,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When shutil.rmtree raises OSError in the upload finally-block, the
    module logger must record a debug entry binding the exception, instead
    of silently swallowing it."""
    sample_tarball, sample_metadata = _make_tarball_and_metadata(tmp_path)

    raised = {"count": 0}

    def _boom_rmtree(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        raised["count"] += 1
        raise OSError(13, "Permission denied")

    import bin.storage_backend as sb_mod
    monkeypatch.setattr(sb_mod.shutil, "rmtree", _boom_rmtree)

    with caplog.at_level(logging.DEBUG, logger="oyster.storage"):
        result = backend.upload(sample_tarball, sample_metadata)

    # Upload still succeeded (control flow preserved).
    assert result.idempotent_skip is False
    assert result.backend == "github"
    # rmtree was called at least twice (tmp_dir + meta_dir cleanup).
    assert raised["count"] >= 2
    # Both finally-blocks logged the rmtree failure with path + exc_info.
    msg_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "could not rmtree temp upload dir" in msg_text
    assert "could not rmtree temp meta dir" in msg_text
    # And the exception info (traceback) is attached on those records.
    upload_recs = [r for r in caplog.records if "temp upload dir" in r.getMessage()]
    meta_recs = [r for r in caplog.records if "temp meta dir" in r.getMessage()]
    assert upload_recs, "missing upload-dir rmtree log record"
    assert meta_recs, "missing meta-dir rmtree log record"
    for rec in upload_recs + meta_recs:
        assert rec.exc_info is not None, (
            f"record {rec.getMessage()!r} missing exc_info; "
            "silent-swallow regression would surface here"
        )


def test_github_upload_happy_path_stays_silent(
    tmp_path: Path,
    backend: GitHubReleaseStorageBackend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sanity check: when rmtree succeeds, no debug rmtree-failure logs are
    emitted. The change must not introduce noise on the happy path."""
    sample_tarball, sample_metadata = _make_tarball_and_metadata(tmp_path)

    with caplog.at_level(logging.DEBUG, logger="oyster.storage"):
        result = backend.upload(sample_tarball, sample_metadata)

    assert result.idempotent_skip is False
    msg_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "could not rmtree" not in msg_text
