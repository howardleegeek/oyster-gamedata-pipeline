"""Cluster Dispatcher Daemon.

Scans for ready specs, dispatches them to the cluster agent runner,
tracks state, and opens PRs for successful runs.

Usage:
    python3 daemon/cluster_dispatcher.py --once --dry-run
    python3 daemon/cluster_dispatcher.py --once --max-concurrent 2
    python3 daemon/cluster_dispatcher.py              # daemon mode (cron)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_STATE_FILE = Path.home() / ".oyster" / "cluster_dispatcher_state.json"
DEFAULT_MAX_CONCURRENT = 4
DEFAULT_POLL_INTERVAL = 15 * 60  # 15 minutes
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 3600  # 1 hour per spec

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SpecEntry:
    """Represents a spec that is ready for dispatch."""

    task_id: str
    spec_path: str
    title: str = ""
    priority: int = 2
    source: str = ""  # "auto" or "specs"


@dataclass
class DispatchState:
    """Tracks the dispatch state of a single spec."""

    task_id: str
    status: str = "pending"  # pending | dispatched | running | success | failed | dead
    attempts: int = 0
    last_dispatched: str = ""
    last_error: str = ""
    working_dir: str = ""
    pr_url: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now_iso()


@dataclass
class ClusterState:
    """Full persistent state for the dispatcher."""

    specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_scan: str = ""

    def get_entry(self, task_id: str) -> DispatchState:
        if task_id not in self.specs:
            entry = DispatchState(task_id=task_id)
            self.specs[task_id] = asdict(entry)
        raw = self.specs[task_id]
        return DispatchState(**raw)

    def update_entry(self, entry: DispatchState) -> None:
        self.specs[entry.task_id] = asdict(entry)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "specs": self.specs,
            "last_scan": self.last_scan,
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "ClusterState":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        state = cls(specs=raw.get("specs", {}), last_scan=raw.get("last_scan", ""))
        return state


# ---------------------------------------------------------------------------
# Spec scanning
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_spec_header(path: Path) -> dict[str, str]:
    """Parse YAML-like front-matter from a spec markdown file."""
    header: dict[str, str] = {}
    try:
        text = path.read_text()
    except Exception as exc:
        logger.debug("_parse_spec_header: failed to read %s: %s", path, exc)
        return header

    in_header = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if not in_header:
                in_header = True
                continue
            else:
                break
        if in_header and ":" in stripped:
            key, _, val = stripped.partition(":")
            header[key.strip().lower()] = val.strip()
    return header


def scan_auto_specs(specs_dir: Path) -> list[SpecEntry]:
    """Scan specs/auto/*.md for iter-watcher produced drafts."""
    entries: list[SpecEntry] = []
    auto_dir = specs_dir / "auto"
    if not auto_dir.exists():
        return entries

    for md_file in sorted(auto_dir.glob("*.md")):
        header = _parse_spec_header(md_file)
        task_id = header.get("task_id", md_file.stem)
        title = header.get("title", md_file.stem)
        priority = int(header.get("priority", "2"))
        entries.append(
            SpecEntry(
                task_id=task_id,
                spec_path=str(md_file),
                title=title,
                priority=priority,
                source="auto",
            )
        )
    return entries


def scan_ready_specs(specs_dir: Path) -> list[SpecEntry]:
    """Scan specs/S*.md for specs marked as ready."""
    entries: list[SpecEntry] = []
    if not specs_dir.exists():
        return entries

    for md_file in sorted(specs_dir.glob("S*.md")):
        header = _parse_spec_header(md_file)
        status = header.get("status", "").lower()
        if status != "ready":
            continue
        task_id = header.get("task_id", md_file.stem)
        title = header.get("title", md_file.stem)
        priority = int(header.get("priority", "2"))
        entries.append(
            SpecEntry(
                task_id=task_id,
                spec_path=str(md_file),
                title=title,
                priority=priority,
                source="specs",
            )
        )
    return entries


def scan_all_specs(specs_dir: Path) -> list[SpecEntry]:
    """Combine auto and ready specs, deduplicate by task_id."""
    auto_entries = scan_auto_specs(specs_dir)
    ready_entries = scan_ready_specs(specs_dir)
    seen: dict[str, SpecEntry] = {}
    for entry in auto_entries + ready_entries:
        if entry.task_id not in seen:
            seen[entry.task_id] = entry
    return sorted(seen.values(), key=lambda e: (e.priority, e.task_id))


# ---------------------------------------------------------------------------
# Working directory preparation
# ---------------------------------------------------------------------------


def prepare_working_dir(task_id: str, source_root: Path) -> Path:
    """Create /tmp/cluster-<date>/<task_id>-output/ and copy source."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = Path("/tmp") / f"cluster-{date_str}"
    working_dir = base / f"{task_id}-output"

    if working_dir.exists():
        # Reuse existing working dir for retries
        return working_dir

    working_dir.mkdir(parents=True, exist_ok=True)

    # Copy bin/ and tests/ directories
    for subdir in ["bin", "tests"]:
        src = source_root / subdir
        if src.exists():
            dst = working_dir / subdir
            shutil.copytree(src, dst, dirs_exist_ok=True)

    return working_dir


# ---------------------------------------------------------------------------
# Dispatch execution
# ---------------------------------------------------------------------------


def run_agent(
    spec_path: str,
    working_dir: Path,
    task_id: str,
    agent_model: str = "qwen3.6-plus",
    timeout: int = DEFAULT_TIMEOUT,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Run minimax_agent_simple.py via subprocess.

    Returns (success, error_message).
    """
    if dry_run:
        logger.info("[DRY-RUN] Would run agent for %s", task_id)
        return True, ""

    # Find minimax_agent_simple.py
    agent_script = _find_agent_script()
    if not agent_script:
        return False, "minimax_agent_simple.py not found"

    env = os.environ.copy()
    env["SPEC_FILE"] = spec_path
    env["WORKING_DIR"] = str(working_dir)
    env["TASK_ID"] = task_id
    env["AGENT_MODEL"] = agent_model

    try:
        result = subprocess.run(
            [sys.executable, str(agent_script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(working_dir),
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            return False, f"exit code {result.returncode}: {stderr}"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except Exception as exc:
        return False, str(exc)


def _find_agent_script() -> Path | None:
    """Locate minimax_agent_simple.py in common locations."""
    candidates = [
        Path("bin/minimax_agent_simple.py"),
        Path("scripts/minimax_agent_simple.py"),
        Path("minimax_agent_simple.py"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None


# ---------------------------------------------------------------------------
# PR creation
# ---------------------------------------------------------------------------


def create_pr(
    working_dir: Path,
    task_id: str,
    title: str,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Create a PR via gh CLI if there are diffs.

    Returns (success, pr_url_or_error).
    """
    if dry_run:
        logger.info("[DRY-RUN] Would create PR for %s", task_id)
        return True, "dry-run-pr-url"

    # Check for diffs
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--quiet"],
            cwd=str(working_dir),
            capture_output=True,
        )
        if diff_result.returncode == 0:
            # No changes
            return True, ""
    except Exception as exc:
        logger.debug(
            "create_pr: git diff --quiet failed in %s: %s", working_dir, exc
        )
        pass

    pr_title = f"[cluster] {task_id}: {title}"
    pr_body = f"Auto-generated PR from cluster dispatcher for task {task_id}."

    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--title", pr_title, "--body", pr_body],
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        # gh pr create prints the URL to stdout
        pr_url = result.stdout.strip()
        return True, pr_url
    except subprocess.TimeoutExpired:
        return False, "gh pr create timed out"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Main dispatch logic
# ---------------------------------------------------------------------------


def dispatch_spec(
    spec: SpecEntry,
    state: ClusterState,
    source_root: Path,
    max_concurrent: int,
    dry_run: bool,
    timeout: int,
    agent_model: str,
) -> DispatchState:
    """Dispatch a single spec. Returns updated state entry."""
    entry = state.get_entry(spec.task_id)

    # Skip if already dead
    if entry.status == "dead":
        logger.info("Skipping dead spec: %s", spec.task_id)
        return entry

    # Skip if already succeeded
    if entry.status == "success":
        logger.info("Skipping already-success spec: %s", spec.task_id)
        return entry

    # Check retry limit
    if entry.attempts >= DEFAULT_MAX_RETRIES and entry.status == "failed":
        entry.status = "dead"
        entry.last_error = f"Exceeded max retries ({DEFAULT_MAX_RETRIES})"
        state.update_entry(entry)
        logger.warning(
            "Marking spec as dead after %d failures: %s", entry.attempts, spec.task_id
        )
        return entry

    # Prepare working dir
    working_dir = prepare_working_dir(spec.task_id, source_root)
    entry.working_dir = str(working_dir)

    # Copy spec file to working dir
    spec_src = Path(spec.spec_path)
    if spec_src.exists():
        spec_dst = working_dir / spec_src.name
        shutil.copy2(spec_src, spec_dst)

    # Run agent
    entry.status = "running"
    entry.attempts += 1
    entry.last_dispatched = _now_iso()
    state.update_entry(entry)

    if not dry_run:
        logger.info("Dispatching spec %s (attempt %d)...", spec.task_id, entry.attempts)

    success, error = run_agent(
        spec_path=spec.spec_path,
        working_dir=working_dir,
        task_id=spec.task_id,
        agent_model=agent_model,
        timeout=timeout,
        dry_run=dry_run,
    )

    if success:
        # Create PR
        pr_success, pr_result = create_pr(
            working_dir=working_dir,
            task_id=spec.task_id,
            title=spec.title,
            dry_run=dry_run,
        )
        if pr_success and pr_result:
            entry.pr_url = pr_result
        entry.status = "success"
        entry.last_error = ""
        logger.info("Spec %s completed successfully", spec.task_id)
    else:
        entry.status = "failed"
        entry.last_error = error
        logger.error("Spec %s failed: %s", spec.task_id, error)

    state.update_entry(entry)
    return entry


def run_dispatch_cycle(
    specs_dir: Path,
    state: ClusterState,
    source_root: Path,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    dry_run: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    agent_model: str = "qwen3.6-plus",
) -> list[DispatchState]:
    """Run one full dispatch cycle.

    Respects max_concurrent by batching specs.
    """
    all_specs = scan_all_specs(specs_dir)
    if not all_specs:
        logger.info("No specs found to dispatch")
        return []

    # Filter to dispatchable specs
    dispatchable = []
    for spec in all_specs:
        entry = state.get_entry(spec.task_id)
        if entry.status in ("success", "dead"):
            continue
        dispatchable.append(spec)

    if not dispatchable:
        logger.info("All specs already processed")
        return []

    if dry_run:
        logger.info("[DRY-RUN] Would dispatch %d specs:", len(dispatchable))
        for spec in dispatchable:
            entry = state.get_entry(spec.task_id)
            logger.info(
                "  - %s [%s] (attempts: %d, status: %s)",
                spec.task_id,
                spec.title,
                entry.attempts,
                entry.status,
            )
        # Still run dispatch in dry-run mode to update state
        results = []
        for spec in dispatchable:
            result = dispatch_spec(
                spec,
                state,
                source_root,
                max_concurrent,
                dry_run=True,
                timeout=timeout,
                agent_model=agent_model,
            )
            results.append(result)
        return results

    # Batch dispatch respecting concurrency limit
    results: list[DispatchState] = []
    for i in range(0, len(dispatchable), max_concurrent):
        batch = dispatchable[i : i + max_concurrent]
        batch_results = []
        for spec in batch:
            result = dispatch_spec(
                spec,
                state,
                source_root,
                max_concurrent,
                dry_run=False,
                timeout=timeout,
                agent_model=agent_model,
            )
            batch_results.append(result)
        results.extend(batch_results)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cluster Dispatcher Daemon — dispatches ready specs to cluster agents",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single dispatch cycle and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate dispatch without running agents or creating PRs",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=DEFAULT_MAX_CONCURRENT,
        help=f"Max concurrent dispatches (default: {DEFAULT_MAX_CONCURRENT})",
    )
    parser.add_argument(
        "--state-file",
        type=str,
        default=str(DEFAULT_STATE_FILE),
        help=f"Path to state file (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--specs-dir",
        type=str,
        default="specs",
        help="Path to specs directory (default: specs)",
    )
    parser.add_argument(
        "--source-root",
        type=str,
        default=".",
        help="Path to source root for copying bin/ and tests/ (default: .)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout per spec in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--agent-model",
        type=str,
        default="qwen3.6-plus",
        help="Agent model to use (default: qwen3.6-plus)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Seconds between dispatch cycles in daemon mode (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    specs_dir = Path(args.specs_dir)
    source_root = Path(args.source_root).resolve()
    state_file = Path(args.state_file)

    # Load state
    state = ClusterState.load(state_file)

    if args.once:
        results = run_dispatch_cycle(
            specs_dir=specs_dir,
            state=state,
            source_root=source_root,
            max_concurrent=args.max_concurrent,
            dry_run=args.dry_run,
            timeout=args.timeout,
            agent_model=args.agent_model,
        )
        state.last_scan = _now_iso()
        state.save(state_file)

        if args.dry_run:
            print(
                f"\n[DRY-RUN] Dispatch cycle complete. {len(results)} specs processed."
            )
            for r in results:
                print(f"  {r.task_id}: {r.status} (attempts: {r.attempts})")
        else:
            print(f"\nDispatch cycle complete. {len(results)} specs processed.")
            for r in results:
                print(f"  {r.task_id}: {r.status} (attempts: {r.attempts})")
                if r.pr_url:
                    print(f"    PR: {r.pr_url}")
                if r.last_error:
                    print(f"    Error: {r.last_error}")
        return 0

    # Daemon mode
    logger.info(
        "Starting cluster dispatcher daemon (poll every %ds)...", args.poll_interval
    )
    while True:
        try:
            results = run_dispatch_cycle(
                specs_dir=specs_dir,
                state=state,
                source_root=source_root,
                max_concurrent=args.max_concurrent,
                dry_run=False,
                timeout=args.timeout,
                agent_model=args.agent_model,
            )
            state.last_scan = _now_iso()
            state.save(state_file)
            logger.info("Cycle complete. %d specs processed.", len(results))
        except Exception as exc:
            logger.error("Dispatch cycle error: %s", exc)

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
