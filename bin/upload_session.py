#!/usr/bin/env python3
"""upload_session — One-Click Tester Session Uploader
=======================================================

Tester finishes a recording, runs ONE command, the entire session lands in
our S3 bucket via the production backend. No tar+网盘+微信 chain.

Flow:
  1. Discover the newest session under %USERPROFILE%\\Documents\\OysterClips\\
  2. Sanity-check (files exist, mp4 ≥ 1 MB)
  3. Zip the session (compress for upload)
  4. POST /api/v1/upload/signed-url    → get presigned S3 PUT URL
  5. PUT zip to that URL               → S3 upload
  6. POST /api/v1/sessions             → register session in backend DB
  7. Print success URL + dashboard link

Token comes from (in order):
  1. --token CLI flag
  2. OYSTER_RECORDER_TOKEN env var
  3. ~/.oyster-recorder-token file (one line)
  4. %LOCALAPPDATA%\\OysterRecorder\\token.txt (Windows)

Pure stdlib + standard urllib. No pip-install needed.

Exit codes:
  0 = uploaded successfully
  1 = upload failed (network / 4xx / 5xx)
  2 = pre-check fail (no session / missing files)
  3 = no token configured
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from urllib import request, error

# ---------------------------------------------------------------------------

DEFAULT_BACKEND = "http://136.109.41.170:8081"
REQUIRED_FILES = ("recording.mp4", "game_state.jsonl", "inputs.jsonl", "metadata.json")
DASHBOARD_BASE = "https://oysterrecorder.com/dashboard"  # placeholder

# ---------------------------------------------------------------------------
# token discovery
# ---------------------------------------------------------------------------


def resolve_token(cli_token: str | None) -> str | None:
    """Try the 4 token sources in order. Return None if all fail."""
    if cli_token:
        return cli_token.strip()
    env = os.environ.get("OYSTER_RECORDER_TOKEN")
    if env:
        return env.strip()
    for cand in (
        Path.home() / ".oyster-recorder-token",
        Path(os.environ.get("LOCALAPPDATA", "")) / "OysterRecorder" / "token.txt",
    ):
        if cand.exists():
            return cand.read_text(encoding="utf-8").strip()
    return None


# ---------------------------------------------------------------------------
# session discovery
# ---------------------------------------------------------------------------


def discover_session(explicit: Path | None) -> Path | None:
    if explicit:
        return explicit if explicit.is_dir() else None
    for root in (
        Path(os.environ.get("USERPROFILE", "")) / "Documents" / "OysterClips",
        Path.home() / "Documents" / "OysterClips",
        Path.home() / "Downloads" / "OysterClips",
    ):
        if not root.is_dir():
            continue
        sessions = sorted(
            (p for p in root.glob("session_*") if p.is_dir()),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if sessions:
            return sessions[0]
    return None


def precheck(session: Path) -> tuple[bool, list[str]]:
    """Lightweight sanity check before zipping."""
    problems: list[str] = []
    for name in REQUIRED_FILES:
        p = session / name
        if not p.exists():
            problems.append(f"missing: {name}")
        elif p.stat().st_size == 0:
            problems.append(f"empty: {name}")
    mp4 = session / "recording.mp4"
    if mp4.exists() and mp4.stat().st_size < 1_000_000:
        problems.append(f"recording.mp4 only {mp4.stat().st_size}B — truncated?")
    return (not problems), problems


# ---------------------------------------------------------------------------
# zip
# ---------------------------------------------------------------------------


def zip_session(session: Path, dest: Path) -> int:
    """Zip session dir → dest. Returns final zip size in bytes."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in session.rglob("*"):
            if not path.is_file():
                continue
            arcname = path.relative_to(session.parent)
            zf.write(path, arcname)
    return dest.stat().st_size


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------


