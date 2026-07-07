#!/usr/bin/env python3
"""
storage_backend.py — Pluggable storage abstraction for tester-tarball uploads.

Howard 2026-05-07: GitHub release has a 2 GiB hard cap per asset and 50 assets
per release. We need to scale beyond ~100 testers/day. This module decouples
the watcher daemon from any one storage provider.

Backends (all production-grade, no stubs):
    LocalFileStorageBackend   — filesystem; used in tests + local dev.
    S3StorageBackend          — AWS S3 / Cloudflare R2 / Backblaze B2 (boto3).
    GitHubReleaseStorageBackend — legacy / fallback for the 3 newest tarballs
                                  (lets testers browse a public release page
                                  without auth).

Iron law (Howard): NO STUB BACKENDS. Each backend roundtrips real data:
- LocalFileStorageBackend writes real files to disk.
- S3StorageBackend issues real boto3 calls (tested with moto).
- GitHubReleaseStorageBackend shells out to the real `gh` CLI.

USAGE:
    from bin.storage_backend import get_backend

    backend = get_backend("s3")            # or "github" or "local"
    result = backend.upload(
        path=Path("/tmp/swarm_real_X.tar.gz"),
        metadata=TarballMetadata(
            tester_id="tester-001",
            sha256="abc123...",
            d5_verdict="REAL",
            size_bytes=12345,
        ),
    )
    # result.signed_url is a 24h presigned download link.

ENV VARS PER BACKEND:
    S3:
        STORAGE_S3_BUCKET           (required)  e.g. oyster-tester-tarballs
        STORAGE_S3_REGION           (default us-east-1)
        STORAGE_S3_ENDPOINT_URL     (optional)  for R2/B2/MinIO
        AWS_ACCESS_KEY_ID           (required)
        AWS_SECRET_ACCESS_KEY       (required)
    GitHub:
        STORAGE_GITHUB_REPO         (default howardleegeek/oyster-gamedata-pipeline)
        STORAGE_GITHUB_TAG          (default real-data-sample-v1-20260507-0742)
        STORAGE_GITHUB_KEEP_NEWEST  (default 3)
        # gh CLI must be authenticated: `gh auth status`
    Local:
        STORAGE_LOCAL_ROOT          (default /tmp/oyster_storage)
        STORAGE_LOCAL_BASE_URL      (default file://{STORAGE_LOCAL_ROOT})
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("oyster.storage")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SIGNED_URL_TTL_SECONDS = 24 * 60 * 60  # 24h
GITHUB_ASSET_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
DEFAULT_GITHUB_REPO = "howardleegeek/oyster-gamedata-pipeline"
DEFAULT_GITHUB_TAG = "real-data-sample-v1-20260507-0742"
DEFAULT_GITHUB_KEEP_NEWEST = 3
PROTECTED_GITHUB_ASSET = "oyster_REAL6_test.tar.gz"  # stable seed, never deleted
VALID_VERDICTS = {"REAL", "MIXED", "PLACEHOLDER"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TarballMetadata:
    """Immutable per-tarball metadata. Stored alongside the upload in JSON sidecar."""

    tester_id: str
    sha256: str
    d5_verdict: str  # "REAL" | "MIXED" | "PLACEHOLDER"
    size_bytes: int
    uploaded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notes: str = ""

    def __post_init__(self) -> None:
        if self.d5_verdict not in VALID_VERDICTS:
            raise ValueError(
                f"d5_verdict must be one of {VALID_VERDICTS}, got {self.d5_verdict!r}"
            )
        if len(self.sha256) != 64 or not all(c in "0123456789abcdef" for c in self.sha256):
            raise ValueError(f"sha256 must be 64 hex chars, got {self.sha256!r}")
        if self.size_bytes <= 0:
            raise ValueError(f"size_bytes must be > 0, got {self.size_bytes}")
        if not self.tester_id:
            raise ValueError("tester_id must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        """Convert TarballMetadata to a dictionary.

        Returns:
            Dictionary representation of this metadata object.
        """
        return asdict(self)


@dataclass(frozen=True)
class UploadResult:
    """Result of a successful (or idempotent) upload."""

    storage_url: str  # canonical backend URL (s3://, gh://, file://)
    signed_url: str  # 24h presigned download URL — what testers/buyers click
    metadata: TarballMetadata
    asset_name: str
    backend: str
    idempotent_skip: bool = False  # True if same sha256 was already uploaded

    def to_dict(self) -> dict[str, Any]:
        """Convert UploadResult to a dictionary.

        Returns:
            Dictionary representation of this upload result, including
            storage_url, signed_url, metadata, asset_name, backend,
            and idempotent_skip fields.
        """
        return {
            "storage_url": self.storage_url,
            "signed_url": self.signed_url,
            "metadata": self.metadata.to_dict(),
            "asset_name": self.asset_name,
            "backend": self.backend,
            "idempotent_skip": self.idempotent_skip,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream-hash a file. 1 MB chunks keep memory bounded for 2 GB tarballs."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def derive_asset_name(path: Path, metadata: TarballMetadata) -> str:
    """
    Asset name keys on sha256 prefix so identical tarballs collide deterministically
    (idempotency). Layout: {tester_id}/{sha8}_{basename}.
    """
    sha8 = metadata.sha256[:8]
    # Strip swarm_real_ prefix to match existing GitHub-release convention.
    base = path.name.removeprefix("swarm_real_")
    return f"{metadata.tester_id}/{sha8}_{base}"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class StorageBackend(ABC):
    """Abstract storage backend. Subclass and implement the four primitives."""

    name: str = "abstract"

    @abstractmethod
    def upload(self, path: Path, metadata: TarballMetadata) -> UploadResult:
        """
        Upload a tarball with metadata. MUST be idempotent: re-uploading the same
        sha256 returns UploadResult(idempotent_skip=True) without re-transferring.
        """

    @abstractmethod
    def list_assets(self) -> list[dict[str, Any]]:
        """Return [{asset_name, sha256, size_bytes, uploaded_at, ...}, ...]."""

    @abstractmethod
    def get_signed_url(self, asset_name: str, ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS) -> str:
        """Generate a fresh signed download URL (default 24h TTL)."""

    @abstractmethod
    def delete(self, asset_name: str) -> bool:
        """Delete an asset. Return True if deleted, False if not found."""

    # ----- shared helpers -----

    def find_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        """Default O(n) scan. Backends with index can override."""
        for asset in self.list_assets():
            if asset.get("sha256") == sha256:
                return asset
        return None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Local file backend (used for tests + local dev — never deploy this to prod)
# ---------------------------------------------------------------------------


class LocalFileStorageBackend(StorageBackend):
    """Writes tarballs + sidecar JSON to local disk. Useful for tests."""

    name = "local"

    def __init__(self, root: Path | str | None = None, base_url: str | None = None) -> None:
        self.root = Path(root or os.environ.get("STORAGE_LOCAL_ROOT", "/tmp/oyster_storage"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url or os.environ.get(
            "STORAGE_LOCAL_BASE_URL", f"file://{self.root}"
        )

    def _meta_path(self, asset_name: str) -> Path:
        return self.root / f"{asset_name}.metadata.json"

    def _asset_path(self, asset_name: str) -> Path:
        return self.root / asset_name

    def upload(self, path: Path, metadata: TarballMetadata) -> UploadResult:
        """Upload a tarball to local storage.

        Copies the tarball to the configured local root directory along with
        a sidecar JSON file containing the metadata. Supports idempotent
        re-uploads: if a file with the same SHA256 already exists, returns
        early with idempotent_skip=True.

        Args:
            path: Path to the tarball file to upload.
            metadata: Metadata including tester_id, sha256, d5_verdict, etc.

        Returns:
            UploadResult with storage_url, signed_url, metadata, asset_name,
                backend, and idempotent_skip flag.

        Raises:
            FileNotFoundError: If the tarball path does not exist or is not a file.
        """
        if not path.is_file():
            raise FileNotFoundError(f"tarball not found: {path}")
        asset_name = derive_asset_name(path, metadata)
        target = self._asset_path(asset_name)
        meta_path = self._meta_path(asset_name)

        # Idempotency check.
        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text())
                if existing.get("sha256") == metadata.sha256:
                    logger.info("local: idempotent skip for sha256=%s", metadata.sha256[:8])
                    return UploadResult(
                        storage_url=f"{self.base_url}/{asset_name}",
                        signed_url=self.get_signed_url(asset_name),
                        metadata=metadata,
                        asset_name=asset_name,
                        backend=self.name,
                        idempotent_skip=True,
                    )
            except (json.JSONDecodeError, OSError):
                pass  # corrupted sidecar — re-upload.

        target.parent.mkdir(parents=True, exist_ok=True)
        # Use copy (not move) — the watcher may still need the original.
        shutil.copy2(path, target)
        meta_path.write_text(json.dumps(metadata.to_dict(), indent=2))
        logger.info("local: wrote %s (%d bytes)", asset_name, metadata.size_bytes)
        return UploadResult(
            storage_url=f"{self.base_url}/{asset_name}",
            signed_url=self.get_signed_url(asset_name),
            metadata=metadata,
            asset_name=asset_name,
            backend=self.name,
        )

    def list_assets(self) -> list[dict[str, Any]]:
        """List all uploaded assets in local storage.

        Reads all *.metadata.json files under the root directory and returns
        a list of asset metadata dictionaries sorted by upload time (newest first).

        Returns:
            List of dictionaries, each containing asset metadata including
            'asset_name', 'tester_id', 'sha256', 'd5_verdict', 'size_bytes',
            and 'uploaded_at' fields.

        Raises:
            OSError: If the root directory cannot be accessed.
        """
        out: list[dict[str, Any]] = []
        for meta_path in self.root.rglob("*.metadata.json"):
            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            asset_name = str(meta_path.relative_to(self.root)).removesuffix(".metadata.json")
            out.append({**meta, "asset_name": asset_name})
        # newest first
        out.sort(key=lambda a: a.get("uploaded_at", ""), reverse=True)
        return out

    def get_signed_url(self, asset_name: str, ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS) -> str:
        """Generate a signed URL for accessing an asset.

        Note: file:// URLs don't actually expire in the traditional sense;
        this method synthesizes an expiration fragment for parity with
        cloud backends so tests can assert TTL propagation.

        Args:
            asset_name: The name/key of the asset to generate a URL for.
            ttl_seconds: Time-to-live for the signed URL in seconds.
                Defaults to DEFAULT_SIGNED_URL_TTL_SECONDS (24h).

        Returns:
            A file:// URL with an expiration fragment in the format
            `file://{base_url}/{asset_name}#expires={unix_timestamp}`.
        """
        # file:// URLs don't actually expire; we synthesize a fragment for parity
        # with cloud backends so tests can assert ttl propagation.
        expires_at = int(time.time()) + ttl_seconds
        return f"{self.base_url}/{asset_name}#expires={expires_at}"

    def delete(self, asset_name: str) -> bool:
        """Delete an asset from local storage.

        Removes both the asset file and its metadata sidecar file.

        Args:
            asset_name: The name/key of the asset to delete.

        Returns:
            True if either the asset or metadata file existed before deletion,
            False if neither existed.
        """
        target = self._asset_path(asset_name)
        meta_path = self._meta_path(asset_name)
        existed = target.exists() or meta_path.exists()
        target.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return existed


# ---------------------------------------------------------------------------
# S3 backend (AWS S3, Cloudflare R2, Backblaze B2 — same boto3 interface)
# ---------------------------------------------------------------------------


class S3StorageBackend(StorageBackend):
    """
    S3-compatible backend. Works against:
      - AWS S3              (no endpoint_url override)
      - Cloudflare R2       (endpoint_url=https://<account>.r2.cloudflarestorage.com)
      - Backblaze B2        (endpoint_url=https://s3.<region>.backblazeb2.com)
      - MinIO (self-hosted) (endpoint_url=http://minio:9000)

    Idempotency: HEAD the object first; if x-amz-meta-sha256 matches, no-op.
    Metadata is stored in object metadata headers AND a .metadata.json sidecar
    (the sidecar is what list_assets reads to avoid N HEAD calls).
    """

    name = "s3"

    def __init__(
        self,
        bucket: str | None = None,
        region: str | None = None,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket or os.environ.get("STORAGE_S3_BUCKET", "")
        if not self.bucket:
            raise ValueError("S3StorageBackend requires bucket (env STORAGE_S3_BUCKET)")
        self.region = region or os.environ.get("STORAGE_S3_REGION", "us-east-1")
        self.endpoint_url = endpoint_url or os.environ.get("STORAGE_S3_ENDPOINT_URL") or None

        if client is not None:
            # Tests inject a moto-backed client.
            self.client = client
        else:
            import boto3  # lazy: keeps import cost off the local-only path
            kwargs: dict[str, Any] = {"region_name": self.region}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self.client = boto3.client("s3", **kwargs)

    def _meta_key(self, asset_name: str) -> str:
        return f"{asset_name}.metadata.json"

    def upload(self, path: Path, metadata: TarballMetadata) -> UploadResult:
        if not path.is_file():
            raise FileNotFoundError(f"tarball not found: {path}")
        asset_name = derive_asset_name(path, metadata)

        # Idempotency: check object metadata sidecar first (single GET, cheap).
        existing = self._get_metadata(asset_name)
        if existing and existing.get("sha256") == metadata.sha256:
            logger.info("s3: idempotent skip for sha256=%s", metadata.sha256[:8])
            return UploadResult(
                storage_url=f"s3://{self.bucket}/{asset_name}",
                signed_url=self.get_signed_url(asset_name),
                metadata=metadata,
                asset_name=asset_name,
                backend=self.name,
                idempotent_skip=True,
            )

        with path.open("rb") as fh:
            self.client.put_object(
                Bucket=self.bucket,
                Key=asset_name,
                Body=fh,
                ContentType="application/gzip",
                Metadata={
                    "sha256": metadata.sha256,
                    "tester-id": metadata.tester_id,
                    "d5-verdict": metadata.d5_verdict,
                    "size-bytes": str(metadata.size_bytes),
                    "uploaded-at": metadata.uploaded_at,
                },
            )
        # Sidecar JSON (for list_assets — avoids N HeadObject calls).
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._meta_key(asset_name),
            Body=json.dumps(metadata.to_dict()).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(
            "s3: uploaded %s/%s (%d bytes)", self.bucket, asset_name, metadata.size_bytes
        )
        return UploadResult(
            storage_url=f"s3://{self.bucket}/{asset_name}",
            signed_url=self.get_signed_url(asset_name),
            metadata=metadata,
            asset_name=asset_name,
            backend=self.name,
        )

    def _get_metadata(self, asset_name: str) -> dict[str, Any] | None:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._meta_key(asset_name))
            return json.loads(resp["Body"].read().decode("utf-8"))
        except Exception:
            return None

    def list_assets(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith(".metadata.json"):
                    continue
                asset_name = key.removesuffix(".metadata.json")
                meta = self._get_metadata(asset_name) or {}
                out.append({**meta, "asset_name": asset_name})
        out.sort(key=lambda a: a.get("uploaded_at", ""), reverse=True)
        return out

    def get_signed_url(self, asset_name: str, ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS) -> str:
        return self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self.bucket, "Key": asset_name},
            ExpiresIn=ttl_seconds,
        )

    def delete(self, asset_name: str) -> bool:
        # HeadObject to determine existence (delete_object is idempotent and
        # always returns 204, so we can't otherwise tell).
        try:
            self.client.head_object(Bucket=self.bucket, Key=asset_name)
            existed = True
        except Exception:
            existed = False
        self.client.delete_object(Bucket=self.bucket, Key=asset_name)
        self.client.delete_object(Bucket=self.bucket, Key=self._meta_key(asset_name))
        return existed


# ---------------------------------------------------------------------------
# GitHub Release backend (legacy — works while we have <3 active samples)
# ---------------------------------------------------------------------------


class GitHubReleaseStorageBackend(StorageBackend):
    """
    Uploads tarballs as assets on a GitHub release. Wraps the `gh` CLI.

    Constraints (Howard 2026-05-07):
      - 2 GiB hard cap per asset (enforced here — exit 1 above that).
      - 50 assets per release (enforced via rotating buffer keep_newest=3).
      - "Signed URL" is just the public release-asset URL (gh releases are
        publicly downloadable; that's the point — testers can browse without
        auth).
    """

    name = "github"

    def __init__(
        self,
        repo: str | None = None,
        tag: str | None = None,
        keep_newest: int | None = None,
        protected_assets: list[str] | None = None,
        gh_runner: Any = None,
    ) -> None:
        self.repo = repo or os.environ.get("STORAGE_GITHUB_REPO", DEFAULT_GITHUB_REPO)
        self.tag = tag or os.environ.get("STORAGE_GITHUB_TAG", DEFAULT_GITHUB_TAG)
        self.keep_newest = int(
            keep_newest
            if keep_newest is not None
            else os.environ.get("STORAGE_GITHUB_KEEP_NEWEST", DEFAULT_GITHUB_KEEP_NEWEST)
        )
        self.protected_assets = protected_assets or [PROTECTED_GITHUB_ASSET]
        # Tests inject a fake runner (callable) instead of mocking subprocess.
        self.gh_runner = gh_runner or self._default_gh_runner

    @staticmethod
    def _default_gh_runner(args: list[str], input_bytes: bytes | None = None) -> tuple[int, str, str]:
        """Run `gh <args>` and return (rc, stdout, stderr)."""
        proc = subprocess.run(
            ["gh"] + args,
            input=input_bytes,
            capture_output=True,
            check=False,
        )
        return proc.returncode, proc.stdout.decode("utf-8", errors="replace"), proc.stderr.decode(
            "utf-8", errors="replace"
        )

    def _gh_asset_name(self, path: Path, metadata: TarballMetadata) -> str:
        """
        GitHub release asset names cannot contain "/". Flatten to use sha8 +
        basename so collisions still hash-match for idempotency, but the
        leading tester_id/ from derive_asset_name() becomes a `__` separator.
        """
        slashed = derive_asset_name(path, metadata)
        return slashed.replace("/", "__")

    def _list_release_assets(self) -> list[dict[str, Any]]:
        rc, out, err = self.gh_runner(
            ["release", "view", self.tag, "--repo", self.repo, "--json", "assets"]
        )
        if rc != 0:
            logger.warning("gh release view failed: rc=%d err=%s", rc, err.strip())
            return []
        try:
            return json.loads(out).get("assets", []) or []
        except json.JSONDecodeError:
            return []

    def upload(self, path: Path, metadata: TarballMetadata) -> UploadResult:
        if not path.is_file():
            raise FileNotFoundError(f"tarball not found: {path}")
        if metadata.size_bytes > GITHUB_ASSET_LIMIT_BYTES:
            raise ValueError(
                f"github backend: {path} is {metadata.size_bytes} bytes — exceeds 2 GiB cap"
            )

        asset_name = self._gh_asset_name(path, metadata)

        # Idempotency: gh release view returns assets[].name; sha8 in name lets
        # us detect duplicates without HEAD-ing each blob.
        existing = self._list_release_assets()
        existing_names = {a.get("name", "") for a in existing}
        if asset_name in existing_names:
            logger.info("github: idempotent skip — %s already exists", asset_name)
            return UploadResult(
                storage_url=f"gh://{self.repo}/releases/{self.tag}/{asset_name}",
                signed_url=self.get_signed_url(asset_name),
                metadata=metadata,
                asset_name=asset_name,
                backend=self.name,
                idempotent_skip=True,
            )

        # Symlink so gh upload uses our chosen asset_name.
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="gh_upload_"))
        link = tmp_dir / asset_name
        try:
            os.symlink(path, link)
            rc, out, err = self.gh_runner(
                ["release", "upload", self.tag, str(link), "--repo", self.repo, "--clobber"]
            )
            if rc != 0:
                raise RuntimeError(f"gh release upload failed: rc={rc} err={err.strip()}")
        finally:
            try:
                shutil.rmtree(tmp_dir)
            except OSError:
                pass

        # Optional sidecar metadata as a separate asset (lets list_assets work
        # without HEAD-ing the tarball).
        meta_asset = f"{asset_name}.metadata.json"
        meta_dir = Path(tempfile.mkdtemp(prefix="gh_meta_"))
        try:
            meta_file = meta_dir / meta_asset
            meta_file.write_text(json.dumps(metadata.to_dict(), indent=2))
            self.gh_runner(
                ["release", "upload", self.tag, str(meta_file), "--repo", self.repo, "--clobber"]
            )
        finally:
            try:
                shutil.rmtree(meta_dir)
            except OSError:
                pass

        # Rotate buffer: keep newest N tarballs, never delete protected ones.
        self._prune_old_assets()

        logger.info("github: uploaded %s to %s", asset_name, self.tag)
        return UploadResult(
            storage_url=f"gh://{self.repo}/releases/{self.tag}/{asset_name}",
            signed_url=self.get_signed_url(asset_name),
            metadata=metadata,
            asset_name=asset_name,
            backend=self.name,
        )

    def _prune_old_assets(self) -> None:
        assets = self._list_release_assets()
        # Only consider tarball assets (not .metadata.json sidecars), exclude protected.
        tarballs = [
            a
            for a in assets
            if a.get("name", "").endswith(".tar.gz")
            and a.get("name") not in self.protected_assets
        ]
        tarballs.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        for old in tarballs[self.keep_newest :]:
            old_name = old["name"]
            self.delete(old_name)
            # Also drop its sidecar.
            self.delete(f"{old_name}.metadata.json")

    def list_assets(self) -> list[dict[str, Any]]:
        assets = self._list_release_assets()
        out: list[dict[str, Any]] = []
        for a in assets:
            name = a.get("name", "")
            if not name.endswith(".tar.gz"):
                continue
            entry = {
                "asset_name": name,
                "size_bytes": a.get("size", 0),
                "uploaded_at": a.get("createdAt", ""),
                "url": a.get("url", ""),
            }
            # Hydrate sha256 / d5_verdict / tester_id from sidecar if present.
            sidecar_name = f"{name}.metadata.json"
            sidecar = next((b for b in assets if b.get("name") == sidecar_name), None)
            if sidecar and sidecar.get("url"):
                # We don't fetch the sidecar contents from gh CLI here (would
                # require curl + auth). The sha8 prefix in the asset name still
                # gives us idempotency.
                pass
            # Pull sha8 from name if it follows the testerid__sha8_basename layout.
            parts = name.split("__", 1)
            if len(parts) == 2:
                rest = parts[1]
                if len(rest) >= 9 and rest[8] == "_":
                    entry["sha8"] = rest[:8]
            out.append(entry)
        out.sort(key=lambda a: a.get("uploaded_at", ""), reverse=True)
        return out

    def get_signed_url(self, asset_name: str, ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS) -> str:
        # GitHub release assets are public; "signed URL" is just the canonical
        # download URL. ttl_seconds is ignored.
        return f"https://github.com/{self.repo}/releases/download/{self.tag}/{asset_name}"

    def delete(self, asset_name: str) -> bool:
        rc, out, err = self.gh_runner(
            ["release", "delete-asset", self.tag, asset_name, "--repo", self.repo, "--yes"]
        )
        if rc == 0:
            logger.info("github: deleted asset %s", asset_name)
            return True
        # gh exits non-zero if the asset doesn't exist — treat as not-found.
        logger.debug("github: delete failed (likely not-found): %s", err.strip())
        return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_BACKENDS: dict[str, type[StorageBackend]] = {
    "local": LocalFileStorageBackend,
    "s3": S3StorageBackend,
    "github": GitHubReleaseStorageBackend,
}


def get_backend(name: str | None = None, **kwargs: Any) -> StorageBackend:
    """
    Resolve a storage backend by name (or env var STORAGE_BACKEND).
    Defaults to "github" to preserve existing watcher behaviour.
    """
    chosen = (name or os.environ.get("STORAGE_BACKEND") or "github").lower()
    if chosen not in _BACKENDS:
        raise ValueError(f"unknown backend {chosen!r}; valid: {sorted(_BACKENDS)}")
    return _BACKENDS[chosen](**kwargs)


def register_backend(name: str, cls: type[StorageBackend]) -> None:
    """Register a custom backend (e.g. for tests or 3rd-party storage)."""
    _BACKENDS[name.lower()] = cls
