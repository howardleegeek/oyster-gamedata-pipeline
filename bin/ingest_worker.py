#!/usr/bin/env python3
"""
ingest_worker.py — Phase 1 local ingest worker for the GameData scoring pipeline.

Automates the existing ``score_session.py`` so that dropped session folders get
scored hands-off, with NO cloud / network dependency. This is the architect's
Phase 1 (local truth loop → worker) per
``oyster-gamedata-pipeline/docs/ARCH_SERVER_SCORING_PIPELINE.md``.

What it does
------------
1. Watches ``--ingest-dir`` for session folders. A directory is treated as a
   "session" once it contains a ``metadata.json`` AND a video
   (``recording.mp4`` / ``video.mp4`` / ``game.mp4``). To avoid scoring a folder
   that is still being copied in, a candidate must look *stable*: its total byte
   size must be unchanged between two observations one poll apart.
2. For each new, stable session it runs the LOCKED scorer:
       score_session.py <session> [--skip-depth]
   capturing the scorer's ``clip_summary.json`` and its ``--report-json`` report.
3. Appends exactly one line per scored session to an append-only ledger
   ``--results-dir/scores_ledger.jsonl``. Prior lines are NEVER rewritten or
   deleted.
4. Idempotent: a session already present in the ledger (matched on
   ``session_id`` + a cheap content hash) is skipped unless ``--force``. A
   re-run never double-appends.
5. Error isolation: a session that errors (missing files, ffprobe failure,
   ``score_session`` non-zero / crash) is logged, gets a ``failed`` ledger
   entry, and the worker CONTINUES to the next session. One bad session never
   crashes the worker.
6. ``--once`` performs a single sweep then exits (for tests / cron). The default
   is a poll loop every ``--poll-seconds`` (default 30), with graceful shutdown
   on SIGINT / SIGTERM.
7. Structured per-session logging: start / end / score / duration / error.

INTEGRITY (hard rule — see issue #132 + the architect's doc)
------------------------------------------------------------
The ONLY path that writes ``passed: true`` or a numeric score into the ledger is
parsing ``score_session.py``'s *real* result from the SAME run — its
``clip_summary.json`` / ``--report-json`` output — cross-checked against the
scorer's exit code. This worker NEVER fabricates a score and NEVER marks a
failing or errored session as passed. ``passed`` is asserted only when the
scorer exits 0 AND its own report says ``passed: true`` AND its clip summary
agrees. Any disagreement, non-zero exit, or crash → the entry is ``failed`` /
``not_passed`` and ``passed`` is ``false``. The ledger is append-only truth.

Usage
-----
    ingest_worker.py --ingest-dir DIR --results-dir DIR
                     [--once] [--poll-seconds N] [--skip-depth] [--force]
                     [--scorer PATH] [--log-level LEVEL]

Exit codes
----------
    0 - sweep / loop completed normally (individual session failures are
        isolated into the ledger, they do NOT change this exit code)
    1 - the worker itself could not run (bad arguments, scorer missing,
        results dir not writable)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Video filenames a "session" may carry. MUST stay a superset-compatible match
# with score_session.VIDEO_CANDIDATES (recording.mp4 first — the recorder's
# canonical name).
VIDEO_CANDIDATES = ("recording.mp4", "video.mp4", "game.mp4")

# The scorer this worker automates. Resolved relative to this file by default so
# the worker and scorer ship together in bin/.
DEFAULT_SCORER = Path(__file__).resolve().parent / "score_session.py"

LEDGER_NAME = "scores_ledger.jsonl"

# Ledger entry schema id — lets future readers (fleet gate) version the format.
LEDGER_SCHEMA = "oyster.scores_ledger/v1"

# Terminal ledger statuses. A session that previously reached one of these is
# considered "already handled" for idempotency.
STATUS_PASSED = "passed"
STATUS_NOT_PASSED = "not_passed"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_PASSED, STATUS_NOT_PASSED, STATUS_FAILED)

# Wall-clock ceiling for a single score_session invocation. The scorer itself
# times its internal stages (depth up to 2400s); this is the outer guard so a
# wedged scorer can never block the worker forever.
SCORER_WALL_TIMEOUT = 3 * 60 * 60  # 3h — generous for a full depth run

logger = logging.getLogger("ingest_worker")

# Set by the signal handlers; the poll loop checks it for graceful shutdown.
_SHUTDOWN = False


def _handle_signal(signum: int, _frame: Any) -> None:
    """Flip the shutdown flag so the poll loop exits cleanly after this sweep."""
    global _SHUTDOWN
    logger.info("signal %s received — finishing current session then shutting down", signum)
    _SHUTDOWN = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """UTC timestamp, second resolution, Z-suffixed (matches score_session)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_video(session_dir: Path) -> Optional[Path]:
    """Return the session's primary video (first existing non-empty candidate).

    Follows symlinks (the validated sample sessions symlink recording.mp4 to a
    real mp4 elsewhere) and requires the resolved target to be a non-empty file.
    """
    for name in VIDEO_CANDIDATES:
        p = session_dir / name
        try:
            if p.is_file() and p.stat().st_size > 0:  # is_file() follows symlinks
                return p
        except OSError:
            continue
    return None


