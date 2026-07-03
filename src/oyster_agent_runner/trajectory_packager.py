"""Phase 1 trajectory bundle packager — buyer-ready zip + provenance + checksums.

Phase 1 produces a four-file bundle on disk:

    trajectory_<id>/
    ├── manifest.json
    ├── cot.jsonl
    ├── metadata.jsonl
    └── inputs.jsonl

To hand this to a buyer who already integrates with Oyster L1 enrichment
bundles, we wrap it in the same envelope they're used to:

  1. ``provenance.json`` — who packaged this, when, against which git SHA,
     against which package version, and which model/provider produced it.
  2. ``packaged.manifest.json`` — extends the original manifest with a
     ``checksums`` block (sha256 per stream) so a buyer can verify
     transport integrity without re-implementing our schema.
  3. A zip archive containing all of the above plus the original four
     stream files.

Pure stdlib (``hashlib`` + ``zipfile`` + ``subprocess``). No new pip deps.

Cross-reference: ``oyster-enrichment/bin/package_for_buyer.py`` is the
reference implementation for the L1 packager. We deliberately do NOT
import or depend on it — that codebase is shipped separately and we want
buyers to be able to consume Phase 1 bundles even when they only have
the agent-runner installed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import zipfile
from datetime import datetime, timezone

UTC = timezone.utc

UTC = UTC
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from oyster_agent_runner.minecraft_streams import (
    COT_FILENAME,
    INPUTS_FILENAME,
    MANIFEST_FILENAME,
    METADATA_FILENAME,
)
from oyster_agent_runner.replay import ConsistencyReport, Replayer

PACKAGED_MANIFEST_FILENAME = "packaged.manifest.json"
PROVENANCE_FILENAME = "provenance.json"

PROVENANCE_SCHEMA_VERSION = "1.0.0"

# Streams the packager checksums and bundles. Order is stable for
# deterministic provenance output.
_STREAM_FILENAMES: tuple[str, ...] = (
    COT_FILENAME,
    METADATA_FILENAME,
    INPUTS_FILENAME,
)


class TrajectoryPackager:
    """Wrap a Phase 1 bundle directory into a buyer-ready zip.

    Parameters
    ----------
    bundle_dir:
        Path to a directory containing ``manifest.json``, ``cot.jsonl``,
        ``metadata.jsonl``, and ``inputs.jsonl`` — the four-file layout
        produced by :class:`oyster_agent_runner.minecraft_streams.MinecraftStreamWriter`.

    Raises
    ------
    FileNotFoundError
        If the bundle directory does not exist or any of the four
        canonical stream files are missing. The intent is to fail loudly
        at construction time rather than mid-package.
    """

    def __init__(self, bundle_dir: Path) -> None:
        self.bundle_dir = Path(bundle_dir)
        if not self.bundle_dir.is_dir():
            raise FileNotFoundError(f"bundle directory not found: {self.bundle_dir}")
        self.manifest_path = self.bundle_dir / MANIFEST_FILENAME
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"missing required manifest at {self.manifest_path}")
        for stream_name in _STREAM_FILENAMES:
            stream_path = self.bundle_dir / stream_name
            if not stream_path.exists():
                raise FileNotFoundError(
                    f"missing required stream {stream_name} in bundle {self.bundle_dir}"
                )

    # --- Provenance ----------------------------------------------------------

    def compute_provenance(self) -> dict[str, Any]:
        """Build the ``provenance.json`` payload.

        The shape mirrors the L1 enrichment packager: a top-level
        ``schema_version`` plus blocks for collection (recorder/runner
        metadata), runtime (git SHA + package version), and rights.

        ``git_sha`` falls back to ``"unknown"`` when:
          * the cwd is not a git repo (e.g. installed wheel)
          * ``git`` is not on PATH
          * the subprocess returns non-zero for any reason
        """
        manifest = self._load_manifest()

        return {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "packaged_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "runtime": {
                "git_sha": self._git_sha(),
                "package_name": "oyster-agent-runner",
                "package_version": self._package_version(),
            },
            "collection": {
                "method": "oyster-agent-runner Phase 1 trajectory pipeline",
                "task_id": manifest.get("task_id", "unknown"),
                "environment": manifest.get("environment", "unknown"),
                "provider": manifest.get("provider", "unknown"),
                "model": manifest.get("model", "unknown"),
                "phase": manifest.get("phase", 1),
                "session_start_wall_clock_utc": manifest.get(
                    "session_start_wall_clock_utc", "unknown"
                ),
            },
            "rights_delivered": {
                "use": "training of AI world models, embodied agents, RL policies",
                "redistribution": (
                    "forbidden — buyer retains training-derived weights; "
                    "raw trajectory streams not resellable"
                ),
                "license": manifest.get("license", "train-only"),
            },
        }

    # --- Checksums -----------------------------------------------------------

    def compute_checksums(self) -> dict[str, str]:
        """SHA-256 every stream file and the original manifest.

        Returns a mapping ``{filename: hex_digest}`` so the bundle
        recipient can re-derive and compare. Hashes are stable across
        runs (no timestamps in the input).

        The original ``manifest.json`` is included so callers can detect
        if the manifest was tampered with after streams were written.
        """
        digests: dict[str, str] = {}
        for stream_name in (*_STREAM_FILENAMES, MANIFEST_FILENAME):
            digests[stream_name] = _sha256_of(self.bundle_dir / stream_name)
        return digests

    # --- Packaging -----------------------------------------------------------

    def package(self, output_path: Path, *, format: str = "zip") -> Path:
        """Emit ``provenance.json`` + ``packaged.manifest.json`` and zip.

        Parameters
        ----------
        output_path:
            Destination zip path. Parent directories are created.
        format:
            Currently only ``"zip"`` is supported. Reserved for future
            ``"tar.gz"`` parity with other Oyster bundle formats.

        Returns
        -------
        Path
            The resolved output path.

        Notes
        -----
        We DO NOT write provenance / packaged.manifest into the bundle
        directory — that would be a side effect on the source. Instead
        we generate them in memory and write directly into the zip.
        This keeps :class:`TrajectoryPackager` idempotent.
        """
        if format != "zip":
            raise ValueError(f"unsupported format: {format!r} (only 'zip' supported)")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        provenance = self.compute_provenance()
        checksums = self.compute_checksums()
        original_manifest = self._load_manifest()
        packaged_manifest = self._extend_manifest(original_manifest, checksums)

        # Open with explicit compression. ZIP_DEFLATED is the universal
        # choice — buyers using Python stdlib, Java, or `unzip` will all
        # decompress without extra deps.
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            # 1. Streams (verbatim from the bundle).
            for stream_name in _STREAM_FILENAMES:
                zf.write(self.bundle_dir / stream_name, stream_name)
            # 2. Original manifest (preserve buyer-visible audit trail).
            zf.write(self.manifest_path, MANIFEST_FILENAME)
            # 3. Wrappers.
            zf.writestr(
                PROVENANCE_FILENAME,
                json.dumps(provenance, indent=2, sort_keys=True),
            )
            zf.writestr(
                PACKAGED_MANIFEST_FILENAME,
                json.dumps(packaged_manifest, indent=2, sort_keys=True),
            )
        return output_path

    # --- Verification --------------------------------------------------------

    def verify_package(self, zip_path: Path) -> ConsistencyReport:
        """Round-trip verify: extract zip, recompute checksums, replay-check.

        Returns a :class:`ConsistencyReport` (same type
        :class:`Replayer` returns) so callers can treat verification of
        an unpacked zip identically to verification of a raw bundle
        directory.

        Failure modes flagged:
          * Missing required entry in zip
          * Stream digest does not match the digest stored in
            ``packaged.manifest.json``
          * Underlying bundle fails :meth:`Replayer.verify_consistency`
            after extraction (orphan events, manifest count drift, etc.)
        """
        zip_path = Path(zip_path)
        report = ConsistencyReport(ok=True)

        if not zip_path.exists():
            report.add(f"zip not found: {zip_path}")
            return report

        # Extract into a temp directory under the zip's parent so the
        # caller can inspect the unpacked tree if they want to debug.
        # tempfile.TemporaryDirectory would be ideal but we want the
        # extraction to live as long as the report so callers can re-run
        # checks offline. We use a deterministic neighbour name.
        extract_root = zip_path.parent / (zip_path.stem + "_unpacked")
        extract_root.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = set(zf.namelist())
                required = {
                    *_STREAM_FILENAMES,
                    MANIFEST_FILENAME,
                    PROVENANCE_FILENAME,
                    PACKAGED_MANIFEST_FILENAME,
                }
                missing = required - names
                if missing:
                    for name in sorted(missing):
                        report.add(f"missing required entry in zip: {name}")
                    return report
                zf.extractall(extract_root)
        except zipfile.BadZipFile as exc:
            report.add(f"zip is not a valid archive: {exc}")
            return report

        # Recompute digests and compare against packaged.manifest.json.
        try:
            packaged_manifest = json.loads(
                (extract_root / PACKAGED_MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            report.add(f"packaged.manifest.json is not valid JSON: {exc}")
            return report

        recorded_checksums = (packaged_manifest.get("checksums") or {}).get("sha256", {})
        if not recorded_checksums:
            report.add("packaged.manifest.json missing checksums.sha256 block")
            return report

        for stream_name in (*_STREAM_FILENAMES, MANIFEST_FILENAME):
            extracted_path = extract_root / stream_name
            if not extracted_path.exists():
                report.add(f"extracted file missing: {stream_name}")
                continue
            actual = _sha256_of(extracted_path)
            expected = recorded_checksums.get(stream_name)
            if expected is None:
                report.add(f"checksum missing for {stream_name}")
            elif actual != expected:
                report.add(
                    f"checksum mismatch for {stream_name}: "
                    f"expected {expected[:12]}..., got {actual[:12]}..."
                )

        # Even if checksums match, replay structural consistency to be
        # sure the bundle is decodable end-to-end. Replayer reads the
        # ORIGINAL manifest.json, not packaged.manifest.json, so we let
        # it walk the bundle from the canonical entry point.
        try:
            replayer = Replayer(extract_root / MANIFEST_FILENAME)
            replay_report = replayer.verify_consistency()
            if not replay_report.ok:
                for issue in replay_report.issues:
                    report.add(f"replay issue: {issue}")
            # Surface counts onto the outer report so callers don't need
            # to re-instantiate a Replayer just to learn step_count.
            report.cot_event_count = replay_report.cot_event_count
            report.metadata_event_count = replay_report.metadata_event_count
            report.input_event_count = replay_report.input_event_count
            report.max_timestamp_sec = replay_report.max_timestamp_sec
            report.step_count = replay_report.step_count
            report.manifest_matches_streams = replay_report.manifest_matches_streams
        except (FileNotFoundError, ValueError) as exc:
            report.add(f"could not replay extracted bundle: {exc}")

        return report

    # --- Internal helpers ----------------------------------------------------

    def _load_manifest(self) -> dict[str, Any]:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover — guarded at __init__
            raise ValueError(f"manifest is not valid JSON: {exc}") from exc

    def _extend_manifest(
        self,
        original: dict[str, Any],
        checksums: dict[str, str],
    ) -> dict[str, Any]:
        """Return a deep-copied manifest with a ``checksums`` block appended.

        The original fields are never mutated; key order is preserved by
        Python ``dict`` insertion semantics so downstream tooling that
        relies on ordered traversal is unaffected.
        """
        # ``json.loads(json.dumps(...))`` is the cheap, dep-free way to
        # deep-copy a JSON-serializable dict — we immediately serialize
        # the result anyway, so the round-trip cost is amortized.
        extended = json.loads(json.dumps(original))
        extended["checksums"] = {
            "algorithm": "sha256",
            "sha256": dict(checksums),
        }
        return extended

    @staticmethod
    def _git_sha() -> str:
        """Return the current ``HEAD`` SHA, or ``"unknown"`` on any failure."""
        try:
            result = subprocess.run(  # noqa: S603 — safe fixed argv
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.debug("git_sha: falling back to 'unknown' (subprocess failed: %s: %s)", type(exc).__name__, exc)
            return "unknown"
        if result.returncode != 0:
            logger.debug("git_sha: falling back to 'unknown' (git returned rc=%s)", result.returncode)
            return "unknown"
        sha = result.stdout.strip()
        return sha or "unknown"

    @staticmethod
    def _package_version() -> str:
        """Return the installed ``oyster-agent-runner`` version, or ``"unknown"``."""
        try:
            return importlib_metadata.version("oyster-agent-runner")
        except importlib_metadata.PackageNotFoundError as exc:
            logger.debug("package_version: falling back to 'unknown' (metadata lookup failed: %s: %s)", type(exc).__name__, exc)
            return "unknown"


def _sha256_of(path: Path) -> str:
    """Streaming SHA-256 of a file, 64 KiB at a time."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = [
    "PACKAGED_MANIFEST_FILENAME",
    "PROVENANCE_FILENAME",
    "PROVENANCE_SCHEMA_VERSION",
    "TrajectoryPackager",
]
