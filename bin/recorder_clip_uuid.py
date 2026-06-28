#!/usr/bin/env python3
"""recorder_clip_uuid.py — Generate per-clip UUID4, insert into systeminfo,
attach metadata, and append UUID suffix to filenames for cross-machine uniqueness.

Usage:
    python3 bin/recorder_clip_uuid.py --clip-dir /path/to/clips [--db-path systeminfo.db]
    python3 bin/recorder_clip_uuid.py --clip-id clip_001 --output-json metadata.json

Closes C6.
"""
from __future__ import annotations
import argparse, json, logging, os, sqlite3, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """CREATE TABLE IF NOT EXISTS clip_uuids (
    clip_id TEXT PRIMARY KEY, clip_uuid TEXT NOT NULL,
    hostname TEXT, created_at TEXT, filename TEXT);"""


def generate_clip_uuid() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())


def suffix_filename(fp: Path, clip_uuid: str) -> Path:
    """Insert short UUID before extension: video.mp4 → video-a1b2c3d4.mp4."""
    return fp.with_name(f"{fp.stem}-{clip_uuid.split('-')[0]}{fp.suffix}")


def build_metadata(
    clip_id: str, clip_uuid: str,
    filepath: Optional[Path] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a metadata dict for a single clip."""
    meta: Dict[str, Any] = {
        "clip_id": clip_id, "clip_uuid": clip_uuid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hostname": os.uname().nodename,
    }
    if filepath is not None:
        meta["original_filename"] = filepath.name
        meta["suffixed_filename"] = suffix_filename(filepath, clip_uuid).name
        meta["file_size_bytes"] = filepath.stat().st_size if filepath.exists() else None
    if extra:
        meta.update(extra)
    return meta


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open/create the systeminfo SQLite database and ensure schema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def insert_clip_record(
    conn: sqlite3.Connection, clip_id: str, clip_uuid: str,
    hostname: str, created_at: str, filename: Optional[str] = None,
) -> None:
    """Upsert a clip-uuid row into the clip_uuids table."""
    conn.execute(
        "INSERT INTO clip_uuids VALUES (?,?,?,?,?) "
        "ON CONFLICT(clip_id) DO UPDATE SET "
        "clip_uuid=excluded.clip_uuid, hostname=excluded.hostname, "
        "created_at=excluded.created_at, filename=excluded.filename",
        (clip_id, clip_uuid, hostname, created_at, filename),
    )
    conn.commit()


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for this CLI.

    Returns:
        argparse.ArgumentParser: Configured parser with arguments for clip-dir,
            clip-id, db-path, output-json, dry-run, and verbose options.
    """
    p = argparse.ArgumentParser(
        description="Generate per-clip UUID4, persist to systeminfo, suffix filenames.")
    p.add_argument("--clip-dir", type=Path, help="Directory of clip files to process.")
    p.add_argument("--clip-id", type=str, help="Single clip identifier.")
    p.add_argument("--db-path", type=Path, default=Path("systeminfo.db"),
                   help="Path to systeminfo SQLite DB (default: systeminfo.db).")
    p.add_argument("--output-json", type=Path, help="Write metadata JSON here.")
    p.add_argument("--dry-run", action="store_true", help="No disk/DB writes.")
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Generate per-clip UUIDs and optionally persist to database.
    
    Supports two modes:
    - Single clip: Generate UUID for a single clip_id and optionally write to DB.
    - Directory: Process all files in a directory, generating UUIDs and renaming files.
    
    Args:
        argv: Command-line arguments (default: sys.argv).
    
    Returns:
        Exit code (0 for success, 1 for errors).
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
    )
    results: List[Dict[str, Any]] = []
    db_abs = args.db_path.resolve() if args.db_path else None

    if args.clip_id and not args.clip_dir:
        clip_uuid = generate_clip_uuid()
        meta = build_metadata(args.clip_id, clip_uuid)
        results.append(meta)
        logger.info("clip_id=%s  uuid=%s", args.clip_id, clip_uuid)
        if not args.dry_run:
            conn = init_db(args.db_path)
            insert_clip_record(conn, args.clip_id, clip_uuid,
                               meta["hostname"], meta["created_at"])
            conn.close()

    elif args.clip_dir:
        if not args.clip_dir.is_dir():
            logger.error("--clip-dir '%s' is not a directory", args.clip_dir)
            return 1
        conn = init_db(args.db_path)
        for entry in sorted(args.clip_dir.iterdir()):
            if not entry.is_file():
                continue
            if db_abs and entry.resolve() == db_abs:
                continue
            clip_id, clip_uuid = entry.stem, generate_clip_uuid()
            new_path = suffix_filename(entry, clip_uuid)
            meta = build_metadata(clip_id, clip_uuid, filepath=entry)
            results.append(meta)
            logger.info("%s → %s  (uuid=%s)", entry.name, new_path.name, clip_uuid)
            if not args.dry_run:
                insert_clip_record(conn, clip_id, clip_uuid,
                                   meta["hostname"], meta["created_at"], entry.name)
                entry.rename(new_path)
        conn.close()

    else:
        parser.print_help()
        return 1

    if args.output_json and results:
        args.output_json.write_text(json.dumps(results, indent=2) + "\n")
        logger.info("Wrote metadata to %s", args.output_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