def looks_like_session_candidate(path: Path) -> bool:
    """True iff *path* is a directory the operator clearly intended as a session.

    The marker is ``metadata.json`` — its presence means someone dropped a
    session here, even if the video has not landed yet (or never will). The
    sweep further classifies each candidate as *complete* (also has a video →
    score it) or *malformed* (stable, no usable video → record a ``failed``
    ledger entry rather than ignore it). We deliberately treat a metadata-only
    folder as a candidate, not noise, so a genuinely broken drop is surfaced.
    """
    return path.is_dir() and (path / "metadata.json").is_file()


def looks_like_session(path: Path) -> bool:
    """True iff *path* is a COMPLETE session: a directory with metadata + video.

    This is the structural completeness gate; stability (size unchanged) is
    checked separately so we never score a folder mid-copy.
    """
    return looks_like_session_candidate(path) and find_video(path) is not None


def dir_size_bytes(path: Path) -> int:
    """Best-effort total size of a session dir (resolved file sizes, recursive).

    Used only as a stability signal (size unchanged across one poll ⇒ the copy
    has settled). Symlinks are sized by their target so a large video landing
    via symlink still registers; unreadable entries are skipped rather than
    raising — this is a heuristic, not an audit.
    """
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for fn in files:
                fp = Path(root) / fn
                try:
                    total += fp.stat().st_size  # follows symlinks
                except OSError:
                    continue
    except OSError:
        return total
    return total


def compute_content_hash(session_dir: Path) -> str:
    """A cheap, stable content fingerprint of the RAW INPUT, for idempotency.

    Fingerprints the input recording's identity — the resolved video's
    (filename, size, mtime_ns) — NOT the multi-GB video bytes, and NOT
    metadata.json. metadata.json is deliberately EXCLUDED: score_session.py
    rewrites it on every run (fps/resolution-correction provenance carries a
    fresh ``corrected_at`` timestamp), so hashing it would change the key on
    each scoring pass and silently break idempotency (re-scoring + double-
    appending forever). The video file is never mutated by the scorer, so it is
    the stable input identity.

    This is deterministic for an unchanged session and changes only if the
    operator drops a materially different recording under the same folder name —
    exactly when we WANT to re-score. It is an idempotency key, not a security
    digest.
    """
    h = hashlib.sha256()
    video = find_video(session_dir)
    if video is not None:
        try:
            st = video.stat()  # follows symlink to the real target
            h.update(f"{video.name}:{st.st_size}:{st.st_mtime_ns}".encode())
        except OSError:
            h.update(b"<no-video-stat>")
    else:
        # No video → a malformed (never-scored) candidate. The scorer never runs
        # on it, so metadata.json is NOT rewritten and IS a stable key here. This
        # keeps the resulting `failed` ledger entry idempotent (one line, even if
        # the worker re-sweeps the same broken folder repeatedly).
        h.update(b"<no-video>")
        meta = session_dir / "metadata.json"
        try:
            h.update(meta.read_bytes())
        except OSError:
            h.update(b"<no-metadata>")
    return h.hexdigest()


