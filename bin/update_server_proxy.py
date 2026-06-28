#!/usr/bin/env python3
"""
G250 · Update Server Proxy (GitHub Releases backed)

Production endpoint serving `GET /api/recorder-update?current=<ver>` which
proxies the GitHub Releases API for `howardleegeek/oyster-gamedata-pipeline`
and returns:

    {
        "latest":        "v0.28.0-rc19.0.1",
        "installer_url": "https://github.com/.../OysterRecorder-setup.exe",
        "release_notes": "...",
        "force":         false,
        "current":       "v0.28.0-rc19.0.0",
        "update_available": true
    }

Why a Python module (not just a Next.js route):
    1. Recorder side (PyInstaller .exe) can call this CLI directly when the
       buyer/tester web is down — `python -m bin.update_server_proxy
       --current v0.28.0-rc19.0.0` prints JSON to stdout.
    2. The TS Next.js route in web-buyer/web-tester can shell-out to this
       module from a Vercel build step, OR replicate the cache logic in TS.
    3. Pytest can exercise the full cache + GitHub-shape parsing without
       node tooling.

Cache:
    In-process LRU + TTL (5 min default) keyed by `repo`.  Honours the
    `--no-cache` CLI flag and the `G250_DISABLE_CACHE=1` env var so we
    can re-pull manually during release rollouts.

Security:
    - We pass the GH token (if present) via Authorization header only;
      never log it.
    - We constrain output to the documented JSON shape; no echo of GH
      response fields that could leak labels/branches we don't want
      visible.
    - We hard-cap response body size at 1 MiB so a malicious GitHub
      response can't OOM us.

Iron-law (Howard 2026-05-07): no demo / fallback `latest` strings. If
GitHub is unreachable and the cache is empty, we raise `UpstreamError`
which the HTTP layer surfaces as 502.

Usage (CLI):
    python -m bin.update_server_proxy --current v0.28.0-rc19.0.0
    python -m bin.update_server_proxy --current v0.28.0-rc19.0.0 \
        --repo howardleegeek/oyster-gamedata-pipeline --no-cache

Usage (library):
    from bin.update_server_proxy import resolve_update, UpstreamError
    info = resolve_update("v0.28.0-rc19.0.0")
    print(info["installer_url"])
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REPO: str = "howardleegeek/oyster-gamedata-pipeline"
GITHUB_API_TEMPLATE: str = "https://api.github.com/repos/{repo}/releases/latest"
DEFAULT_CACHE_TTL_SECONDS: int = 300  # 5 minutes per spec
MAX_GH_RESPONSE_BYTES: int = 1 << 20  # 1 MiB
HTTP_TIMEOUT_SECONDS: float = 8.0
USER_AGENT: str = "oyster-update-server/1.0 (+howardleegeek/oyster-gamedata-pipeline)"

# Asset suffix priority for installer_url selection (Windows-first since
# the recorder is currently Windows-only — v0.3.0 will add macOS .pkg).
INSTALLER_ASSET_SUFFIXES: tuple[str, ...] = (
    "-setup.exe",
    ".msi",
    ".exe",
    ".pkg",
    ".dmg",
)

# Release-note label that means "force update" — body line starts with
# this literal token (case-insensitive).
FORCE_TOKEN: str = "[FORCE]"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UpstreamError(RuntimeError):
    """GitHub returned an error or could not be reached, and cache is empty."""


class InvalidVersionError(ValueError):
    """The supplied ``current`` parameter is not a recognised version string."""


# ---------------------------------------------------------------------------
# Version parsing (semver-ish, tolerant of rcN.X.Y suffixes used by the recorder)
# ---------------------------------------------------------------------------

_VERSION_RE: re.Pattern[str] = re.compile(
    r"""
    ^v?                              # optional leading v
    (?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)
    (?:-(?P<pre>[A-Za-z0-9.\-]+))?   # pre-release tag, e.g. rc19.0.1
    $
    """,
    re.VERBOSE,
)


def parse_version(raw: str) -> tuple[tuple[int, int, int], tuple[Any, ...]]:
    """Parse a recorder version into (semver_tuple, prerelease_tuple).

    The prerelease tuple is a sequence of segment tuples whose first
    element is always ``0`` (numeric) or ``1`` (alpha) so comparisons
    are type-safe.  Numeric runs are coerced to ints so ``rc19`` <
    ``rc20`` < ``rc100``.

    An empty pre-release tuple means "final release" and compares
    greater than any non-empty prerelease at the same semver (handled
    in :func:`_compare_pre_tuples`).

    Raises:
        InvalidVersionError: when the string doesn't match the expected
            shape.
    """
    if raw is None:
        raise InvalidVersionError("version is None")
    m = _VERSION_RE.match(raw.strip())
    if m is None:
        raise InvalidVersionError(f"cannot parse version string: {raw!r}")
    semver = (int(m.group("major")), int(m.group("minor")), int(m.group("patch")))
    pre = m.group("pre")
    if pre is None:
        # Empty prerelease tuple => final release => greater than any pre.
        return semver, ()
    parts: list[Any] = []
    for piece in pre.split("."):
        if piece.isdigit():
            # (0, n) so numeric segments sort before alpha (per semver intent).
            parts.append((0, int(piece)))
        else:
            am = re.match(r"^([A-Za-z]+)(\d+)?$", piece)
            if am and am.group(2):
                parts.append((1, am.group(1).lower(), int(am.group(2))))
            else:
                parts.append((1, piece.lower()))
    return semver, tuple(parts)


def _compare_pre_tuples(a: tuple[Any, ...], b: tuple[Any, ...]) -> int:
    """Return -1/0/1 ordering, with empty tuple > non-empty (final > pre)."""
    if not a and not b:
        return 0
    if not a:
        return 1  # final > pre
    if not b:
        return -1
    # Pad shorter side with the smallest possible segment so e.g.
    # rc19.0.1 > rc19 (more identifiers wins per semver §11).
    pad = (0, -1)
    length = max(len(a), len(b))
    for i in range(length):
        ai = a[i] if i < len(a) else pad
        bi = b[i] if i < len(b) else pad
        if ai == bi:
            continue
        # Tuples are type-homogeneous now: (0/1, ...).
        if ai > bi:
            return 1
        if ai < bi:
            return -1
    return 0


def is_newer(latest: str, current: str) -> bool:
    """Return True iff ``latest`` is strictly newer than ``current``."""
    l_semver, l_pre = parse_version(latest)
    c_semver, c_pre = parse_version(current)
    if l_semver != c_semver:
        return l_semver > c_semver
    return _compare_pre_tuples(l_pre, c_pre) > 0


# ---------------------------------------------------------------------------
# TTL cache (thread-safe, single-process)
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    payload: dict[str, Any]
    expires_at: float


class TTLCache:
    """Tiny thread-safe TTL cache keyed by string.

    No external deps; the GitHub-releases lookup hits this before going
    out over the network.  Module-level singleton :data:`_CACHE` is the
    one used by :func:`resolve_update`.  Tests can construct their own.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str, *, now: Optional[float] = None) -> Optional[dict[str, Any]]:
        t = time.time() if now is None else now
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at < t:
                self._store.pop(key, None)
                return None
            return entry.payload

    def set(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        now: Optional[float] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        t = time.time() if now is None else now
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        with self._lock:
            self._store[key] = _CacheEntry(payload=payload, expires_at=t + ttl)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)


