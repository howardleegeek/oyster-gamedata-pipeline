#!/usr/bin/env python3
"""upload_to_web_tester.py — HTTP reference client for ``/api/upload-tarball``.

Production gap #6: this is the canonical Python implementation of an
authenticated upload to the tester web portal. The Rust recorder under
``vendor/recorder/`` mirrors this contract:

1. Read the per-tester HMAC token (full 64-hex digest) from one of:
   - ``--token`` CLI flag (explicit override),
   - ``OYSTER_UPLOAD_TOKEN`` env var,
   - the 16-hex prefix parsed from the .exe filename
     (``OysterRecorder-<short>-<uuid>-<token16>.exe``).

2. POST multipart/form-data to ``{base_url}/api/upload-tarball`` with:
   - form fields: ``tester_id``, ``duration_seconds``, ``sha256``, ``tarball``
   - header: ``X-Upload-Token: <token>``

3. Server verifies ``HMAC_SHA256(UPLOAD_HMAC_SECRET, tester_id)`` matches
   the presented token. When ``UPLOAD_REQUIRE_TOKEN=true`` and the token
   is missing/invalid, the server returns 401.

USAGE
=====

    bin/upload_to_web_tester.py /tmp/swarm_real_X.tar.gz \\
        --base-url https://oyster-tester.vercel.app \\
        --tester-id 11111111-2222-3333-4444-555555555555 \\
        --duration-seconds 3600 \\
        --token 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

Or compute the token locally (when you control the HMAC secret):

    UPLOAD_HMAC_SECRET=$(cat ~/.oyster-keys/upload-hmac.secret) \\
    bin/upload_to_web_tester.py /tmp/swarm_real_X.tar.gz \\
        --base-url http://localhost:3000 \\
        --tester-id 11111111-2222-3333-4444-555555555555 \\
        --duration-seconds 3600

Exit codes
----------
    0 = success
    1 = bad input / missing tarball
    2 = HTTP error (4xx/5xx)
    3 = network error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

# Allow ``python bin/upload_to_web_tester.py`` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bin.upload_auth import (  # noqa: E402
    UPLOAD_TOKEN_HEADER,
    UploadAuthConfig,
    compute_token,
)

LOG = logging.getLogger("upload_to_web_tester")

_FILENAME_TOKEN_RE = re.compile(
    r"OysterRecorder-[0-9a-f]{8}-"
    r"(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"-(?P<token>[0-9a-f]{16})\.exe",
    re.IGNORECASE,
)


def parse_token_from_exe_name(exe_path: str | os.PathLike) -> Optional[str]:
    """Extract the 16-hex token from the recorder .exe filename, or None.

    Pure parser — no I/O, no env reads. Returns the 16-char hex token if
    the filename matches the production naming convention, else None.
    """
    name = Path(exe_path).name
    m = _FILENAME_TOKEN_RE.match(name)
    if m is None:
        return None
    return m.group("token").lower()


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_token(
    *,
    explicit: Optional[str],
    tester_id: str,
    exe_path: Optional[str],
    env: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Resolve the upload token from CLI/env/filename/HMAC-secret sources.

    Resolution order (highest priority first):
        1. ``--token`` CLI flag
        2. ``OYSTER_UPLOAD_TOKEN`` env var
        3. 16-char prefix extracted from the recorder .exe filename
        4. Locally computed via ``UPLOAD_HMAC_SECRET`` env var
           (only useful in dev / when the operator controls the secret)

    Returns None when no token source is configured.
    """
    e = env if env is not None else os.environ
    if explicit:
        return explicit.strip().lower()
    env_token = e.get("OYSTER_UPLOAD_TOKEN", "").strip().lower()
    if env_token:
        return env_token
    if exe_path:
        from_name = parse_token_from_exe_name(exe_path)
        if from_name:
            return from_name
    secret = e.get("UPLOAD_HMAC_SECRET", "")
    if secret:
        return compute_token(tester_id, secret)
    return None


def upload(
    *,
    tarball: Path,
    base_url: str,
    tester_id: str,
    duration_seconds: int,
    token: Optional[str],
    sha256: Optional[str] = None,
    timeout: float = 600.0,
) -> dict:
    """POST the tarball to ``{base_url}/api/upload-tarball``.

    Imports ``requests`` lazily so the module is importable for unit
    tests that mock out the HTTP layer without paying the import cost.
    """
    import requests  # type: ignore[import-not-found]

    url = base_url.rstrip("/") + "/api/upload-tarball"

    headers: dict[str, str] = {}
    if token:
        headers[UPLOAD_TOKEN_HEADER] = token
    else:
        LOG.warning("no upload token resolved — server may 401 if " "UPLOAD_REQUIRE_TOKEN=true")

    sha = sha256 or compute_sha256(tarball)
    LOG.info("uploading %s (sha256=%s) → %s", tarball, sha, url)

    with tarball.open("rb") as fh:
        files = {"tarball": (tarball.name, fh, "application/gzip")}
        data = {
            "tester_id": tester_id,
            "duration_seconds": str(duration_seconds),
            "sha256": sha,
        }
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=timeout)

    if resp.status_code >= 400:
        # Bubble the body up — the route returns structured JSON we want
        # to surface for diagnostics.
        try:
            detail = resp.json()
        except Exception as e:  # pragma: no cover - defensive
            LOG.debug("upload_to_web_tester: failed to parse error JSON (status=%s): %s",
                      resp.status_code, e)
            detail = {"raw": resp.text[:500]}
        raise SystemExit(
            json.dumps(
                {"http_status": resp.status_code, "error": detail},
                separators=(",", ":"),
            )
        )

    return resp.json()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("tarball", type=Path, help="Path to the .tar.gz tarball.")
    p.add_argument("--base-url", required=True, help="Web-tester base URL (e.g. https://...).")
    p.add_argument("--tester-id", required=True, help="Tester UUID.")
    p.add_argument("--duration-seconds", type=int, required=True, help="Billable seconds.")
    p.add_argument("--token", default=None, help="HMAC token (override).")
    p.add_argument(
        "--exe-path",
        default=None,
        help="Recorder .exe path to extract the token prefix from.",
    )
    p.add_argument("--sha256", default=None, help="Pre-computed sha256 (skips re-hash).")
    p.add_argument("--timeout", type=float, default=600.0, help="HTTP timeout seconds.")
    p.add_argument("--verbose", action="store_true", help="Enable INFO logs on stderr.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not args.tarball.is_file():
        print(f"ERROR: tarball not found: {args.tarball}", file=sys.stderr)
        return 1

    token = resolve_token(
        explicit=args.token,
        tester_id=args.tester_id,
        exe_path=args.exe_path,
    )

    # Surface the upload-auth config to the operator so misconfig is obvious.
    cfg = UploadAuthConfig.from_env()
    if cfg.require_token and not token:
        LOG.error(
            "server-side UPLOAD_REQUIRE_TOKEN=true but no token resolved; "
            "expect a 401 from the server"
        )

    try:
        result = upload(
            tarball=args.tarball,
            base_url=args.base_url,
            tester_id=args.tester_id,
            duration_seconds=args.duration_seconds,
            token=token,
            sha256=args.sha256,
            timeout=args.timeout,
        )
    except SystemExit as e:
        # ``upload()`` raised with a JSON-formatted error string.
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: network error: {e}", file=sys.stderr)
        return 3

    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
