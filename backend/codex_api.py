#!/usr/bin/env python3
r"""codex_api — HTTP wrapper for OpenAI Codex CLI (dispatch + parallel).

Run on mac1 to expose Codex CLI as an HTTP service. Used to dispatch
parallel code-writing tasks to Codex without spawning shell subprocesses
each time.

Usage:
    pip install fastapi uvicorn
    python3 backend/codex_api.py
    # Listens on http://127.0.0.1:9000

Then from any script:
    curl -X POST http://127.0.0.1:9000/codex/exec \\
         -H 'Content-Type: application/json' \\
         -d '{"prompt": "list files in current dir", "repo_dir": "/tmp"}'
    # Returns: {"job_id": "abc12345", "status": "queued"}

    curl http://127.0.0.1:9000/codex/job/abc12345
    # Returns: {"status": "completed", "stdout": "...", "stderr": "..."}

Endpoints:
    POST /codex/exec          — async dispatch (returns job_id immediately)
    POST /codex/exec_sync     — sync execution (blocks until done)
    GET  /codex/job/<id>      — poll job status / result
    GET  /codex/jobs          — list all jobs
    DELETE /codex/job/<id>    — cancel running job
    GET  /health              — heartbeat
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from threading import Lock, Thread
import logging

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ImportError as e:
    msg = "missing deps: pip install fastapi uvicorn pydantic"
    raise SystemExit(msg) from e

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CODEX_BIN = os.environ.get(
    "CODEX_BIN",
    "/Users/howardli/.nvm/versions/node/v18.20.8/bin/codex",
)
DEFAULT_REPO = os.environ.get(
    "CODEX_REPO",
    "/Users/howardli/Downloads/oyster-gamedata-pipeline",
)
JOBS_DIR = Path(os.environ.get("CODEX_JOBS_DIR", "/tmp/codex_api_jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job registry (also persisted to disk)
_jobs: dict[str, dict] = {}
_jobs_lock = Lock()
_running_procs: dict[str, subprocess.Popen] = {}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CodexTask(BaseModel):
    prompt: str = Field(..., min_length=1, description="Task instructions for Codex")
    repo_dir: str = Field(default_factory=lambda: DEFAULT_REPO,
                          description="Working directory for codex exec -C")
    model: str | None = Field(default=None, description="Override model (e.g. 'o3')")
    timeout_sec: int = Field(default=1200, ge=30, le=3600)
    full_auto: bool = Field(default=True, description="Pass --full-auto")
    skip_git_repo_check: bool = Field(default=True)


class JobStatus(BaseModel):
    job_id: str
    status: str  # queued / running / completed / failed / timeout / cancelled
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    prompt_preview: str | None = None


# ---------------------------------------------------------------------------
# Codex runner
# ---------------------------------------------------------------------------


def _persist_job(job_id: str) -> None:
    f = JOBS_DIR / f"{job_id}.json"
    with _jobs_lock:
        if job_id in _jobs:
            f.write_text(json.dumps(_jobs[job_id], indent=2), encoding="utf-8")


def _update_job(job_id: str, **kwargs) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)
    _persist_job(job_id)


def _run_codex_in_thread(job_id: str, task: CodexTask) -> None:
    """Background thread that runs codex subprocess + writes result."""
    _update_job(job_id, status="running", started_at=time.time())

    cmd = [CODEX_BIN, "exec"]
    if task.skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    if task.full_auto:
        cmd.append("--full-auto")
    cmd.extend(["-C", task.repo_dir])
    if task.model:
        cmd.extend(["-c", f'model="{task.model}"'])
    cmd.append(task.prompt)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with _jobs_lock:
            _running_procs[job_id] = proc

        try:
            stdout, stderr = proc.communicate(timeout=task.timeout_sec)
            rc = proc.returncode
            status = "completed" if rc == 0 else "failed"
            _update_job(
                job_id,
                status=status,
                finished_at=time.time(),
                exit_code=rc,
                stdout=(stdout or "")[-20000:],
                stderr=(stderr or "")[-5000:],
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                logger.debug(
                    "proc.wait(timeout=10) timed out during cleanup for job_id=%s: %s",
                    job_id,
                    exc,
                )
            _update_job(
                job_id,
                status="timeout",
                finished_at=time.time(),
                error=f"timeout after {task.timeout_sec}s",
            )
    except (OSError, FileNotFoundError) as exc:
        _update_job(
            job_id,
            status="error",
            finished_at=time.time(),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        with _jobs_lock:
            _running_procs.pop(job_id, None)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


app = FastAPI(title="Codex API", version="0.1")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "codex_bin": CODEX_BIN,
        "codex_alive": Path(CODEX_BIN).exists(),
        "jobs_dir": str(JOBS_DIR),
        "running_jobs": len(_running_procs),
        "total_jobs": len(_jobs),
    }


@app.post("/codex/exec", response_model=JobStatus)
def exec_async(task: CodexTask) -> JobStatus:
    """Dispatch a codex task asynchronously. Returns job_id to poll."""
    if not Path(CODEX_BIN).exists():
        raise HTTPException(500, f"codex binary not found at {CODEX_BIN}")
    if not Path(task.repo_dir).is_dir():
        raise HTTPException(400, f"repo_dir not a directory: {task.repo_dir}")

    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "prompt_preview": task.prompt[:200],
            "repo_dir": task.repo_dir,
            "model": task.model,
        }
    _persist_job(job_id)

    Thread(target=_run_codex_in_thread, args=(job_id, task), daemon=True).start()

    return JobStatus(**_jobs[job_id])


@app.post("/codex/exec_sync", response_model=JobStatus)
def exec_sync(task: CodexTask) -> JobStatus:
    """Synchronous codex exec. Blocks until done."""
    response = exec_async(task)
    job_id = response.job_id
    # Poll
    while True:
        with _jobs_lock:
            j = _jobs.get(job_id, {}).copy()
        if j.get("status") in ("completed", "failed", "timeout", "error", "cancelled"):
            return JobStatus(**j)
        time.sleep(1)


@app.get("/codex/job/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    with _jobs_lock:
        j = _jobs.get(job_id)
    if j is None:
        # Try disk
        f = JOBS_DIR / f"{job_id}.json"
        if f.exists():
            j = json.loads(f.read_text(encoding="utf-8"))
        else:
            raise HTTPException(404, f"job {job_id} not found")
    return JobStatus(**j)


@app.get("/codex/jobs")
def list_jobs() -> dict:
    with _jobs_lock:
        return {
            "total": len(_jobs),
            "running": len(_running_procs),
            "jobs": list(_jobs.values()),
        }


@app.delete("/codex/job/{job_id}")
def cancel_job(job_id: str) -> dict:
    with _jobs_lock:
        proc = _running_procs.get(job_id)
    if proc is None:
        raise HTTPException(404, f"job {job_id} not running")
    proc.send_signal(signal.SIGTERM)
    _update_job(job_id, status="cancelled", finished_at=time.time())
    return {"job_id": job_id, "cancelled": True}


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()

    print(f"Codex API on http://{args.host}:{args.port}")
    print(f"  codex_bin: {CODEX_BIN}")
    print(f"  default repo: {DEFAULT_REPO}")
    print(f"  jobs dir: {JOBS_DIR}")
    print()
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