def http_post_json(url: str, body: dict, token: str, timeout: int = 60) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "upload_session.py/0.1",
        },
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_put_file(url: str, file_path: Path, timeout: int = 1800) -> int:
    """PUT a file to a presigned URL. Returns HTTP status."""
    size = file_path.stat().st_size
    with file_path.open("rb") as fp:
        req = request.Request(
            url, data=fp, method="PUT",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
            },
        )
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("session_path", nargs="?", default=None,
                        help="Path to session_* directory (default: newest in OysterClips)")
    parser.add_argument("--token", default=None,
                        help="Bearer token (or set OYSTER_RECORDER_TOKEN env)")
    parser.add_argument("--backend", default=os.environ.get("OYSTER_BACKEND", DEFAULT_BACKEND),
                        help=f"Backend base URL (default: {DEFAULT_BACKEND})")
    parser.add_argument("--keep-zip", action="store_true",
                        help="Don't delete the temp zip after upload")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run all checks + zip but skip actual upload")
    args = parser.parse_args(argv)

    print("Oyster Recorder Session Uploader v0.1")
    print()

    # 1. Token
    token = resolve_token(args.token)
    if not token and not args.dry_run:
        print("ERROR: no Bearer token found.")
        print("  Set --token, OYSTER_RECORDER_TOKEN env, or ~/.oyster-recorder-token")
        print()
        print("  Get a token via the Discord OAuth flow OR ask Howard.")
        return 3

    # 2. Session discovery
    session = discover_session(Path(args.session_path) if args.session_path else None)
    if session is None:
        print("ERROR: no session_* found under Documents/OysterClips/")
        return 2
    print(f"session: {session}")

    # 3. Pre-check
    ok, problems = precheck(session)
    if not ok:
        print("ERROR: session incomplete:")
        for p in problems:
            print(f"  • {p}")
        return 2
    print("pre-check: OK")

    # 4. Zip
    zip_path = session.parent / f"{session.name}.zip"
    print(f"zipping → {zip_path.name} ...")
    t0 = time.time()
    zip_size = zip_session(session, zip_path)
    elapsed = time.time() - t0
    print(f"  zipped: {zip_size / 1024 / 1024:.1f} MB in {elapsed:.1f}s")

    if args.dry_run:
        print()
        print(f"DRY-RUN ok. Zip kept at: {zip_path}")
        return 0

    try:
        # 5. Request presigned URL
        print(f"\nrequesting signed URL from {args.backend} ...")
        signed = http_post_json(
            f"{args.backend}/api/v1/upload/signed-url",
            {"session_id": session.name, "filename": zip_path.name,
             "size_bytes": zip_size, "content_type": "application/zip"},
            token,
        )
        upload_url = signed.get("upload_url") or signed.get("url")
        s3_key = signed.get("key") or session.name
        if not upload_url:
            print(f"  ERROR: backend returned no upload_url: {signed}")
            return 1
        print(f"  got URL (key={s3_key})")

        # 6. PUT the zip
        print(f"\nuploading {zip_size / 1024 / 1024:.1f} MB ...")
        t0 = time.time()
        status = http_put_file(upload_url, zip_path)
        elapsed = time.time() - t0
        if status not in (200, 204):
            print(f"  ERROR: upload returned HTTP {status}")
            return 1
        mbps = (zip_size / 1024 / 1024) / max(elapsed, 0.001)
        print(f"  uploaded in {elapsed:.1f}s ({mbps:.1f} MB/s)")

        # 7. Finalize session
        print("\nregistering session in backend ...")
        meta_path = session / "metadata.json"
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            meta = {}
        finalize = http_post_json(
            f"{args.backend}/api/v1/sessions",
            {
                "session_id": session.name,
                "s3_key": s3_key,
                "size_bytes": zip_size,
                "recorder_version": meta.get("recorder_version", "unknown"),
                "duration_sec": meta.get("duration_sec", 0),
            },
            token,
        )
        print(f"  registered: {finalize.get('id', s3_key)}")

        print()
        print("━" * 50)
        print("✅ SESSION UPLOADED")
        print(f"   key: {s3_key}")
        print(f"   size: {zip_size / 1024 / 1024:.1f} MB")
        print(f"   dashboard: {DASHBOARD_BASE}/sessions/{s3_key}")
        print("━" * 50)

    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        print(f"\nERROR: HTTP {e.code}: {body}")
        return 1
    except error.URLError as e:
        print(f"\nERROR: network: {e.reason}")
        return 1
    finally:
        if not args.keep_zip and zip_path.exists():
            zip_path.unlink()

    return 0


if __name__ == "__main__":
    sys.exit(main())
