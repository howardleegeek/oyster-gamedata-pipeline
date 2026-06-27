"""
Tests for bin/storage_backend.py + bin/upload_tarball.py.

Iron-law (Howard): no stub backends. Each backend roundtrips real data:
  - LocalFileStorageBackend writes real files
  - S3StorageBackend uses real boto3 against moto-mocked S3
  - GitHubReleaseStorageBackend uses a fake-gh-runner that simulates the
    `gh` CLI's JSON contract (real CLI is exercised in CI/manual tests).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Ensure repo root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bin.storage_backend import (  # noqa: E402
    GITHUB_ASSET_LIMIT_BYTES,
    GitHubReleaseStorageBackend,
    LocalFileStorageBackend,
    S3StorageBackend,
    TarballMetadata,
    UploadResult,
    compute_sha256,
    derive_asset_name,
    get_backend,
    register_backend,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_tarball(tmp_path: Path) -> Path:
    """Build a minimal real .tar.gz so callers can exercise hash/size logic."""
    payload = b"oyster-real-data-sample-payload-" * 1024
    p = tmp_path / "swarm_real_unit_test.tar.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(payload)
    return p


@pytest.fixture
def sample_metadata(sample_tarball: Path) -> TarballMetadata:
    return TarballMetadata(
        tester_id="tester-001",
        sha256=compute_sha256(sample_tarball),
        d5_verdict="REAL",
        size_bytes=sample_tarball.stat().st_size,
    )


# ---------------------------------------------------------------------------
# 1. TarballMetadata validation
# ---------------------------------------------------------------------------


def test_tarball_metadata_rejects_bad_verdict() -> None:
    with pytest.raises(ValueError, match="d5_verdict"):
        TarballMetadata(
            tester_id="t-1",
            sha256="0" * 64,
            d5_verdict="BOGUS",
            size_bytes=1,
        )


def test_tarball_metadata_rejects_bad_sha256() -> None:
    with pytest.raises(ValueError, match="sha256"):
        TarballMetadata(
            tester_id="t-1",
            sha256="ZZZ",
            d5_verdict="REAL",
            size_bytes=1,
        )


# ---------------------------------------------------------------------------
# 2. LocalFileStorageBackend round-trip
# ---------------------------------------------------------------------------


def test_local_backend_round_trip(
    tmp_path: Path, sample_tarball: Path, sample_metadata: TarballMetadata
) -> None:
    backend = LocalFileStorageBackend(root=tmp_path / "store")

    # upload
    result = backend.upload(sample_tarball, sample_metadata)
    assert isinstance(result, UploadResult)
    assert result.idempotent_skip is False
    assert result.backend == "local"
    assert result.metadata.sha256 == sample_metadata.sha256

    # storage_url + signed_url shapes
    assert result.storage_url.startswith("file://")
    assert result.asset_name in result.storage_url
    assert "expires=" in result.signed_url

    # the actual blob landed on disk with correct bytes
    asset_path = tmp_path / "store" / result.asset_name
    assert asset_path.is_file()
    assert hashlib.sha256(asset_path.read_bytes()).hexdigest() == sample_metadata.sha256

    # list shows it
    listed = backend.list_assets()
    assert len(listed) == 1
    assert listed[0]["sha256"] == sample_metadata.sha256
    assert listed[0]["asset_name"] == result.asset_name

    # find_by_sha256 hits
    found = backend.find_by_sha256(sample_metadata.sha256)
    assert found is not None
    assert found["asset_name"] == result.asset_name

    # delete works
    assert backend.delete(result.asset_name) is True
    assert backend.delete(result.asset_name) is False  # second time = not-found


def test_local_backend_idempotent_upload(
    tmp_path: Path, sample_tarball: Path, sample_metadata: TarballMetadata
) -> None:
    backend = LocalFileStorageBackend(root=tmp_path / "store")
    first = backend.upload(sample_tarball, sample_metadata)
    second = backend.upload(sample_tarball, sample_metadata)
    assert first.idempotent_skip is False
    assert second.idempotent_skip is True
    # Same canonical URL.
    assert first.storage_url == second.storage_url


# ---------------------------------------------------------------------------
# 3. S3StorageBackend with moto
# ---------------------------------------------------------------------------


@pytest.fixture
def s3_backend():
    """Spin up an in-memory S3 with moto and return a configured backend."""
    pytest.importorskip("moto")
    import boto3
    from moto import mock_aws

    bucket = "oyster-test-bucket"
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=bucket)
        yield S3StorageBackend(bucket=bucket, region="us-east-1", client=client)


def test_s3_backend_round_trip(
    s3_backend: S3StorageBackend, sample_tarball: Path, sample_metadata: TarballMetadata
) -> None:
    result = s3_backend.upload(sample_tarball, sample_metadata)
    assert result.backend == "s3"
    assert result.idempotent_skip is False
    assert result.storage_url.startswith(f"s3://{s3_backend.bucket}/")

    # The blob is in the bucket with correct content + metadata headers.
    obj = s3_backend.client.get_object(Bucket=s3_backend.bucket, Key=result.asset_name)
    assert obj["Metadata"]["sha256"] == sample_metadata.sha256
    assert obj["Metadata"]["d5-verdict"] == "REAL"
    assert hashlib.sha256(obj["Body"].read()).hexdigest() == sample_metadata.sha256

    # list_assets reads the sidecar JSON and returns metadata.
    assets = s3_backend.list_assets()
    assert len(assets) == 1
    assert assets[0]["sha256"] == sample_metadata.sha256
    assert assets[0]["asset_name"] == result.asset_name


def test_s3_backend_signed_url(
    s3_backend: S3StorageBackend, sample_tarball: Path, sample_metadata: TarballMetadata
) -> None:
    result = s3_backend.upload(sample_tarball, sample_metadata)
    url = s3_backend.get_signed_url(result.asset_name, ttl_seconds=3600)
    # Presigned URLs always carry these markers.
    assert "X-Amz-" in url or "AWSAccessKeyId" in url
    assert s3_backend.bucket in url
    assert result.asset_name in url


def test_s3_backend_idempotent_upload(
    s3_backend: S3StorageBackend, sample_tarball: Path, sample_metadata: TarballMetadata
) -> None:
    first = s3_backend.upload(sample_tarball, sample_metadata)
    second = s3_backend.upload(sample_tarball, sample_metadata)
    assert first.idempotent_skip is False
    assert second.idempotent_skip is True


def test_s3_backend_delete(
    s3_backend: S3StorageBackend, sample_tarball: Path, sample_metadata: TarballMetadata
) -> None:
    result = s3_backend.upload(sample_tarball, sample_metadata)
    assert s3_backend.delete(result.asset_name) is True
    assert s3_backend.list_assets() == []
    # Second delete should report not-found.
    assert s3_backend.delete(result.asset_name) is False


# ---------------------------------------------------------------------------
# 4. GitHubReleaseStorageBackend with fake gh runner
# ---------------------------------------------------------------------------


class FakeGhRunner:
    """
    Mimics `gh release view/upload/delete-asset` against an in-memory release.

    Stores asset names + bytes so tests can assert the exact upload happened.
    """

    def __init__(self) -> None:
        # name -> {"size": int, "createdAt": iso, "url": str}
        self.assets: dict[str, dict[str, Any]] = {}
        self._tick = 0
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], input_bytes: bytes | None = None) -> tuple[int, str, str]:
        self.calls.append(args)
        if args[:2] == ["release", "view"]:
            payload = {
                "assets": [{"name": k, **v} for k, v in self.assets.items()],
            }
            return 0, json.dumps(payload), ""
        if args[:2] == ["release", "upload"]:
            # gh release upload <tag> <path...> --repo R --clobber
            paths = [a for a in args[3:] if not a.startswith("--") and "/" in a]
            for p in paths:
                name = os.path.basename(p)
                self._tick += 1
                size = os.path.getsize(p) if os.path.exists(p) else 0
                self.assets[name] = {
                    "size": size,
                    "createdAt": f"2026-05-07T00:00:{self._tick:02d}Z",
                    "url": f"https://example.invalid/{name}",
                }
            return 0, "", ""
        if args[:2] == ["release", "delete-asset"]:
            name = args[3]
            if name in self.assets:
                del self.assets[name]
                return 0, "", ""
            return 1, "", "not found"
        return 1, "", f"unhandled gh args: {args!r}"


@pytest.fixture
def github_backend() -> GitHubReleaseStorageBackend:
    fake = FakeGhRunner()
    backend = GitHubReleaseStorageBackend(
        repo="howardleegeek/test-repo",
        tag="test-tag",
        keep_newest=2,
        gh_runner=fake,
    )
    backend._fake_runner = fake  # type: ignore[attr-defined]  # for test introspection
    return backend


def test_github_backend_uploads_and_lists(
    github_backend: GitHubReleaseStorageBackend,
    sample_tarball: Path,
    sample_metadata: TarballMetadata,
) -> None:
    result = github_backend.upload(sample_tarball, sample_metadata)
    assert result.backend == "github"
    assert result.idempotent_skip is False
    assert result.storage_url.startswith("gh://howardleegeek/test-repo/releases/test-tag/")

    listed = github_backend.list_assets()
    # Should include our tarball; list_assets filters out .metadata.json sidecars.
    asset_names = [a["asset_name"] for a in listed]
    assert any(name.endswith(".tar.gz") for name in asset_names)
    assert result.asset_name in asset_names


def test_github_backend_signed_url_is_public_release_url(
    github_backend: GitHubReleaseStorageBackend,
    sample_tarball: Path,
    sample_metadata: TarballMetadata,
) -> None:
    result = github_backend.upload(sample_tarball, sample_metadata)
    url = github_backend.get_signed_url(result.asset_name)
    # gh releases are publicly downloadable; the "signed" URL is just the
    # canonical release-asset URL — testers click it without any auth.
    expected = (
        f"https://github.com/howardleegeek/test-repo/releases/download/test-tag/{result.asset_name}"
    )
    assert url == expected


def test_github_backend_idempotent_upload(
    github_backend: GitHubReleaseStorageBackend,
    sample_tarball: Path,
    sample_metadata: TarballMetadata,
) -> None:
    first = github_backend.upload(sample_tarball, sample_metadata)
    second = github_backend.upload(sample_tarball, sample_metadata)
    assert first.idempotent_skip is False
    assert second.idempotent_skip is True


def test_github_backend_rejects_oversized_tarball(
    github_backend: GitHubReleaseStorageBackend, tmp_path: Path
) -> None:
    huge = tmp_path / "huge.tar.gz"
    huge.write_bytes(b"\0")  # actual size doesn't matter — metadata claims it's huge
    metadata = TarballMetadata(
        tester_id="t-1",
        sha256="a" * 64,
        d5_verdict="REAL",
        size_bytes=GITHUB_ASSET_LIMIT_BYTES + 1,
    )
    with pytest.raises(ValueError, match="2 GiB"):
        github_backend.upload(huge, metadata)


def test_github_backend_rotating_buffer_prunes_old_assets(
    github_backend: GitHubReleaseStorageBackend,
    tmp_path: Path,
) -> None:
    """keep_newest=2 means after 4 uploads, only 2 tarballs remain."""
    runner = github_backend._fake_runner  # type: ignore[attr-defined]
    for i in range(4):
        tar = tmp_path / f"swarm_real_{i:02d}.tar.gz"
        with gzip.open(tar, "wb") as fh:
            fh.write(f"sample-{i}".encode())
        meta = TarballMetadata(
            tester_id=f"t-{i}",
            sha256=hashlib.sha256(f"sample-{i}".encode()).hexdigest(),
            d5_verdict="REAL",
            size_bytes=tar.stat().st_size,
        )
        github_backend.upload(tar, meta)

    tarballs = [n for n in runner.assets if n.endswith(".tar.gz")]
    assert len(tarballs) == 2  # keep_newest


# ---------------------------------------------------------------------------
# 5. UploadResult / get_backend factory
# ---------------------------------------------------------------------------


def test_upload_result_to_dict_roundtrip(
    tmp_path: Path, sample_tarball: Path, sample_metadata: TarballMetadata
) -> None:
    backend = LocalFileStorageBackend(root=tmp_path / "store")
    result = backend.upload(sample_tarball, sample_metadata)
    payload = result.to_dict()
    assert set(payload) >= {"storage_url", "signed_url", "metadata", "asset_name", "backend"}
    assert payload["metadata"]["sha256"] == sample_metadata.sha256


def test_get_backend_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "factory"))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    b = get_backend()
    assert isinstance(b, LocalFileStorageBackend)
    assert b.root == tmp_path / "factory"

    with pytest.raises(ValueError, match="unknown backend"):
        get_backend("does-not-exist")


def test_register_custom_backend() -> None:
    class Dummy(LocalFileStorageBackend):
        name = "dummy"

    register_backend("dummy", Dummy)
    try:
        b = get_backend("dummy", root=Path(tempfile.mkdtemp()))
        assert isinstance(b, Dummy)
    finally:
        # Re-register the original to avoid leaking into other tests.
        from bin.storage_backend import _BACKENDS

        _BACKENDS.pop("dummy", None)


# ---------------------------------------------------------------------------
# 6. derive_asset_name + compute_sha256 sanity
# ---------------------------------------------------------------------------


def test_derive_asset_name_uses_sha8(tmp_path: Path) -> None:
    p = tmp_path / "swarm_real_abc.tar.gz"
    p.write_bytes(b"x")
    meta = TarballMetadata(
        tester_id="alpha",
        sha256="0123456789abcdef" * 4,  # 64 hex
        d5_verdict="REAL",
        size_bytes=1,
    )
    name = derive_asset_name(p, meta)
    assert name == "alpha/01234567_abc.tar.gz"  # swarm_real_ prefix stripped


def test_compute_sha256_matches_hashlib(tmp_path: Path) -> None:
    payload = b"oyster" * 1024
    p = tmp_path / "blob.bin"
    p.write_bytes(payload)
    assert compute_sha256(p) == hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# 7. CLI smoke (exercises bin/upload_tarball.py end-to-end)
# ---------------------------------------------------------------------------


def test_upload_tarball_cli(
    tmp_path: Path,
    sample_tarball: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "cli_store"))
    from bin.upload_tarball import main

    rc = main(
        [
            str(sample_tarball),
            "--tester-id",
            "cli-tester",
            "--d5-verdict",
            "REAL",
            "--backend",
            "local",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip())
    assert out["backend"] == "local"
    assert out["metadata"]["tester_id"] == "cli-tester"
    assert out["metadata"]["d5_verdict"] == "REAL"
    assert out["idempotent_skip"] is False
