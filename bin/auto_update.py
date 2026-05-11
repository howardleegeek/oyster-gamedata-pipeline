#!/usr/bin/env python3
"""bin/auto_update.py — rc16.14 auto-update check scaffold (Stage 2 prep).

Banked. NOT wired into oyster_play.py yet — wiring is a follow-up.

Public surface (matches rc16.14 spec): ReleaseInfo, get_current_version,
check_for_updates, show_update_dialog, download_update, run_auto_update_check.

Stdlib only. Never raises (logs + returns None / "" instead). Throttled to
once / 24h via %LOCALAPPDATA%/Oyster Recorder/last_update_check.json
(Mac: ~/Library/Application Support/Oyster Recorder/).
"""
from __future__ import annotations
import hashlib, json, logging, os, re, sys, time
import urllib.error, urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Keep in sync with recorder_consumer_lite.RECORDER_VERSION. We avoid
# importing that module — too heavy for a string. get_current_version()
# falls back to bundle/manifest.json if this ships detached.
RECORDER_VERSION = "lite-v0.28.0-rc16.13"
_DEFAULT_REPO = "howardleegeek/oyster-gamedata-pipeline"
_GITHUB_API = "https://api.github.com"
_USER_AGENT = "OysterRecorder-AutoUpdate/rc16.14"
_CHECK_THROTTLE_SECONDS = 24 * 60 * 60
_HTTP_TIMEOUT = 15
_DOWNLOAD_TIMEOUT = 300
_DIALOG_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ReleaseInfo:
    """A release available on GitHub."""
    tag: str                      # "recorder-v0.28.0-rc16.9"
    version: str                  # "rc16.9"
    published_at: str             # ISO timestamp from GH API
    installer_url: str            # direct download URL
    installer_size_mb: float
    release_notes: str            # markdown body (may be empty)
    is_newer: bool                # True iff version > current

