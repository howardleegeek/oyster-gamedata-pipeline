#!/usr/bin/env python3
"""
upload_tarball_signed.py — Recorder-side client for the Gap #8 signed-URL flow.

The legacy /api/upload-tarball POSTed the binary through Next.js, which 413s
on Vercel for any tarball larger than 4.5 MB. This client speaks the new
three-call protocol that bypasses Vercel entirely:

    1. POST /api/upload-tarball/sign   (small JSON, returns a signed PUT URL)
    2. PUT  <signed_url>                (binary goes recorder -> Supabase)
    3. POST /api/upload-tarball/finalize (small JSON, server verifies)

This is the canonical implementation. The Rust recorder mirrors the same
protocol — see vendor/recorder/src/upload.rs once it lands. Any shell
pipeline that today calls bin/upload_tarball.py against the legacy route
should switch to this script (or set BACKEND="vercel-signed" on the
storage_backend dispatcher once that lands).

USAGE:
    bin/upload_tarball_signed.py /tmp/swarm_real_X.tar.gz \\
        --tester-id <uuid> \\
        --duration-seconds 1800 \\
        --base-url https://tester.oysterworld.dev \\
        [--auth-secret <hex>]      # default $TESTER_AUTH_HMAC_SECRET
        [--sha256 <hex>]           # default: auto-compute
        [--timeout 600]            # PUT timeout, seconds
        [--verbose]

Output: single JSON line on stdout
    {"id":"...", "tester_id":"...", "sha256":"...", "size_bytes":..., "accepted":true}

Exit codes:
    0 = uploaded / accepted (incl. duplicate)
    1 = bad input / missing file
    2 = HTTP auth / validation rejection (4xx)
    3 = server / network failure (5xx)

Howard 2026-05-13.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerr
from urllib import request as urlreq

logger = logging.getLogger("oyster.upload_signed")

DEFAULT_TIMEOUT_SEC = 600  # 10 min — generous for a slow uplink uploading 1 GiB
DEFAULT_CHUNK_BYTES = 1 << 20  # 1 MiB streaming chunks for sha256


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def compute_sha256_streaming(path: Path) -> str:
    """Stream-hash a file. 1 MiB chunks keep memory bounded for multi-GB tarballs."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(DEFAULT_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_hmac_header(tester_id: str, secret: str, body: bytes) -> str:
    """X-Tester-Auth: 'v1 <tester_id> <ts_ms> <hex_sig>'.

    Signature = HMAC-SHA256(secret, tester_id || '\\n' || ts_ms || '\\n' || sha256(body))
    Must match web-tester/lib/tester-auth.ts verifyHmac().
    """
    ts_ms = int(time.time() * 1000)
    body_sha = hashlib.sha256(body).hexdigest()
    payload = f"{tester_id}\n{ts_ms}\n{body_sha}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"v1 {tester_id} {ts_ms} {sig}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class UploadError(Exception):
    """Base class — `status` is HTTP status if available, 0 for network errors."""

    def __init__(self, message: str, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class ClientError(UploadError):
    """4xx — bad request, auth, validation. Don't retry."""


class ServerError(UploadError):
    """5xx or network. Caller may retry."""


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

def _json_request(
    *,
    method: str,
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    req = urlreq.Request(url, data=body, method=method, headers=headers)
    try:
        with urlreq.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urlerr.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        if 400 <= e.code < 500:
            raise ClientError(f"{method} {url} -> {e.code}", status=e.code, body=err_body) from e
        raise ServerError(f"{method} {url} -> {e.code}", status=e.code, body=err_body) from e
    except urlerr.URLError as e:
        raise ServerError(f"{method} {url}: {e}", status=0) from e
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise ServerError(f"non-JSON response from {url}: {raw[:200]!r}", status=200) from e


def _binary_put(*, url: str, path: Path, content_type: str, timeout: int) -> int:
    """PUT a file's bytes. Returns HTTP status. Raises on network failure."""
    size = path.stat().st_size
    with path.open("rb") as f:
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(size),
            "x-upsert": "true",
        }
        req = urlreq.Request(url, data=f, method="PUT", headers=headers)
        try:
            with urlreq.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urlerr.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if 400 <= e.code < 500:
                raise ClientError(
                    f"PUT {url} -> {e.code}", status=e.code, body=err_body
                ) from e
            raise ServerError(f"PUT {url} -> {e.code}", status=e.code, body=err_body) from e
        except urlerr.URLError as e:
            raise ServerError(f"PUT {url}: {e}", status=0) from e


# ---------------------------------------------------------------------------
# Public API — the three protocol calls + an orchestrator.
# ---------------------------------------------------------------------------

@dataclass
class SignResponse:
    tarball_id: str
    signed_url: str
    storage_bucket: str
    storage_path: str
    expires_at: str
    ttl_seconds: int
    already_uploaded: bool

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SignResponse":
        return cls(
            tarball_id=d["tarball_id"],
            signed_url=d.get("signed_url", ""),
            storage_bucket=d.get("storage_bucket", ""),
            storage_path=d.get("storage_path", ""),
            expires_at=d.get("expires_at", ""),
            ttl_seconds=int(d.get("ttl_seconds", 0)),
            already_uploaded=bool(d.get("already_uploaded", False)),
        )


def sign(
    *,
    base_url: str,
    tester_id: str,
    filename: str,
    size_bytes: int,
    sha256: str,
    duration_seconds: int,
    auth_secret: str = "",
    timeout: int = 60,
) -> SignResponse:
    body_obj = {
        "tester_id": tester_id,
        "filename": filename,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "duration_seconds": duration_seconds,
    }
    body = json.dumps(body_obj).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_secret:
        headers["X-Tester-Auth"] = build_hmac_header(tester_id, auth_secret, body)
    resp = _json_request(
        method="POST",
        url=f"{base_url.rstrip('/')}/api/upload-tarball/sign",
        body=body,
        headers=headers,
        timeout=timeout,
    )
    return SignResponse.from_dict(resp)


def put_blob(*, signed_url: str, path: Path, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
    status = _binary_put(
        url=signed_url, path=path, content_type="application/gzip", timeout=timeout
    )
    if status >= 300:
        raise ServerError(f"signed-URL PUT returned {status}", status=status)


def finalize(
    *,
    base_url: str,
    tester_id: str,
    tarball_id: str,
    sha256: str,
    auth_secret: str = "",
    timeout: int = 60,
) -> dict[str, Any]:
    body_obj = {"tarball_id": tarball_id, "sha256": sha256}
    body = json.dumps(body_obj).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_secret:
        headers["X-Tester-Auth"] = build_hmac_header(tester_id, auth_secret, body)
    return _json_request(
        method="POST",
        url=f"{base_url.rstrip('/')}/api/upload-tarball/finalize",
        body=body,
        headers=headers,
        timeout=timeout,
    )


def upload(
    *,
    base_url: str,
    tester_id: str,
    path: Path,
    duration_seconds: int,
    sha256: str | None = None,
    auth_secret: str = "",
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Run the full three-step upload protocol. Returns the finalize response."""
    if not path.is_file():
        raise ClientError(f"tarball not found: {path}")
    size_bytes = path.stat().st_size
    sha = (sha256 or compute_sha256_streaming(path)).lower()

    logger.info("sign step: %s bytes=%s sha256=%s", path.name, size_bytes, sha)
    sign_resp = sign(
        base_url=base_url,
        tester_id=tester_id,
        filename=path.name,
        size_bytes=size_bytes,
        sha256=sha,
        duration_seconds=duration_seconds,
        auth_secret=auth_secret,
        timeout=min(timeout, 60),
    )

    if sign_resp.already_uploaded:
        logger.info("sign reported already_uploaded — skipping PUT")
    else:
        logger.info("PUT step: %s -> %s", path.name, sign_resp.storage_path)
        put_blob(signed_url=sign_resp.signed_url, path=path, timeout=timeout)

    logger.info("finalize step: tarball_id=%s", sign_resp.tarball_id)
    fin = finalize(
        base_url=base_url,
        tester_id=tester_id,
        tarball_id=sign_resp.tarball_id,
        sha256=sha,
        auth_secret=auth_secret,
        timeout=min(timeout, 60),
    )
    return fin


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("tarball", type=Path, help="Path to the .tar.gz tarball.")
    p.add_argument("--tester-id", required=True, help="UUID of the tester.")
    p.add_argument(
        "--duration-seconds",
        required=True,
        type=int,
        help="Billable duration of the recording (>=1, <=43200).",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("UPLOAD_BASE_URL", ""),
        help="Tester portal URL (default $UPLOAD_BASE_URL).",
    )
    p.add_argument(
        "--auth-secret",
        default=os.environ.get("TESTER_AUTH_HMAC_SECRET", ""),
        help="Per-tester HMAC secret (default $TESTER_AUTH_HMAC_SECRET). "
        "Optional only when the server is also in stub_mode mode (gap #6 not yet deployed).",
    )
    p.add_argument(
        "--sha256",
        default=None,
        help="Pre-computed sha256 (skip re-hash). 64 lowercase hex.",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SEC,
        help="PUT timeout in seconds (default 600).",
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if not args.base_url:
        print("ERROR: --base-url or $UPLOAD_BASE_URL is required", file=sys.stderr)
        return 1

    # Validate tester-id shape early so the server doesn't have to roundtrip.
    try:
        uuid.UUID(args.tester_id)
    except ValueError:
        print(f"ERROR: tester-id is not a UUID: {args.tester_id}", file=sys.stderr)
        return 1

    try:
        result = upload(
            base_url=args.base_url,
            tester_id=args.tester_id,
            path=args.tarball,
            duration_seconds=args.duration_seconds,
            sha256=args.sha256,
            auth_secret=args.auth_secret,
            timeout=args.timeout,
        )
    except ClientError as e:
        print(f"ERROR (4xx): {e} :: {e.body[:300]}", file=sys.stderr)
        return 2
    except ServerError as e:
        print(f"ERROR (5xx/network): {e} :: {e.body[:300]}", file=sys.stderr)
        return 3

    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