def read_session_id(session_dir: Path) -> str:
    """Session id from metadata.json (preferred) else the folder name.

    Mirrors score_session's own session_id resolution so the ledger key matches
    the scorer's clip_summary.json / report.
    """
    meta_path = session_dir / "metadata.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text())
            if isinstance(meta, dict):
                sid = meta.get("session_id")
                if isinstance(sid, str) and sid.strip():
                    return sid
        except (json.JSONDecodeError, OSError):
            pass
    return session_dir.name


# ---------------------------------------------------------------------------
# Ledger (append-only)
# ---------------------------------------------------------------------------

def load_ledger_keys(ledger_path: Path) -> set[tuple[str, str]]:
    """Return the set of (session_id, content_hash) already in the ledger.

    Tolerant of malformed lines (a partially-written final line from a crash is
    skipped, not fatal) — the ledger is the source of truth and we never let a
    bad line block reads. Only terminal-status entries count toward idempotency.
    """
    keys: set[tuple[str, str]] = set()
    if not ledger_path.is_file():
        return keys
    with ledger_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("ledger has a non-JSON line (ignored for idempotency): %.80s", line)
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("status") not in TERMINAL_STATUSES:
                continue
            sid = rec.get("session_id")
            chash = rec.get("content_hash")
            if isinstance(sid, str) and isinstance(chash, str):
                keys.add((sid, chash))
    return keys


def append_ledger(ledger_path: Path, entry: dict[str, Any]) -> None:
    """Append exactly one JSON line to the ledger. NEVER rewrites prior lines.

    Opens in append mode, writes one compact line + newline, flushes and fsyncs
    so a crash right after can't lose or truncate the record. This is the only
    function that writes the ledger.
    """
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------------------------
# Scoring a single session (integrity-gated)
# ---------------------------------------------------------------------------

@dataclass
class ScoreOutcome:
    """Structured result of running the scorer on one session."""
    status: str                      # passed | not_passed | failed
    passed: bool                     # True ONLY on a verified green run
    scorer_exit_code: Optional[int]
    error: Optional[str] = None
    report: Optional[dict[str, Any]] = None        # parsed score_session_report.json
    clip_summary: Optional[dict[str, Any]] = None  # parsed clip_summary.json