def _appdata_dir() -> Path:
    """%LOCALAPPDATA%/Oyster Recorder + Mac/Linux fallbacks (matches oyster_play)."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "Oyster Recorder"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Oyster Recorder"
    else:
        base = Path.home() / ".local" / "share" / "oyster-recorder"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("auto_update: cannot create %s: %s", base, e)
    return base

def _parse_version_tuple(version: str) -> Tuple[int, ...]:
    """'rc16.9' / 'recorder-v0.28.0-rc16.9' → int tuple. Empty → (0,)."""
    if not version:
        return (0,)
    segment = version.rsplit("-v", 1)[-1] if "-v" in version else version
    nums = re.findall(r"\d+", segment)
    return tuple(int(n) for n in nums) if nums else (0,)

def _extract_rc_suffix(tag_or_version: str) -> str:
    """'recorder-v0.28.0-rc16.9' → 'rc16.9'. Returns input on miss."""
    m = re.search(r"(rc\d+(?:\.\d+)?)", tag_or_version)
    return m.group(1) if m else tag_or_version


def get_current_version() -> str:
    """Installed version. Falls back to bundle/manifest.json, then 'unknown'."""
    if RECORDER_VERSION:
        return RECORDER_VERSION
    try:
        manifest_path = Path(__file__).resolve().parent.parent / "bundle" / "manifest.json"
        if manifest_path.is_file():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            v = data.get("recorder_version") or data.get("version")
            if isinstance(v, str) and v:
                return v
    except (OSError, ValueError, KeyError) as e:
        logger.debug("auto_update: manifest fallback failed: %s", e)
    return "unknown"

def _cache_path() -> Path:
    return _appdata_dir() / "last_update_check.json"

def _should_check_now(force: bool = False) -> bool:
    """True iff >24h since last check, or never checked, or force=True."""
    if force:
        return True
    cache = _cache_path()
    if not cache.is_file():
        return True
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return (time.time() - float(data.get("last_check_epoch", 0))) >= _CHECK_THROTTLE_SECONDS
    except (OSError, ValueError, TypeError) as e:
        logger.debug("auto_update: stale cache, will recheck: %s", e)
        return True

def _record_check(version_seen: Optional[str]) -> None:
    payload = {
        "last_check_epoch": time.time(),
        "last_check_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version_seen": version_seen or "",
        "current_version": get_current_version(),
    }
    try:
        _cache_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("auto_update: cache write failed: %s", e)

def _http_get(url: str, as_json: bool = True):
    """GET url. Returns parsed JSON dict/list (or text if as_json=False). None on failure."""
    headers = {"User-Agent": _USER_AGENT}
    if as_json:
        headers["Accept"] = "application/vnd.github+json"
    req = urllib.request.Request(url, headers=headers)
    if as_json and os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if as_json else raw
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError, OSError) as e:
        logger.info("auto_update: GET %s failed: %s", url, e)
        return None

def _pick_installer_asset(assets: list) -> Optional[dict]:
    """Prefer OysterRecorder-Setup-*.exe; fall back to any 'Setup'/.exe."""
    candidates = [(a, a.get("name", "")) for a in assets]
    for prefix in ("OysterRecorder-Setup-", "Setup"):
        for a, n in candidates:
            if prefix in n and n.endswith(".exe"):
                return a
    return None

def _release_to_info(release: dict, current_version: str) -> Optional[ReleaseInfo]:
    try:
        tag = release.get("tag_name") or ""
        if not tag:
            return None
        installer = _pick_installer_asset(release.get("assets") or [])
        if installer is None:
            logger.info("auto_update: release %s has no installer asset", tag)
            return None
        size_mb = float(installer.get("size", 0)) / (1024 * 1024)
        is_newer = _parse_version_tuple(tag) > _parse_version_tuple(current_version)
        return ReleaseInfo(
            tag=tag,
            version=_extract_rc_suffix(tag) or tag,
            published_at=release.get("published_at") or "",
            installer_url=installer.get("browser_download_url") or "",
            installer_size_mb=round(size_mb, 1),
            release_notes=(release.get("body") or "")[:8000],
            is_newer=is_newer,
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("auto_update: release parse failed: %s", e)
        return None


def check_for_updates(repo: str = _DEFAULT_REPO,
                      channel: str = "stable") -> Optional[ReleaseInfo]:
    """Query GitHub Releases API. None on any failure.

    channel: 'stable' (/releases/latest, no prereleases), 'beta' (/releases,
    includes prereleases), 'dev' (/releases, includes drafts; needs GITHUB_TOKEN).
    """
    current = get_current_version()
    try:
        if channel == "stable":
            release = _http_get(f"{_GITHUB_API}/repos/{repo}/releases/latest")
            info = _release_to_info(release, current) if release else None
        else:
            releases = _http_get(f"{_GITHUB_API}/repos/{repo}/releases?per_page=20")
            if not isinstance(releases, list):
                return None
            if channel not in ("beta", "dev"):
                logger.warning("auto_update: unknown channel %r → stable filter", channel)
            info = None
            for r in releases:
                if r.get("draft") and channel != "dev":
                    continue
                if r.get("prerelease") and channel not in ("beta", "dev"):
                    continue
                info = _release_to_info(r, current)
                if info is not None:
                    break
        if info is not None:
            _record_check(info.tag)
        return info
    except Exception as e:  # belt-and-suspenders — never raise
        logger.warning("auto_update: check_for_updates crashed: %s", e)
        return None


_DIALOG_STRINGS = {
    "en": {"title": "Oyster Recorder — Update Available",
           "header": "A newer Oyster Recorder is available.",
           "current": "Installed:", "new": "Available:", "size": "Download size:",
           "notes": "Release notes:", "update": "Update now",
           "later": "Remind me later", "skip": "Skip this version",
           "auto_default": "(Auto-defaulting to 'Remind me later' in 30s.)"},
    "zh": {"title": "Oyster Recorder — 有可用更新",
           "header": "Oyster Recorder 有新版本可下载。",
           "current": "当前版本:", "new": "新版本:", "size": "下载大小:",
           "notes": "更新说明:", "update": "立即更新",
           "later": "稍后提醒", "skip": "跳过此版本",
           "auto_default": "(30 秒后自动选择 \"稍后提醒\"。)"},
}


def show_update_dialog(info: ReleaseInfo, lang: str = "en") -> str:
    """Tk dialog. Returns 'update' | 'skip' | 'later'. 30s default → 'later'."""
    s = _DIALOG_STRINGS.get(lang, _DIALOG_STRINGS["en"])
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        logger.info("auto_update: Tk unavailable, defaulting to 'later'")
        return "later"
    result = {"choice": "later"}
    try:
        root = tk.Tk()
        root.title(s["title"])
        root.geometry("520x420")
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        def _label(text: str, **kw) -> None:
            ttk.Label(frame, text=text, **kw).pack(anchor="w", pady=kw.pop("pady", 0))
        _label(s["header"], font=("TkDefaultFont", 12, "bold"))
        _label(f"{s['current']} {get_current_version()}", pady=(8, 0))
        _label(f"{s['new']} {info.tag}")
        _label(f"{s['size']} {info.installer_size_mb:.1f} MB")
        _label(s["notes"], pady=(12, 4))
        notes_box = tk.Text(frame, height=8, wrap="word")
        notes_box.insert("1.0", info.release_notes or "(no notes)")
        notes_box.config(state="disabled")
        notes_box.pack(fill="both", expand=True)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(12, 0))
        def _close(choice: str) -> None:
            result["choice"] = choice
            root.destroy()
        for choice, side, padx in (("skip", "left", 0), ("later", "left", 8), ("update", "right", 0)):
            ttk.Button(btn_frame, text=s[choice],
                       command=lambda c=choice: _close(c)).pack(side=side, padx=padx)
        ttk.Label(frame, text=s["auto_default"], foreground="#888").pack(anchor="w", pady=(8, 0))
        root.after(_DIALOG_TIMEOUT_SECONDS * 1000, lambda: _close("later"))
        root.mainloop()
    except Exception as e:
        logger.warning("auto_update: Tk dialog crashed: %s", e)
        return "later"
    return result["choice"]

def _parse_sha_manifest(manifest_text: str) -> dict:
    """SHA-256-manifest.txt → {filename: hex}. Format '<hash> [*]<filename>'."""
    out: dict = {}
    for line in (manifest_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+\*?", line, maxsplit=1)
        if len(parts) != 2:
            continue
        sha, name = parts[0].strip().lower(), parts[1].strip()
        if re.fullmatch(r"[0-9a-f]{64}", sha):
            out[name] = sha
    return out

def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def download_update(info: ReleaseInfo, dest_dir: Path) -> Path:
    """Download installer (SHA-verified) into dest_dir. Raises on checksum mismatch."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    manifest_url = info.installer_url.rsplit("/", 1)[0] + "/SHA-256-manifest.txt"
    manifest_text = _http_get(manifest_url, as_json=False)
    if not manifest_text:
        raise ValueError(f"could not fetch SHA-256 manifest: {manifest_url}")
    manifest = _parse_sha_manifest(manifest_text)

    installer_name = info.installer_url.rsplit("/", 1)[-1]
    expected_sha = manifest.get(installer_name)
    if not expected_sha:
        raise ValueError(f"installer {installer_name!r} not in SHA-256-manifest.txt")

    final_path = dest_dir / installer_name
    tmp_path = final_path.with_suffix(final_path.suffix + ".part")

    req = urllib.request.Request(info.installer_url,
                                 headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)

    actual_sha = _sha256_of_file(tmp_path)
    if actual_sha != expected_sha:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise ValueError(
            f"SHA-256 mismatch for {installer_name}: "
            f"expected {expected_sha}, got {actual_sha}"
        )
    tmp_path.replace(final_path)
    return final_path

