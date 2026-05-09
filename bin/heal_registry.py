"""bin/heal_registry.py — Central self-heal event registry (Heal Framework v1).

rc15-fix (2026-05-09 audit):
  - BUG#1: json.dumps now uses default=str so Path/datetime/set in
    details dict don't silently drop the event.
  - BUG#2: emit_event guarded by threading.Lock so concurrent writes
    from daemon threads don't interleave half-lines.


Howard 2026-05-09: "新 feature 都需要有良好的 self-heal 和 report 系统".

Every recorder feature emits structured events here so we can:
1. Aggregate failure modes across the install base (Phase C telemetry)
2. Generate local heal_report.html for tester self-debug
3. Enforce contract via lint: every feature_id MUST be registered
4. Detect regressions: rc11 SF mod_handshake_ok bug would be catchable
   if we audited heal_event log for "always-False" patterns

Append-only JSONL at:
    %USERPROFILE%/Documents/OysterRecorder/runtime/heal_events.jsonl

NEVER raises — heal logging that breaks recording is the worst outcome.
All exceptions swallowed silently into traceback log.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# rc15-fix BUG#2: serialize file appends across threads. Without this,
# 2 daemon threads emitting at the same μs could write half-lines that
# corrupt the heal_events.jsonl. append-mode atomicity is OS-dependent
# and not reliable on Windows for >4KB writes.
_EMIT_LOCK = threading.Lock()

# === Registry contract ===
# Add new features here. Lint refuses to merge PRs that emit unregistered
# feature_id. See HEAL_CONTRACT.md for the workflow.
VALID_FEATURES: set[str] = {
    # rc10
    "B1_close_confirm_kill_mc",       # window-close + taskkill javaw
    "B2_ffmpeg_clean_close",          # mp4 trailer integrity flag
    "B3_update_bat_retry",             # 30s retry move /Y
    "B4_update_same_drive_tmp",        # _update_tmp under sys.executable
    "B5_disk_space_preflight",         # 500MB check before record
    # rc11 Phase A
    "SF_terminator",                   # 13-reason failure attribution
    "SG_heartbeat",                    # health.json 30s pulse
    # rc12 Phase A.3
    "SH_preflight",                    # 14-check startup self-test
    # rc13 Phase B
    "SI_orphan_cleanup",               # tmp_dir scan + rmtree
    "SK_duration_too_short_prompt",    # < 5min reprompt modal
    # rc14 dual-track
    "depth_dual_track",                # cuda/dml/cpu/server-pending router
    # framework itself
    "heal_registry",                    # meta-events about the registry
}

VALID_EVENT_TYPES: set[str] = {
    "detect",         # we noticed a condition (good or bad)
    "report",         # passive status report (e.g. preflight result)
    "heal_attempt",   # we tried to fix it
    "heal_success",   # fix worked
    "heal_failed",    # fix didn't work, escalate
    "user_prompt",    # asked user to take action
    "user_action",    # user did something (responded to prompt)
}

VALID_SEVERITY: set[str] = {"info", "warn", "error", "fatal"}


def _heal_log_path() -> Path:
    """Lazy-resolve so tests can mock _real_documents_dir."""
    try:
        # Import here to avoid circular: recorder_consumer_lite imports us.
        from recorder_consumer_lite import _real_documents_dir  # type: ignore
        base = Path(_real_documents_dir())
    except Exception:
        # Standalone / test fallback
        base = Path.home() / "Documents"
    return base / "OysterRecorder" / "runtime" / "heal_events.jsonl"


def emit_event(
    feature_id: str,
    event_type: str,
    severity: str,
    summary: str,
    details: Optional[dict[str, Any]] = None,
    remediation: Optional[dict[str, Any]] = None,
    session_id: Optional[str] = None,
    recorder_version: Optional[str] = None,
) -> str:
    """Append a heal event. Returns event_id (caller may ignore).

    NEVER raises. Validation warnings are encoded in the event itself
    (feature_id prefixed with 'unregistered:') so lint catches them.

    Standard schema fields (ALL events):
      - schema_version: "1.0"
      - event_id: uuid4
      - ts: epoch float
      - ts_iso: ISO timestamp
      - feature_id: must be in VALID_FEATURES
      - event_type: must be in VALID_EVENT_TYPES
      - severity: info | warn | error | fatal
      - summary: one-line human-readable
      - details: dict (feature-specific)
      - remediation: dict (action, performed, next_step)
      - session_id: optional UUID linking to a recording session
      - recorder_version: optional version string

    remediation suggested shape:
      {
        "action": "auto_clean|user_prompt|server_defer|none",
        "performed": bool,
        "next_step": "what user/system should do",
      }
    """
    # Encode contract violations into the event so lint can detect.
    feature_clean = feature_id
    contract_warnings: list[str] = []
    if feature_id not in VALID_FEATURES:
        feature_clean = f"unregistered:{feature_id}"
        contract_warnings.append(
            f"feature_id '{feature_id}' not in VALID_FEATURES — register in heal_registry.py"
        )
    if event_type not in VALID_EVENT_TYPES:
        contract_warnings.append(
            f"event_type '{event_type}' not in VALID_EVENT_TYPES"
        )
    if severity not in VALID_SEVERITY:
        contract_warnings.append(
            f"severity '{severity}' not in VALID_SEVERITY"
        )

    # rc15.3-fix BUG#13 (PREVENTIVE): cap details size to defend against
    # crash-storm bloat (e.g. excepthook payload with full traceback ×
    # 100 events = jsonl > 10MB in seconds). Stress test showed no upper
    # bound; a single excepthook can emit 50KB. Cap at 4KB raw JSON +
    # marker so we keep enough context for debug without runaway growth.
    _details_safe = details or {}
    try:
        _details_str = json.dumps(_details_safe, ensure_ascii=False, default=str)
        if len(_details_str) > 4096:
            _details_safe = {
                "_truncated": True,
                "_original_size_bytes": len(_details_str),
                "_preview": _details_str[:3500] + "...[truncated]",
            }
    except Exception:
        _details_safe = {"_serialize_failed": True}

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "ts": time.time(),
        "ts_iso": datetime.now().isoformat(),
        "feature_id": feature_clean,
        "event_type": event_type,
        "severity": severity,
        "summary": summary[:300],  # cap
        "details": _details_safe,
        "remediation": remediation or {},
    }
    if session_id:
        payload["session_id"] = session_id
    if recorder_version:
        payload["recorder_version"] = recorder_version
    if contract_warnings:
        payload["_contract_warnings"] = contract_warnings

    # rc15-fix BUG#3: lazy self-check on first real emit (avoids the
    # circular-import trap that put first ~10 events into wrong path).
    _maybe_self_check()

    # rc15-fix BUG#1: details may contain Path/datetime/set/etc that
    # json.dumps can't serialize natively. Use default=str so writer
    # never silently fails on caller laziness. Was: emit returned a
    # fake event_id but log file got nothing.
    try:
        path = _heal_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # rc15.4-fix BUG R6 C1: gate file size at 20MB to prevent unbounded
        # growth (orphan_resumed × N runs). On overflow, rotate file →.1
        # and start fresh. ~5K typical events = .1 = ~5MB rotated keeps.
        try:
            if path.exists() and path.stat().st_size > 20_000_000:
                rotated = path.with_suffix(".jsonl.1")
                try:
                    if rotated.exists():
                        rotated.unlink()
                except Exception:
                    pass
                try:
                    path.rename(rotated)
                except Exception:
                    pass
        except Exception:
            pass
        # rc15-fix BUG#2: lock around the open+write so concurrent emits
        # from daemon threads don't interleave half-lines.
        with _EMIT_LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # Last-resort: dump to stderr but never raise.
        try:
            os.write(2, f"[heal_registry] emit failed: {traceback.format_exc()}\n".encode())
        except Exception:
            pass

    return payload["event_id"]


def read_recent_events(limit: int = 200) -> list[dict[str, Any]]:
    """Tail the heal log. Used by heal_report.html generator.

    Returns up to `limit` most recent events, parsed. Bad lines skipped.
    Never raises.

    rc15.4-fix BUG R6 C1: use deque(fh, maxlen=limit) for O(1) memory tail
    so we don't load 20MB into RAM just to slice last 200 events.
    """
    from collections import deque  # noqa: PLC0415
    out: list[dict[str, Any]] = []
    try:
        path = _heal_log_path()
        if not path.exists():
            return out
        with path.open("r", encoding="utf-8") as fh:
            tail_lines = deque(fh, maxlen=limit)
        for line in tail_lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return out
    return out


def aggregate_by_feature(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Group events by feature_id + count by severity.

    Returns: {feature_id: {info: N, warn: N, error: N, fatal: N, total: N}}
    Used by heal_report.html for the per-feature dashboard.
    """
    out: dict[str, dict[str, int]] = {}
    for ev in events:
        fid = ev.get("feature_id", "unknown")
        sev = ev.get("severity", "info")
        bucket = out.setdefault(fid, {"info": 0, "warn": 0, "error": 0, "fatal": 0, "total": 0})
        bucket[sev] = bucket.get(sev, 0) + 1
        bucket["total"] += 1
    return out