_CACHE: TTLCache = TTLCache()


def reset_cache() -> None:
    """Test hook — clears the module-level cache."""
    _CACHE.clear()


# ---------------------------------------------------------------------------
# GitHub API client (urllib only, zero new deps)
# ---------------------------------------------------------------------------


FetchFn = Callable[[str, dict[str, str]], dict[str, Any]]


def _default_github_fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
    """Real GitHub releases fetcher.  Bounded body, JSON-parsed."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read(MAX_GH_RESPONSE_BYTES + 1)
            if len(raw) > MAX_GH_RESPONSE_BYTES:
                raise UpstreamError(f"GitHub response exceeded {MAX_GH_RESPONSE_BYTES} bytes")
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise UpstreamError(f"malformed JSON from GitHub: {exc}") from exc
    except urllib.error.HTTPError as exc:
        # Surface 404 distinctly so callers can tell repo-missing from
        # transient network failure.
        raise UpstreamError(f"GitHub HTTPError {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise UpstreamError(f"GitHub unreachable: {exc.reason}") from exc


def _build_headers(token: Optional[str]) -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _select_installer_url(assets: list[dict[str, Any]]) -> Optional[str]:
    """Pick the best installer asset given the priority suffix list.

    Returns the ``browser_download_url`` of the highest-priority match,
    or None if nothing recognisable was attached.
    """
    if not isinstance(assets, list):
        return None
    by_name: dict[str, str] = {}
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        url = a.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            by_name[name] = url
    for suffix in INSTALLER_ASSET_SUFFIXES:
        for name, url in by_name.items():
            if name.lower().endswith(suffix):
                return url
    return None


def _is_force_release(body: Optional[str]) -> bool:
    """Detect a force-update release by leading ``[FORCE]`` token in the body."""
    if not isinstance(body, str) or not body:
        return False
    return any(line.strip().upper().startswith(FORCE_TOKEN) for line in body.splitlines())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_update(
    current: str,
    *,
    repo: str = DEFAULT_REPO,
    cache: Optional[TTLCache] = None,
    fetch: Optional[FetchFn] = None,
    token: Optional[str] = None,
    use_cache: bool = True,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Resolve the latest available update info for ``current``.

    Args:
        current: the recorder version the caller is running.
        repo: ``owner/name`` GitHub repository.
        cache: optional TTL cache (mostly for tests).
        fetch: pluggable GitHub fetcher (mostly for tests).
        token: optional GitHub bearer token (recommended in production
            to lift the 60-req/h anonymous limit).
        use_cache: when False, bypasses the cache and refreshes from
            GitHub regardless.
        now: optional clock override (for tests).

    Returns:
        ``{"latest", "installer_url", "release_notes", "force",
           "current", "update_available"}``.

    Raises:
        UpstreamError: GitHub failed and the cache was empty.
        InvalidVersionError: ``current`` could not be parsed.
    """
    # Validate `current` early so a malformed param never reaches GitHub.
    parse_version(current)

    cache = cache if cache is not None else _CACHE
    cache_key = f"latest:{repo}"

    payload: Optional[dict[str, Any]] = None
    if use_cache and os.environ.get("G250_DISABLE_CACHE", "") != "1":
        payload = cache.get(cache_key, now=now)

    if payload is None:
        # Resolve fetcher at call time so monkeypatching the module
        # attribute works for tests.
        fetcher = fetch if fetch is not None else _default_github_fetch
        url = GITHUB_API_TEMPLATE.format(repo=repo)
        headers = _build_headers(token or os.environ.get("GITHUB_TOKEN"))
        payload = fetcher(url, headers)
        if not isinstance(payload, dict):
            raise UpstreamError("GitHub returned non-object payload")
        cache.set(cache_key, payload, now=now)

    latest_tag = payload.get("tag_name")
    if not isinstance(latest_tag, str) or not latest_tag:
        raise UpstreamError("GitHub payload missing tag_name")

    installer = _select_installer_url(payload.get("assets") or [])
    notes = payload.get("body") or ""
    force = _is_force_release(notes) if isinstance(notes, str) else False

    try:
        update_available = is_newer(latest_tag, current)
    except InvalidVersionError:
        # If `latest_tag` itself is unparseable, treat as no-update rather
        # than blowing up the recorder — but still surface the upstream
        # value so ops can see it.
        update_available = False

    return {
        "latest": latest_tag,
        "installer_url": installer or "",
        "release_notes": notes,
        "force": bool(force),
        "current": current,
        "update_available": bool(update_available),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update_server_proxy",
        description=(
            "G250 · Resolve the latest recorder release from GitHub "
            "and emit JSON suitable for the /api/recorder-update endpoint."
        ),
    )
    parser.add_argument(
        "--current",
        required=True,
        help="Current recorder version (e.g. v0.28.0-rc19.0.1)",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub repo to query (default: {DEFAULT_REPO})",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the in-process TTL cache for this lookup.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub token (also accepted via GITHUB_TOKEN env var).",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=None,
        help=(
            f"Override cache TTL for this invocation only (default: {DEFAULT_CACHE_TTL_SECONDS})."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point.  Exit code 0 on success, 2 on input error, 1 on upstream."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    cache: Optional[TTLCache] = None
    if args.ttl_seconds is not None:
        cache = TTLCache(ttl_seconds=args.ttl_seconds)

    try:
        info = resolve_update(
            current=args.current,
            repo=args.repo,
            cache=cache,
            token=args.token,
            use_cache=not args.no_cache,
        )
    except InvalidVersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except UpstreamError as exc:
        print(f"error: upstream failure: {exc}", file=sys.stderr)
        return 1

    json.dump(info, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