def _read_json_obj(path: Path) -> Optional[dict[str, Any]]:
    """Read *path* as a JSON object, or None if absent / unparseable / not dict."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def run_scorer(
    session_dir: Path,
    *,
    scorer: Path,
    skip_depth: bool,
    timeout: int = SCORER_WALL_TIMEOUT,
) -> ScoreOutcome:
    """Run score_session.py on one session and return an integrity-gated outcome.

    The scorer writes its report to a path we choose (``--report-json``) so we
    parse a freshly-produced report, never a stale one. The verdict is derived
    SOLELY from that report + the clip summary + the scorer's exit code:

      * exit 0  AND report.passed is True AND clip.prd_passed is True → ``passed``
      * exit 1  (ran, did not pass) OR any of the above disagree      → ``not_passed``
      * exit 2 / crash / timeout / no report                          → ``failed``

    Never raises for an in-session problem — those become a ``failed`` outcome so
    the caller can ledger it and move on. The verdict cannot be coaxed into a
    PASS by anything other than a genuine green scorer run.
    """
    report_json = session_dir / "score_session_report.json"
    cmd = [
        sys.executable,
        str(scorer),
        str(session_dir),
        "--report-json",
        str(report_json),
    ]
    if skip_depth:
        cmd.append("--skip-depth")

    logger.info("[%s] scorer START  cmd=%s", session_dir.name, " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error("[%s] scorer TIMEOUT after %ss", session_dir.name, timeout)
        return ScoreOutcome(
            status=STATUS_FAILED,
            passed=False,
            scorer_exit_code=None,
            error=f"score_session timed out after {timeout}s",
        )
    except Exception as exc:  # noqa: BLE001 — isolate ANY launch failure
        logger.error("[%s] scorer could not be launched: %s", session_dir.name, exc)
        return ScoreOutcome(
            status=STATUS_FAILED,
            passed=False,
            scorer_exit_code=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    exit_code = proc.returncode
    stderr_tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])

    # Parse the freshly-written artifacts. These are the integrity source.
    report = _read_json_obj(report_json)
    clip_summary = _read_json_obj(session_dir / "clip_summary.json")

    # Exit 2 = fatal precondition inside the scorer (missing video / scripts /
    # dir). No trustworthy score exists → failed.
    if exit_code == 2:
        err = stderr_tail.splitlines()[-1] if stderr_tail else "score_session fatal precondition (exit 2)"
        logger.error("[%s] scorer FATAL (exit 2): %s", session_dir.name, err)
        return ScoreOutcome(
            status=STATUS_FAILED,
            passed=False,
            scorer_exit_code=exit_code,
            error=err,
            report=report,
            clip_summary=clip_summary,
        )

    # Any exit code other than the scorer's documented 0/1 is unexpected → failed.
    if exit_code not in (0, 1):
        err = stderr_tail.splitlines()[-1] if stderr_tail else f"unexpected scorer exit {exit_code}"
        logger.error("[%s] scorer unexpected exit %s: %s", session_dir.name, exit_code, err)
        return ScoreOutcome(
            status=STATUS_FAILED,
            passed=False,
            scorer_exit_code=exit_code,
            error=err,
            report=report,
            clip_summary=clip_summary,
        )

    # The scorer ran to completion (0 or 1) but produced no report → we have no
    # trustworthy result to record. Treat as failed (never guess a score).
    if report is None:
        logger.error(
            "[%s] scorer exited %s but wrote no parseable report at %s",
            session_dir.name, exit_code, report_json,
        )
        return ScoreOutcome(
            status=STATUS_FAILED,
            passed=False,
            scorer_exit_code=exit_code,
            error="score_session produced no parseable score_session_report.json",
            clip_summary=clip_summary,
        )

    # --- INTEGRITY GATE -----------------------------------------------------
    # A real PASS requires unanimous agreement across three independent signals
    # from THIS run: the process exit code, the orchestration report, and the
    # clip summary. Any disagreement collapses to not_passed (never passed).
    report_passed = report.get("passed") is True
    clip_passed = (clip_summary or {}).get("prd_passed") is True
    verified_pass = (exit_code == 0) and report_passed and clip_passed

    if verified_pass:
        status = STATUS_PASSED
        passed = True
    else:
        status = STATUS_NOT_PASSED
        passed = False
        if exit_code == 0 and not (report_passed and clip_passed):
            # Defensive: exit 0 but artifacts don't both confirm a pass. Record
            # honestly as not_passed and surface the discrepancy.
            logger.warning(
                "[%s] scorer exit 0 but report.passed=%s clip.prd_passed=%s "
                "→ recording as not_passed (integrity gate)",
                session_dir.name, report_passed, clip_passed,
            )

    logger.info(
        "[%s] scorer END  exit=%s status=%s prd_score=%s%% passed=%s",
        session_dir.name, exit_code, status,
        report.get("prd_score_percent"), passed,
    )
    return ScoreOutcome(
        status=status,
        passed=passed,
        scorer_exit_code=exit_code,
        report=report,
        clip_summary=clip_summary,
    )


def _per_test_map(clip_summary: Optional[dict[str, Any]]) -> dict[str, str]:
    """Flatten clip_summary's prd_tests list into a {test_name: status} map."""
    out: dict[str, str] = {}
    if not clip_summary:
        return out
    for t in clip_summary.get("prd_tests", []) or []:
        if isinstance(t, dict):
            name = t.get("name")
            status = t.get("status")
            if isinstance(name, str) and isinstance(status, str):
                out[name] = status
    return out