# === Contract self-check ===
# rc15-fix BUG#3 (CRITICAL): self-check is now LAZY — called on first
# emit_event() invocation, NOT at module import. Was bug:
# recorder_consumer_lite imports heal_registry which immediately calls
# _self_check() → emit_event() → _heal_log_path() → tries to import
# recorder_consumer_lite._real_documents_dir BEFORE that module body
# finished executing → ImportError silently swallowed → first events
# go to wrong fallback path (Path.home()/Documents instead of the
# OneDrive-redirected real path). Two-path log split confused testers.
# Lazy guard avoids the cycle entirely.
_SELF_CHECK_DONE = False
_SELF_CHECK_LOCK = threading.Lock()


def _maybe_self_check() -> None:
    """First-call-wins self check. Never raises."""
    global _SELF_CHECK_DONE
    with _SELF_CHECK_LOCK:
        if _SELF_CHECK_DONE:
            return
        _SELF_CHECK_DONE = True
    # IMPORTANT: don't recurse via emit_event — write a sentinel event
    # directly so this can't loop forever even if emit_event is broken.
    try:
        path = _heal_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        sentinel = {
            "schema_version": "1.0",
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "ts_iso": datetime.now().isoformat(),
            "feature_id": "heal_registry",
            "event_type": "report",
            "severity": "info",
            "summary": f"heal_registry loaded, {len(VALID_FEATURES)} features registered",
            "details": {"valid_features": sorted(VALID_FEATURES)},
            "remediation": {"action": "none", "performed": True, "next_step": "no action"},
        }
        with _EMIT_LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(sentinel, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
