#!/usr/bin/env python3
"""diag_uploader.py — rc16.11 Layer 4 auto-diagnostic uploader.

When a recording ends with one or more failed lint criteria, the
launcher offers to bundle the session directory and POST it to an
anonymous-upload endpoint so support can grab the artefact from the
user's log instead of asking them to find files manually.

Stdlib only (zipfile + urllib.request + tkinter). Every function
catches its own exceptions and returns a sentinel — Layer 4 must
never crash the launcher. Auto-confirm defaults to True so a tester
walking away from a failed recording still produces a support URL.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from _i18n import _t  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — defensive: dialog text never crashes import
    def _t(en: str, zh: str) -> str:  # type: ignore[no-redef]
        return en

logger = logging.getLogger(__name__)

MODULE_VERSION: str = "rc16.11"
DEFAULT_ENDPOINT: str = "https://0x0.st/"
DEFAULT_MAX_SIZE_MB: int = 200
DEFAULT_AUTO_CONFIRM_SECONDS: float = 30.0
DEFAULT_UPLOAD_TIMEOUT_SECONDS: float = 60.0
ENV_FORCE_UPLOAD: str = "OYSTER_AUTO_UPLOAD"
USER_AGENT: str = f"OysterRecorder-DiagUploader/{MODULE_VERSION}"

# Videos are dropped largest-first if the session exceeds max_size_mb.
# inputs.jsonl + lint_report + system_info total <5 MB combined.
_TRIMMABLE_PATTERNS: tuple[str, ...] = (
    "**/recording.mp4", "**/recording_*.mp4",
    "**/*.mp4", "**/*.mkv", "**/*.avi",
)
_TRUTHY: frozenset = frozenset({"1", "true", "yes"})


@dataclass(frozen=True)
class DiagnosticBundle:
    """Immutable record of a diagnostic ZIP + (optionally) its upload URL."""
    session_dir: Path
    zip_path: Path
    size_bytes: int
    upload_url: Optional[str] = None
    upload_timestamp: Optional[str] = None


def should_offer_upload(lint_report: Optional[Dict[str, Any]]) -> bool:
    """rc16.11: True iff lint failed, lint missing (silent fail), or env forces."""
    forced = os.environ.get(ENV_FORCE_UPLOAD, "").strip().lower() in _TRUTHY
    if forced:
        logger.info("rc16.11 should_offer_upload: forced via env"); return True
    if lint_report is None:
        logger.info("rc16.11 should_offer_upload: lint missing"); return True
    if not isinstance(lint_report, dict):
        logger.warning("rc16.11 should_offer_upload: bad type, treat as fail")
        return True
    summary = lint_report.get("summary")
    if isinstance(summary, dict):
        try:
            failed = int(summary.get("failed", 0))
        except (TypeError, ValueError):
            failed = 0
        if failed > 0:
            logger.info("rc16.11 should_offer_upload: %d failed", failed); return True
    results = lint_report.get("results")
    if isinstance(results, list):
        for row in results:
            if isinstance(row, dict) and row.get("passed") is False:
                logger.info("rc16.11 should_offer_upload: criterion failed")
                return True
    logger.info("rc16.11 should_offer_upload: clean")
    return False


def _dir_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _files_to_trim(session_dir: Path, max_bytes: int) -> set[Path]:
    """Trimmable video files we must drop to fit the cap (largest first)."""
    total = _dir_size(session_dir)
    if total <= max_bytes:
        return set()
    seen: set[Path] = set()
    candidates: List[Path] = []
    for pat in _TRIMMABLE_PATTERNS:
        for p in session_dir.glob(pat):
            try:
                if p.is_file() and p not in seen:
                    seen.add(p)
                    candidates.append(p)
            except OSError:
                continue
    candidates.sort(key=lambda f: f.stat().st_size, reverse=True)
    drop: set[Path] = set()
    remaining = total
    for f in candidates:
        if remaining <= max_bytes:
            break
        drop.add(f)
        try:
            remaining -= f.stat().st_size
        except OSError:
            pass
    return drop


def bundle_session(session_dir: Path,
                   max_size_mb: int = DEFAULT_MAX_SIZE_MB) -> DiagnosticBundle:
    """Zip the session directory; drop video files largest-first if oversized.

    ZIP is written as a sibling of session_dir (not inside) so a
    re-bundle never recursively packs a previous diag zip.
    """
    session_dir = Path(session_dir).resolve()
    if not session_dir.is_dir():
        raise FileNotFoundError(f"session_dir not a directory: {session_dir}")
    max_bytes = max(1, max_size_mb) * 1024 * 1024
    drop_set = _files_to_trim(session_dir, max_bytes)
    if drop_set:
        logger.info("rc16.11 bundle_session: dropping %d video(s) to fit %d MB",
                    len(drop_set), max_size_mb)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = session_dir.parent / f"diag_{session_dir.name}_{stamp}.zip"
    manifest = [f"# diag_uploader {MODULE_VERSION}", f"# session={session_dir.name}",
                f"# bundled_at={stamp}", f"# trimmed={len(drop_set)}"]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                          compresslevel=6, allowZip64=True) as zf:
        for f in sorted(session_dir.rglob("*")):
            try:
                if not f.is_file(): continue  # noqa: E701
            except OSError:
                continue
            try:
                arcname = f.relative_to(session_dir.parent)
            except ValueError:
                arcname = Path(session_dir.name) / f.name
            if f in drop_set:
                manifest.append(f"trimmed {arcname} ({f.stat().st_size} B)")
                continue
            try:
                zf.write(f, arcname=str(arcname))
            except OSError as exc:
                logger.warning("rc16.11 bundle_session: skip %s (%s)", f, exc)
                manifest.append(f"skipped {arcname} ({exc})")
        zf.writestr(f"{session_dir.name}/_diag_manifest.txt",
                    "\n".join(manifest) + "\n")
    size_bytes = zip_path.stat().st_size
    logger.info("rc16.11 bundle_session: %s (%.1f MB)", zip_path, size_bytes / (1024 * 1024))
    return DiagnosticBundle(session_dir=session_dir, zip_path=zip_path, size_bytes=size_bytes)


def show_upload_dialog(bundle: DiagnosticBundle,
                       auto_confirm_seconds: float = DEFAULT_AUTO_CONFIRM_SECONDS,
                       ) -> bool:
    """rc16.11 Tk dialog (Send|Decline). Auto-confirms Send on timeout.

    Returns True on Send (click OR timer). False on explicit Decline
    or if Tk is unavailable.
    """
    try:
        import tkinter as tk  # noqa: PLC0415 — stdlib, optional on minimal hosts
    except Exception as exc:  # noqa: BLE001
        logger.warning("rc16.11 show_upload_dialog: Tk unavailable (%s)", exc)
        return False
    size_mb = bundle.size_bytes / (1024 * 1024)
    state: Dict[str, bool] = {"send": False, "decided": False}
    def _close(send: bool, root: Any) -> None:
        if not state["decided"]:
            state["send"] = send
            state["decided"] = True
        try: root.destroy()  # noqa: E701
        except Exception: pass  # noqa: BLE001, E701
    try:
        root = tk.Tk()
        root.title(_t(
            "Oyster Recorder — Diagnostic Upload",
            "Oyster Recorder — 诊断上传",
        ))
        try: root.attributes("-topmost", True)  # noqa: E701
        except Exception: pass  # noqa: BLE001, E701
        root.geometry("460x220"); root.resizable(False, False)
        tk.Label(root, justify="left", padx=20, pady=12,
                 text=_t(
                     "Your recording had issues that our team can fix faster\n"
                     "if you share the diagnostic bundle.\n\n"
                     f"Bundle: {bundle.zip_path.name}\n"
                     f"Size:   {size_mb:.1f} MB\n\n"
                     f"Auto-send in {int(auto_confirm_seconds)}s if no choice.",
                     "您的录制出现问题。如果分享诊断包,\n"
                     "我们的技术支持团队可以更快地修复。\n\n"
                     f"诊断包: {bundle.zip_path.name}\n"
                     f"大小:   {size_mb:.1f} MB\n\n"
                     f"{int(auto_confirm_seconds)} 秒内未选择将自动发送。",
                 ),
                 ).pack(fill="both", expand=True)
        bf = tk.Frame(root); bf.pack(pady=8)
        send_btn = tk.Button(bf, text=_t("Send", "发送"), width=12, default="active",
                             command=lambda: _close(True, root))
        send_btn.pack(side="left", padx=6)
        tk.Button(bf, text=_t("Decline", "拒绝"), width=12,
                  command=lambda: _close(False, root)).pack(side="left", padx=6)
        try: root.after(int(auto_confirm_seconds * 1000), lambda: _close(True, root))  # noqa: E701
        except Exception: pass  # noqa: BLE001, E701
        send_btn.focus_set()
        root.mainloop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rc16.11 show_upload_dialog: Tk error (%s)", exc)
        return False
    logger.info("rc16.11 show_upload_dialog: decision=%s", state["send"])
    return bool(state["send"])


def _build_multipart(zip_path: Path) -> tuple[bytes, str]:
    boundary = f"----OysterDiagBoundary{uuid.uuid4().hex}"
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{zip_path.name}"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ).encode("utf-8")
    post = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return pre + zip_path.read_bytes() + post, f"multipart/form-data; boundary={boundary}"


def upload_bundle(bundle: DiagnosticBundle,
                  endpoint: str = DEFAULT_ENDPOINT,
                  timeout: float = DEFAULT_UPLOAD_TIMEOUT_SECONDS,
                  ) -> DiagnosticBundle:
    """rc16.11: multipart POST to ``endpoint``; never raises.

    On success returns a new bundle with ``upload_url`` and
    ``upload_timestamp`` populated. On any failure logs a warning and
    returns the input bundle unchanged.
    """
    if not bundle.zip_path.is_file():
        logger.warning("rc16.11 upload_bundle: zip missing %s", bundle.zip_path)
        return bundle
    try:
        body, content_type = _build_multipart(bundle.zip_path)
    except OSError as exc:
        logger.warning("rc16.11 upload_bundle: read failed (%s)", exc)
        return bundle
    req = urllib.request.Request(  # noqa: S310 — operator-supplied HTTPS
        endpoint, data=body, method="POST",
        headers={"Content-Type": content_type, "User-Agent": USER_AGENT},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
            status = getattr(resp, "status", 0) or resp.getcode()
    except urllib.error.HTTPError as exc:
        logger.warning("rc16.11 upload_bundle: HTTP %s (%s)", exc.code, exc.reason)
        return bundle
    except urllib.error.URLError as exc:
        logger.warning("rc16.11 upload_bundle: network error (%s)", exc.reason)
        return bundle
    except (TimeoutError, OSError) as exc:
        logger.warning("rc16.11 upload_bundle: io error (%s)", exc)
        return bundle
    except Exception as exc:  # noqa: BLE001
        logger.warning("rc16.11 upload_bundle: unexpected (%s)", exc)
        return bundle
    if status >= 400:
        logger.warning("rc16.11 upload_bundle: server rejected status=%s", status)
        return bundle
    url = raw.decode("utf-8", errors="replace").strip()
    if not url.startswith("http"):
        logger.warning("rc16.11 upload_bundle: non-URL response (%r)", url[:120])
        return bundle
    elapsed = time.monotonic() - started
    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info("rc16.11 upload_bundle: ok %s in %.1fs", url, elapsed)
    return replace(bundle, upload_url=url, upload_timestamp=timestamp)


def run_layer4(session_dir: Path,
               lint_report: Optional[Dict[str, Any]] = None,
               auto_confirm: bool = True,
               endpoint: str = DEFAULT_ENDPOINT,
               max_size_mb: int = DEFAULT_MAX_SIZE_MB,
               auto_confirm_seconds: float = DEFAULT_AUTO_CONFIRM_SECONDS,
               ) -> Optional[DiagnosticBundle]:
    """rc16.11 orchestrator. Called from oyster_play.py post-recording.

    Flow: should_offer_upload -> bundle_session -> show_upload_dialog
    (skipped when auto_confirm AND OYSTER_AUTO_UPLOAD=1) -> upload_bundle.
    Returns the (possibly uploaded) bundle, or None when the session
    was clean. Never raises.
    """
    try:
        if not should_offer_upload(lint_report):
            logger.info("rc16.11 run_layer4: clean — no offer"); return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("rc16.11 run_layer4: decision crashed (%s)", exc); return None
    try:
        bundle = bundle_session(Path(session_dir), max_size_mb=max_size_mb)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rc16.11 run_layer4: bundle failed (%s)", exc); return None
    force_silent = (auto_confirm and
                    os.environ.get(ENV_FORCE_UPLOAD, "").strip().lower() in _TRUTHY)
    if force_silent:
        logger.info("rc16.11 run_layer4: auto-send (env set)"); proceed = True
    else:
        try:
            proceed = show_upload_dialog(bundle, auto_confirm_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rc16.11 run_layer4: dialog crashed (%s)", exc); proceed = False
    if not proceed:
        logger.info("rc16.11 run_layer4: declined — local at %s", bundle.zip_path)
        return bundle
    try:
        bundle = upload_bundle(bundle, endpoint=endpoint)
    except Exception as exc:  # noqa: BLE001 — defence in depth
        logger.warning("rc16.11 run_layer4: upload raised (%s)", exc); return bundle
    if bundle.upload_url:
        logger.info("rc16.11 run_layer4: uploaded %s", bundle.upload_url)
    else:
        logger.info("rc16.11 run_layer4: upload failed — local at %s", bundle.zip_path)
    return bundle


if __name__ == "__main__":
    # Smoke entry. Real call site is the launcher.
    # Usage: python3 bin/diag_uploader.py <session_dir> [--dry]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if len(sys.argv) < 2:
        sys.exit("usage: diag_uploader.py <session_dir> [--dry]")
    _sd = Path(sys.argv[1])
    if "--dry" in sys.argv[2:]:
        _b = bundle_session(_sd)
        print(f"BUNDLE {_b.zip_path} {_b.size_bytes}"); sys.exit(0)
    os.environ.setdefault(ENV_FORCE_UPLOAD, "1")
    _r = run_layer4(_sd, lint_report=None, auto_confirm=True)
    if _r is None:
        print("SKIPPED"); sys.exit(0)
    print(f"BUNDLE {_r.zip_path} {_r.size_bytes}")
    if _r.upload_url:
        print(f"URL    {_r.upload_url}")