def build_ledger_entry(
    *,
    session_dir: Path,
    session_id: str,
    content_hash: str,
    outcome: ScoreOutcome,
    duration_seconds: float,
) -> dict[str, Any]:
    """Assemble the append-only ledger record for one scored session.

    Every score-bearing field is read from the scorer's own artifacts (report /
    clip summary). Nothing here invents a value; on a failed run the score
    fields are null and ``passed`` is False.
    """
    report = outcome.report or {}
    clip = outcome.clip_summary or {}
    video = find_video(session_dir)

    # Real fps / resolution: prefer the clip summary (the scorer's truth unit),
    # fall back to the orchestration report, else null. Never recomputed here.
    real_fps = clip.get("fps_real") if clip else None
    real_resolution = clip.get("resolution_real") if clip else report.get("resolution_real")
    recorder_version = clip.get("recorder_version") if clip else None
    prd_score = clip.get("prd_score_percent") if clip else report.get("prd_score_percent")

    return {
        "schema": LEDGER_SCHEMA,
        "session_id": session_id,
        "content_hash": content_hash,
        "status": outcome.status,
        "scored_at": _now_iso(),
        "recorder_version": recorder_version,
        "real_fps": real_fps,
        "real_resolution": real_resolution,
        "prd_score": prd_score,
        # `passed` is the integrity-gated verdict — True only on a verified green
        # run (see run_scorer). A failed / not_passed session is False, always.
        "passed": outcome.passed,
        "per_test": _per_test_map(outcome.clip_summary),
        "video_path": str(video) if video else None,
        # provenance / debugging — never used to derive `passed`
        "session_dir": str(session_dir),
        "scorer_exit_code": outcome.scorer_exit_code,
        "duration_seconds": round(duration_seconds, 3),
        "skip_depth": bool(clip.get("depth_skipped")) if clip else None,
        "error": outcome.error,
        "worker": "ingest_worker.py",
    }


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def discover_sessions(ingest_dir: Path) -> list[Path]:
    """Return immediate subdirs of *ingest_dir* that look like session candidates.

    A *candidate* is any directory carrying ``metadata.json`` (operator intent).
    The sweep then sorts each into complete (score) vs malformed (failed). This
    is intentionally broader than "complete session" so a metadata-only / broken
    drop is surfaced as a ``failed`` ledger entry instead of silently ignored.
    """
    sessions: list[Path] = []
    try:
        for child in sorted(ingest_dir.iterdir()):
            if looks_like_session_candidate(child):
                sessions.append(child)
    except OSError as exc:
        logger.error("cannot list ingest dir %s: %s", ingest_dir, exc)
    return sessions


def process_session(
    session_dir: Path,
    *,
    ledger_path: Path,
    ledger_keys: set[tuple[str, str]],
    scorer: Path,
    skip_depth: bool,
    force: bool,
) -> str:
    """Score one session and append its ledger entry. Returns an outcome label.

    Wrapped so that ANY exception is caught, logged, and converted into a
    ``failed`` ledger entry — one bad session never propagates out to crash the
    sweep. Returns one of: passed | not_passed | failed | skipped.
    """
    sid = read_session_id(session_dir)
    try:
        content_hash = compute_content_hash(session_dir)
    except Exception as exc:  # noqa: BLE001 — even hashing must not crash the worker
        logger.error("[%s] could not fingerprint session: %s", session_dir.name, exc)
        entry = build_ledger_entry(
            session_dir=session_dir,
            session_id=sid,
            content_hash="<unhashable>",
            outcome=ScoreOutcome(
                status=STATUS_FAILED, passed=False, scorer_exit_code=None,
                error=f"content hash failed: {type(exc).__name__}: {exc}",
            ),
            duration_seconds=0.0,
        )
        append_ledger(ledger_path, entry)
        return STATUS_FAILED

    key = (sid, content_hash)
    if key in ledger_keys and not force:
        logger.info(
            "[%s] already in ledger (session_id=%s) — skipping (use --force to re-score)",
            session_dir.name, sid,
        )
        return "skipped"

    logger.info(
        "[%s] START scoring  session_id=%s content_hash=%s skip_depth=%s force=%s",
        session_dir.name, sid, content_hash[:12], skip_depth, force,
    )
    t0 = time.time()
    try:
        outcome = run_scorer(session_dir, scorer=scorer, skip_depth=skip_depth)
    except Exception as exc:  # noqa: BLE001 — final safety net around scoring
        logger.exception("[%s] unexpected error while scoring", session_dir.name)
        outcome = ScoreOutcome(
            status=STATUS_FAILED, passed=False, scorer_exit_code=None,
            error=f"{type(exc).__name__}: {exc}",
        )
    duration = time.time() - t0

    entry = build_ledger_entry(
        session_dir=session_dir,
        session_id=sid,
        content_hash=content_hash,
        outcome=outcome,
        duration_seconds=duration,
    )
    append_ledger(ledger_path, entry)
    # Keep the in-memory key set current so a duplicate later in the SAME sweep
    # (e.g. two symlinks to the same session) does not double-append.
    ledger_keys.add(key)

    logger.info(
        "[%s] END scoring  status=%s passed=%s prd_score=%s duration=%.2fs",
        session_dir.name, outcome.status, outcome.passed,
        entry.get("prd_score"), duration,
    )
    return outcome.status