def _skipped_tags(add: Optional[str] = None) -> list:
    """Read skipped_versions.json. If `add` is set, append + persist."""
    p = _appdata_dir() / "skipped_versions.json"
    tags: list = []
    try:
        if p.is_file():
            tags = list(json.loads(p.read_text(encoding="utf-8")).get("tags") or [])
    except (OSError, ValueError) as e:
        logger.debug("auto_update: skip-list read failed: %s", e)
    if add and add not in tags:
        tags.append(add)
        try:
            p.write_text(json.dumps({"tags": tags}, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("auto_update: skip-list write failed: %s", e)
    return tags


def run_auto_update_check(lang: str = "en") -> Optional[Path]:
    """Throttle → check → prompt → (maybe) download. Returns installer path
    on accept, else None. Never raises. Throttled to once / 24h."""
    try:
        if not _should_check_now():
            logger.debug("auto_update: throttled, skipping check")
            return None
        # Default to 'beta' for tester rc tags. Override via env.
        channel = os.environ.get("OYSTER_UPDATE_CHANNEL", "beta")
        info = check_for_updates(channel=channel)
        if info is None:
            logger.info("auto_update: no release info (network? rate limit?)")
            _record_check(None)
            return None
        if not info.is_newer:
            logger.info("auto_update: already on latest (%s)", info.tag)
            return None
        if info.tag in _skipped_tags():
            logger.info("auto_update: user skipped %s", info.tag)
            return None

        choice = show_update_dialog(info, lang=lang)
        if choice == "skip":
            _skipped_tags(add=info.tag)
            return None
        if choice == "later":
            return None
        try:
            return download_update(info, _appdata_dir() / "downloads")
        except (OSError, ValueError, urllib.error.URLError) as e:
            logger.warning("auto_update: download failed: %s", e)
            return None
    except Exception as e:
        logger.warning("auto_update: orchestrator crashed: %s", e)
        return None


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _info = check_for_updates(channel="beta")
    if _info is None:
        print("auto_update: check returned None (network? rate limit?)")
        sys.exit(1)
    for k in ("tag", "version", "published_at", "installer_url",
              "installer_size_mb", "is_newer"):
        print(f"{k:<16} {getattr(_info, k)}")
    print(f"{'current':<16} {get_current_version()}")
