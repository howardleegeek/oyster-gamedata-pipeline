#!/usr/bin/env python3
"""
EPal Companion Quality Score — bin/epal_companion_quality_score.py

Wire EPal companion professional rating (1-5 stars) into clip metadata.
Buyers pay a premium for higher-rated companion captures; the score also
informs cluster training-time data weighting.

Usage:
    python3 bin/epal_companion_quality_score.py embed --rating 4 \
        --companion-id COMP-001 clip_meta.json
    python3 bin/epal_companion_quality_score.py read clip_meta.json
    python3 bin/epal_companion_quality_score.py batch --ratings r.yaml \
        --metadata-dir ./clips/
    python3 bin/epal_companion_quality_score.py sidecar img.png \
        --rating 5 --companion-id COMP-001

Author: G254 Engineering  |  Version: 1.0
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any, Optional

# Lazy imports (vendors may not have these installed)
try:
    import yaml  # type: ignore[import-not-found]
    HAS_YAML: bool = True
except ImportError:
    HAS_YAML = False
try:
    from PIL import Image  # type: ignore[import-not-found]
    HAS_PIL: bool = True
except ImportError:
    HAS_PIL = False

MIN_RATING, MAX_RATING = 1, 5
METADATA_KEY = "epal_companion_quality_score"
METADATA_VERSION = "1.0"
META_EXT = frozenset({".json", ".yaml", ".yml"})
PREMIUM_MULTIPLIERS = {1: 1.00, 2: 1.10, 3: 1.25, 4: 1.50, 5: 2.00}
TRAINING_WEIGHTS = {1: 0.5, 2: 0.7, 3: 1.0, 4: 1.3, 5: 1.8}


class CompanionRating:
    """EPal companion professional rating (1-5 stars).

    Attributes:
        rating: Integer star rating from 1 to 5 inclusive.
        companion_id: Unique identifier for the companion.
        notes: Optional free-text notes about the rating.
    """
    __slots__ = ("rating", "companion_id", "notes")

    def __init__(self, rating: int, companion_id: str, notes: str = "") -> None:
        if not isinstance(rating, int) or not (MIN_RATING <= rating <= MAX_RATING):
            raise ValueError(f"Rating must be int in [{MIN_RATING},{MAX_RATING}], got {rating!r}")
        if not companion_id or not isinstance(companion_id, str):
            raise ValueError("companion_id must be a non-empty string")
        self.rating, self.companion_id, self.notes = rating, companion_id, notes

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON/YAML embedding."""
        return {"rating": self.rating, "companion_id": self.companion_id,
                "notes": self.notes, "version": METADATA_VERSION,
                "premium_multiplier": PREMIUM_MULTIPLIERS[self.rating],
                "training_weight": TRAINING_WEIGHTS[self.rating]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CompanionRating":
        """Deserialise from a dict (ignores extra keys)."""
        return cls(rating=int(d["rating"]), companion_id=str(d["companion_id"]),
                   notes=str(d.get("notes", "")))

    def __repr__(self) -> str:
        return f"CompanionRating(rating={self.rating}, companion_id={self.companion_id!r})"


def read_metadata(path: Path) -> dict[str, Any]:
    """Read clip metadata from a JSON or YAML file.

    Args:
        path: Path to the metadata file.

    Returns:
        Parsed metadata dict.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file extension is unsupported.
        ImportError: If PyYAML is needed but not installed.
    """
    if not path.exists():
        raise FileNotFoundError(f"Metadata file not found: {path}")
    ext, text = path.suffix.lower(), path.read_text(encoding="utf-8")
    if ext == ".json":
        return json.loads(text)
    if ext in (".yaml", ".yml"):
        if not HAS_YAML:
            raise ImportError("PyYAML is required to read YAML metadata")
        return yaml.safe_load(text) or {}
    raise ValueError(f"Unsupported metadata extension: {ext}")


def write_metadata(path: Path, data: dict[str, Any]) -> None:
    """Write clip metadata to a JSON or YAML file.

    Args:
        path: Destination path (extension determines format).
        data: Metadata dict to serialise.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext == ".json":
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    elif ext in (".yaml", ".yml"):
        if not HAS_YAML:
            raise ImportError("PyYAML is required to write YAML metadata")
        path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
                        encoding="utf-8")
    else:
        raise ValueError(f"Unsupported metadata extension: {ext}")


def embed_rating_as_sidecar(image_path: Path, rating: CompanionRating) -> Path:
    """Create a JSON sidecar file alongside an image with the rating embedded.

    Args:
        image_path: Path to the source image.
        rating: The companion rating to embed.

    Returns:
        Path to the created sidecar file.
    """
    if not HAS_PIL:
        raise ImportError("Pillow (PIL) is required for image sidecar embedding")
    with Image.open(image_path) as img:
        img.verify()
    sidecar = image_path.with_suffix(image_path.suffix + ".rating.json")
    sidecar.write_text(json.dumps({METADATA_KEY: rating.to_dict()}, indent=2),
                       encoding="utf-8")
    return sidecar


def read_rating_from_sidecar(image_path: Path) -> Optional[CompanionRating]:
    """Read a companion rating from an image's sidecar JSON file.

    Args:
        image_path: Path to the source image.

    Returns:
        CompanionRating if a sidecar exists, else None.
    """
    sidecar = image_path.with_suffix(image_path.suffix + ".rating.json")
    if not sidecar.exists():
        return None
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    raw = data.get(METADATA_KEY)
    return CompanionRating.from_dict(raw) if raw else None


def batch_apply(ratings_path: Path, metadata_dir: Path,
                dry_run: bool = False) -> list[dict[str, Any]]:
    """Apply companion ratings from a ratings file to all metadata in a dir.

    The ratings file (JSON or YAML) should be a mapping of companion_id ->
    rating (int 1-5) or a list of dicts with 'companion_id' and 'rating' keys.

    Args:
        ratings_path: Path to the ratings source file.
        metadata_dir: Directory containing clip metadata files.
        dry_run: If True, report what would be done without writing.

    Returns:
        List of result dicts describing each processed file.
    """
    ratings_data = read_metadata(ratings_path)
    rating_map: dict[str, int] = {}
    if isinstance(ratings_data, dict):
        for cid, val in ratings_data.items():
            rating_map[cid] = int(val.get("rating", val)) if isinstance(val, dict) else int(val)
    elif isinstance(ratings_data, list):
        for entry in ratings_data:
            cid = entry.get("companion_id", entry.get("id", ""))
            rating_map[cid] = int(entry.get("rating", 0))
    results: list[dict[str, Any]] = []
    for mf in sorted(metadata_dir.glob("*")):
        if mf.suffix.lower() not in META_EXT:
            continue
        try:
            meta = read_metadata(mf)
        except Exception as exc:
            results.append({"file": str(mf), "status": "error", "detail": str(exc)})
            continue
        companion_id = meta.get("companion_id", "")
        if companion_id not in rating_map:
            results.append({"file": str(mf), "status": "skipped",
                            "detail": f"no rating for {companion_id!r}"})
            continue
        r = CompanionRating(rating=rating_map[companion_id], companion_id=companion_id)
        meta[METADATA_KEY] = r.to_dict()
        if not dry_run:
            write_metadata(mf, meta)
        results.append({"file": str(mf), "status": "updated", "rating": r.rating,
                        "premium_multiplier": PREMIUM_MULTIPLIERS[r.rating],
                        "training_weight": TRAINING_WEIGHTS[r.rating]})
    return results


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI parser."""
    parser = argparse.ArgumentParser(
        prog="epal_companion_quality_score",
        description="Wire EPal companion ratings (1-5 stars) into clip metadata.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("embed", help="Embed a rating into a metadata file")
    p.add_argument("metadata", type=Path, help="Path to metadata file")
    p.add_argument("--rating", type=int, required=True, help=f"Rating ({MIN_RATING}-{MAX_RATING})")
    p.add_argument("--companion-id", required=True, help="Companion unique identifier")
    p.add_argument("--notes", default="", help="Optional rating notes")
    p.add_argument("--output", type=Path, default=None, help="Output path (default: overwrite)")
    p = sub.add_parser("read", help="Read embedded rating from metadata")
    p.add_argument("metadata", type=Path, help="Path to metadata file")
    p = sub.add_parser("batch", help="Batch-apply ratings to a directory")
    p.add_argument("--ratings", type=Path, required=True, help="Ratings source (JSON/YAML)")
    p.add_argument("--metadata-dir", type=Path, required=True, help="Dir with metadata files")
    p.add_argument("--dry-run", action="store_true", help="Report without writing")
    p = sub.add_parser("sidecar", help="Create/read image sidecar rating")
    p.add_argument("image", type=Path, help="Path to image file")
    p.add_argument("--rating", type=int, default=None, help=f"Rating ({MIN_RATING}-{MAX_RATING})")
    p.add_argument("--companion-id", default=None, help="Companion ID (required with --rating)")
    p.add_argument("--notes", default="", help="Optional notes")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 = success, non-zero = failure).
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "embed":
            rating = CompanionRating(rating=args.rating, companion_id=args.companion_id,
                                     notes=args.notes)
            meta = read_metadata(args.metadata)
            meta[METADATA_KEY] = rating.to_dict()
            out = args.output or args.metadata
            write_metadata(out, meta)
            print(f"[embed] Wrote rating={rating.rating} for {rating.companion_id} -> {out}")
        elif args.command == "read":
            meta = read_metadata(args.metadata)
            raw = meta.get(METADATA_KEY)
            if raw is None:
                print(f"[read] No {METADATA_KEY} found in {args.metadata}")
                return 1
            print(json.dumps(CompanionRating.from_dict(raw).to_dict(), indent=2))
        elif args.command == "batch":
            results = batch_apply(args.ratings, args.metadata_dir, dry_run=args.dry_run)
            for r in results:
                print(json.dumps(r, ensure_ascii=False))
            ok = sum(1 for r in results if r["status"] == "updated")
            print(f"[batch] {ok}/{len(results)} files updated")
        elif args.command == "sidecar":
            if args.rating is not None:
                if not args.companion_id:
                    parser.error("--companion-id is required with --rating")
                rating = CompanionRating(rating=args.rating, companion_id=args.companion_id,
                                         notes=args.notes)
                sidecar_path = embed_rating_as_sidecar(args.image, rating)
                print(f"[sidecar] Wrote {sidecar_path}")
            else:
                r = read_rating_from_sidecar(args.image)
                if r is None:
                    print(f"[sidecar] No rating sidecar for {args.image}")
                    return 1
                print(json.dumps(r.to_dict(), indent=2))
        else:
            parser.print_help()
            return 2
    except (ValueError, FileNotFoundError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