def sweep_once(
    *,
    ingest_dir: Path,
    ledger_path: Path,
    scorer: Path,
    skip_depth: bool,
    force: bool,
    stable_sizes: dict[str, int],
) -> dict[str, int]:
    """Run one discovery + score pass. Returns a tally of outcomes.

    Stability gate: a candidate is only scored when its total size matches what
    we observed on the previous sweep (``stable_sizes``). A first-seen folder is
    recorded and deferred to the next sweep — this avoids scoring a folder still
    being copied in. ``--once`` callers that need a folder scored immediately
    should ensure it has settled before invoking (the sample/cron path).
    """
    # Reload ledger keys each sweep so out-of-band appends / a fresh run see the
    # current truth (the ledger file is authoritative, not in-memory state).
    ledger_keys = load_ledger_keys(ledger_path)

    candidates = discover_sessions(ingest_dir)
    tally = {STATUS_PASSED: 0, STATUS_NOT_PASSED: 0, STATUS_FAILED: 0, "skipped": 0, "deferred": 0}

    if not candidates:
        logger.info("sweep: no session folders found in %s", ingest_dir)

    for session_dir in candidates:
        if _SHUTDOWN:
            logger.info("shutdown requested — stopping sweep early")
            break

        name = str(session_dir.resolve())
        sid = read_session_id(session_dir)
        chash = ""
        try:
            chash = compute_content_hash(session_dir)
        except Exception:  # noqa: BLE001
            chash = ""

        # Already handled (and not forced)? Skip BEFORE the stability dance so a
        # settled, scored session doesn't sit "deferred" forever.
        if chash and (sid, chash) in ledger_keys and not force:
            logger.info("[%s] already in ledger — skipping", session_dir.name)
            tally["skipped"] += 1
            stable_sizes.pop(name, None)
            continue

        # Stability gate: compare current size to the previous observation.
        cur_size = dir_size_bytes(session_dir)
        prev_size = stable_sizes.get(name)
        if prev_size is None:
            stable_sizes[name] = cur_size
            logger.info(
                "[%s] first observation (size=%d bytes) — deferring one poll to confirm stable",
                session_dir.name, cur_size,
            )
            tally["deferred"] += 1
            continue
        if cur_size != prev_size:
            stable_sizes[name] = cur_size
            logger.info(
                "[%s] size still changing (%d → %d bytes) — deferring (mid-copy?)",
                session_dir.name, prev_size, cur_size,
            )
            tally["deferred"] += 1
            continue

        # Stable now. Classify: complete (metadata + video) → score it;
        # malformed (stable, still no usable video) → a `failed` ledger entry.
        # A stable metadata-only folder is a broken drop, not mid-copy, so we
        # surface it honestly rather than ignore it.
        if find_video(session_dir) is None:
            logger.error(
                "[%s] malformed session: has metadata.json but NO usable video "
                "(looked for %s) and is stable — recording failed entry",
                session_dir.name, ", ".join(VIDEO_CANDIDATES),
            )
            entry = build_ledger_entry(
                session_dir=session_dir,
                session_id=sid,
                content_hash=chash or "<unhashable>",
                outcome=ScoreOutcome(
                    status=STATUS_FAILED, passed=False, scorer_exit_code=None,
                    error=(
                        "malformed session: metadata.json present but no usable "
                        f"video (looked for {', '.join(VIDEO_CANDIDATES)})"
                    ),
                ),
                duration_seconds=0.0,
            )
            append_ledger(ledger_path, entry)
            if chash:
                ledger_keys.add((sid, chash))
            tally[STATUS_FAILED] += 1
            stable_sizes.pop(name, None)
            continue

        # Complete + stable + new → score it.
        result = process_session(
            session_dir,
            ledger_path=ledger_path,
            ledger_keys=ledger_keys,
            scorer=scorer,
            skip_depth=skip_depth,
            force=force,
        )
        tally[result] = tally.get(result, 0) + 1
        stable_sizes.pop(name, None)  # done; forget its size

    logger.info(
        "sweep complete: passed=%d not_passed=%d failed=%d skipped=%d deferred=%d",
        tally[STATUS_PASSED], tally[STATUS_NOT_PASSED], tally[STATUS_FAILED],
        tally["skipped"], tally["deferred"],
    )
    return tally


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1 local ingest worker: auto-scores dropped GameData "
                    "session folders via score_session.py, append-only ledger.",
    )
    parser.add_argument("--ingest-dir", type=Path, required=True,
                        help="Directory watched for incoming session folders.")
    parser.add_argument("--results-dir", type=Path, required=True,
                        help="Directory holding the append-only scores_ledger.jsonl.")
    parser.add_argument("--once", action="store_true",
                        help="Single discovery+score sweep then exit (tests / cron). "
                             "Default is a poll loop.")
    parser.add_argument("--poll-seconds", type=int, default=30,
                        help="Poll interval for the loop (default: 30).")
    parser.add_argument("--skip-depth", action="store_true",
                        help="Pass --skip-depth to score_session (fast, no DA-V2 depth).")
    parser.add_argument("--force", action="store_true",
                        help="Re-score sessions already present in the ledger "
                             "(still append-only — adds a new line, never rewrites).")
    parser.add_argument("--scorer", type=Path, default=DEFAULT_SCORER,
                        help=f"Path to score_session.py (default: {DEFAULT_SCORER}).")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging verbosity (default: INFO).")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    ingest_dir = args.ingest_dir.expanduser().resolve()
    results_dir = args.results_dir.expanduser().resolve()
    scorer = args.scorer.expanduser().resolve()

    # --- Worker-level preconditions (these DO fail the worker, exit 1). ------
    if not ingest_dir.is_dir():
        logger.error("ingest dir does not exist / not a directory: %s", ingest_dir)
        return 1
    if not scorer.is_file():
        logger.error("scorer not found: %s", scorer)
        return 1
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("cannot create results dir %s: %s", results_dir, exc)
        return 1
    if not os.access(results_dir, os.W_OK):
        logger.error("results dir not writable: %s", results_dir)
        return 1

    ledger_path = results_dir / LEDGER_NAME

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "ingest_worker starting | ingest=%s results=%s ledger=%s once=%s "
        "poll=%ss skip_depth=%s force=%s scorer=%s",
        ingest_dir, results_dir, ledger_path, args.once, args.poll_seconds,
        args.skip_depth, args.force, scorer,
    )

    # Per-folder size memory for the stability gate, persists across loop sweeps.
    stable_sizes: dict[str, int] = {}

    if args.once:
        # Single sweep. For --once we still apply the stability gate, but a
        # first-seen folder would be deferred — so for the one-shot path we seed
        # each candidate's size first, then immediately re-sweep so settled
        # folders score in this single invocation (the documented test/cron use).
        for s in discover_sessions(ingest_dir):
            stable_sizes.setdefault(str(s.resolve()), dir_size_bytes(s))
        sweep_once(
            ingest_dir=ingest_dir, ledger_path=ledger_path, scorer=scorer,
            skip_depth=args.skip_depth, force=args.force, stable_sizes=stable_sizes,
        )
        logger.info("--once sweep done — exiting")
        return 0

    # Poll loop.
    while not _SHUTDOWN:
        sweep_once(
            ingest_dir=ingest_dir, ledger_path=ledger_path, scorer=scorer,
            skip_depth=args.skip_depth, force=args.force, stable_sizes=stable_sizes,
        )
        # Sleep in short slices so SIGINT/SIGTERM is honoured promptly.
        slept = 0.0
        while slept < args.poll_seconds and not _SHUTDOWN:
            time.sleep(min(1.0, args.poll_seconds - slept))
            slept += 1.0

    logger.info("ingest_worker shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
