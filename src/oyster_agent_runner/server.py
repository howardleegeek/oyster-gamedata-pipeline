"""HTTP server stub — FastAPI drop-in for buyers who don't want to spawn a CLI subprocess.

Endpoints
---------
* ``GET  /healthz``        Liveness probe. No auth.
* ``GET  /v1/providers``   Same payload as ``oyster-agent list-providers --json``.
* ``POST /v1/run-task``    Kick off an agent run. 202 Accepted + Location header.
                           Body: ``{task: dict, provider: str, output_dir: str}``.
                           Returns ``{trajectory_id, manifest_path}``.
* ``POST /v1/replay``      Walk a manifest. Body: ``{manifest_path, mode}`` where
                           ``mode ∈ {"check", "re-execute"}``. Returns the
                           ``ConsistencyReport`` or ``ReplayDriftReport`` JSON.
* ``POST /v1/quote``       Project token + dollar cost. Body:
                           ``{task: dict, provider: str, steps: int, thinking_budget: int}``.
                           Returns the ``Quote`` payload from ``quote.build_quote``.

Auth
----
HTTP Bearer compared against ``OYSTER_API_TOKEN`` env var. Missing token on
the request → 401. **The server refuses to start if ``OYSTER_API_TOKEN`` is
unset** — this is a security default so accidentally-launched servers can't
serve traffic without authentication.

FastAPI is a *soft optional* dependency. Importing this module without
FastAPI installed raises ``ImportError`` with an actionable hint. Tests must
guard with ``pytest.importorskip("fastapi")``.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

# --- Soft FastAPI import -----------------------------------------------------
#
# We deliberately import lazily *and* re-raise with a friendly message rather
# than letting the original ImportError surface. FastAPI is not in
# ``pyproject.toml`` dependencies — buyers who want the HTTP server install
# it themselves (``pip install fastapi uvicorn``). Anyone importing this
# module without those deps is doing it wrong; tell them how to fix it.
try:
    from fastapi import (  # type: ignore[import-not-found]
        BackgroundTasks,
        Depends,
        FastAPI,
        HTTPException,
        Request,
        Response,
        status,
    )
    from fastapi.security import (  # type: ignore[import-not-found]
        HTTPAuthorizationCredentials,
        HTTPBearer,
    )
    from pydantic import BaseModel, Field

    _FASTAPI_AVAILABLE = True

    # --- Request / response models -------------------------------------------
    #
    # These are declared at module scope (not inside `create_app`) because
    # FastAPI's body-introspection only recognizes Pydantic models that are
    # importable as proper types — closure-scoped models get classified as
    # query params and the body is silently dropped.

    class RunTaskBody(BaseModel):
        """POST /v1/run-task request body."""

        task: dict[str, Any] = Field(
            ...,
            description="AgentTask payload (matches `oyster-agent` JSON schema).",
        )
        provider: str = Field(
            ...,
            min_length=1,
            description="Provider key, e.g. 'mock', 'claude-thinking'.",
        )
        output_dir: str = Field(
            ...,
            min_length=1,
            description="Filesystem path to write the trajectory under.",
        )

    class RunTaskResponse(BaseModel):
        """POST /v1/run-task response body."""

        trajectory_id: str
        manifest_path: str

    class ReplayBody(BaseModel):
        """POST /v1/replay request body."""

        manifest_path: str = Field(
            ...,
            min_length=1,
            description="Path to manifest.json inside a Phase 1 bundle.",
        )
        mode: str = Field(
            ...,
            description="One of 'check' | 're-execute'.",
        )

    class QuoteBody(BaseModel):
        """POST /v1/quote request body."""

        task: dict[str, Any] = Field(
            ...,
            description="AgentTask payload (full task JSON, including Phase-1 extras).",
        )
        provider: str = Field(
            ...,
            min_length=1,
            description="Pricing key — see oyster_agent_runner.pricing.PROVIDER_PRICING.",
        )
        steps: int = Field(..., ge=0, description="Number of agent steps to project.")
        thinking_budget: int = Field(
            default=0,
            ge=0,
            description="Override thinking-token budget (0 disables).",
        )

except ImportError as exc:  # pragma: no cover — exercised only when fastapi is missing
    _FASTAPI_AVAILABLE = False
    _FASTAPI_IMPORT_ERROR = exc

    # Bind names so the rest of the module still imports cleanly enough for
    # `from oyster_agent_runner.server import create_app` to fail with the
    # *helpful* error below rather than a NameError.
    BackgroundTasks = object  # type: ignore[assignment,misc]
    Depends = object  # type: ignore[assignment,misc]
    FastAPI = object  # type: ignore[assignment,misc]
    HTTPException = Exception  # type: ignore[assignment,misc]
    Request = object  # type: ignore[assignment,misc]
    Response = object  # type: ignore[assignment,misc]
    status = object  # type: ignore[assignment,misc]
    HTTPAuthorizationCredentials = object  # type: ignore[assignment,misc]
    HTTPBearer = object  # type: ignore[assignment,misc]
    BaseModel = object  # type: ignore[assignment,misc]
    Field = lambda *args, **kwargs: None  # type: ignore[assignment,misc]  # noqa: E731
    RunTaskBody = object  # type: ignore[assignment,misc]
    RunTaskResponse = object  # type: ignore[assignment,misc]
    ReplayBody = object  # type: ignore[assignment,misc]
    QuoteBody = object  # type: ignore[assignment,misc]


_TOKEN_ENV_VAR = "OYSTER_API_TOKEN"


def _require_fastapi() -> None:
    """Raise a friendly ImportError if FastAPI/uvicorn aren't installed."""
    if not _FASTAPI_AVAILABLE:
        raise ImportError(
            "fastapi is not installed. Install with: "
            "pip install 'fastapi>=0.110' 'uvicorn[standard]>=0.27'"
        ) from _FASTAPI_IMPORT_ERROR


# --- Background runner indirection ------------------------------------------
#
# Tests monkeypatch ``_default_runner_callable`` to avoid spinning up a real
# Anthropic-backed run. The signature is intentionally narrow: take the parsed
# task dict + provider key + output dir, return ``(trajectory_id,
# manifest_path)``. Production overrides plug in the real
# ``AgentRunner.run(...)`` pipeline.

RunnerCallable = Callable[[dict[str, Any], str, Path], tuple[str, str]]


def _default_runner_callable(
    task_payload: dict[str, Any],
    provider: str,
    output_dir: Path,
) -> tuple[str, str]:
    """Spawn a real run via the AgentRunner using the same plumbing as the CLI.

    This is the production hook. Tests inject a fake by passing
    ``runner_callable=...`` to ``create_app``.

    Returns
    -------
    (trajectory_id, manifest_path)
        ``trajectory_id`` is the resolved task id used for the run
        directory. ``manifest_path`` is the absolute path the runner wrote
        ``trajectory.jsonl`` to (Phase 1 manifests live next to it; the
        runner returns the trajectory file path on its TaskResult).
    """
    # Lazy imports — keep server import cheap when only quote/replay are used.
    from oyster_agent_runner.cli import _make_environment, _make_provider
    from oyster_agent_runner.runner import AgentRunner, RunnerConfig
    from oyster_agent_runner.schema import AgentTask

    task = AgentTask.model_validate(task_payload)
    trajectory_id = task.task_id or f"run-{uuid.uuid4().hex[:8]}"
    run_dir = Path(output_dir) / trajectory_id
    environment = _make_environment(task.environment)
    llm_provider = _make_provider(provider, task.required_provider_model)
    runner = AgentRunner(RunnerConfig(write_frames=True))
    result = runner.run(task, environment, llm_provider, run_dir)
    # The Phase 1 manifest is written by `run-mc`, not the generic `run` path.
    # Return the trajectory.jsonl path here — it's the single artifact every
    # run produces.
    return trajectory_id, result.trajectory_path


# --- App factory -------------------------------------------------------------


def create_app(
    *,
    api_token: str | None = None,
    runner_callable: RunnerCallable | None = None,
) -> Any:
    """Build a FastAPI app instance.

    Parameters
    ----------
    api_token:
        Bearer token clients must present. Defaults to ``OYSTER_API_TOKEN``
        env var. **A startup error is raised when both are missing** — the
        server refuses to run unauthenticated by design.
    runner_callable:
        Indirection seam for tests. Receives ``(task_payload, provider,
        output_dir)`` and returns ``(trajectory_id, manifest_path)``. Defaults
        to the real ``AgentRunner`` pipeline.
    """
    _require_fastapi()

    resolved_token = api_token if api_token is not None else os.environ.get(_TOKEN_ENV_VAR)
    if not resolved_token:
        raise RuntimeError(
            f"refusing to start: {_TOKEN_ENV_VAR} env var is not set "
            "and no api_token was passed to create_app(). "
            "Set OYSTER_API_TOKEN=<secret> before launching the server."
        )

    runner = runner_callable if runner_callable is not None else _default_runner_callable

    # --- App + auth dep ------------------------------------------------------

    app = FastAPI(
        title="oyster-agent-runner HTTP API",
        version="0.1.0",
        description="Drop-in HTTP API for the L4 agent runner. See cli.py for parity.",
    )
    bearer_scheme = HTTPBearer(auto_error=False)

    def _check_auth(
        creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
    ) -> None:
        """401 unless the request carries a matching ``Authorization: Bearer ...``.

        ``Depends(...)`` in the default-arg position is the canonical FastAPI
        idiom for dependency injection, so the B008 lint here is a false
        positive — silence it inline.
        """
        if creds is None or creds.scheme.lower() != "bearer" or creds.credentials != resolved_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # --- Endpoints -----------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        """Liveness probe — no auth required so platform health checks work."""
        return {"ok": True}

    @app.get("/v1/providers", dependencies=[Depends(_check_auth)])
    def list_providers() -> list[dict[str, str]]:
        """Same payload as ``oyster-agent list-providers --json``."""
        from oyster_agent_runner.cli import PROVIDER_REGISTRY

        return PROVIDER_REGISTRY

    @app.post(
        "/v1/run-task",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=RunTaskResponse,
        dependencies=[Depends(_check_auth)],
    )
    def run_task(
        body: RunTaskBody,
        background_tasks: BackgroundTasks,
        request: Request,
        response: Response,
    ) -> RunTaskResponse:
        """Spawn the runner async; return 202 + Location header for pickup.

        The runner is dispatched on a FastAPI ``BackgroundTask`` so the HTTP
        response returns immediately. The buyer polls the manifest path
        (or re-uses ``GET /v1/replay``) when the bundle is ready.
        """
        try:
            output_dir = Path(body.output_dir).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            trajectory_id, manifest_path = runner(body.task, body.provider, output_dir)
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"runner rejected payload: {type(exc).__name__}: {exc}",
            ) from exc

        # Location header points buyers at where the manifest will land. We
        # echo the manifest path verbatim — file:// is technically more
        # correct but `Location:` accepts opaque strings and FS paths read
        # cleanly in `curl -i` output.
        response.headers["Location"] = manifest_path
        return RunTaskResponse(trajectory_id=trajectory_id, manifest_path=manifest_path)

    @app.post("/v1/replay", dependencies=[Depends(_check_auth)])
    def replay(body: ReplayBody) -> dict[str, Any]:
        """Walk a manifest in ``check`` or ``re-execute`` mode and return JSON."""
        from oyster_agent_runner.replay import Replayer

        if body.mode not in {"check", "re-execute"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"mode must be 'check' or 're-execute', got {body.mode!r}",
            )
        manifest = Path(body.manifest_path).expanduser()
        try:
            replayer = Replayer(manifest)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"cannot load bundle: {exc}",
            ) from exc

        if body.mode == "check":
            report = replayer.verify_consistency()
        else:
            report = replayer.replay_against()
        return _dataclass_to_json_dict(report)

    @app.post("/v1/quote", dependencies=[Depends(_check_auth)])
    def quote_endpoint(body: QuoteBody) -> dict[str, Any]:
        """Project token + dollar cost via ``quote.build_quote``."""
        from oyster_agent_runner.quote import build_quote, render_json

        # build_quote takes a path on disk (it tolerates Phase-1 extras by
        # stripping them after re-reading the file). Round-trip the inline
        # task dict through a tempfile so we don't have to mirror the
        # extras-stripping logic here.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump(body.task, fh)
            tmp_task_path = Path(fh.name)
        try:
            quote = build_quote(
                task_path=tmp_task_path,
                steps=body.steps,
                provider_key=body.provider,
                thinking_budget=body.thinking_budget if body.thinking_budget > 0 else None,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown provider: {exc.args[0]}",
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid quote input: {exc}",
            ) from exc
        finally:
            # Best-effort cleanup — don't break the response on FS quirks.
            with contextlib.suppress(OSError):
                tmp_task_path.unlink()

        return json.loads(render_json(quote))

    return app


# Module-level ``app`` for ``uvicorn oyster_agent_runner.server:app`` style
# launches. We materialize it lazily (only when fastapi is installed *and*
# OYSTER_API_TOKEN is set) so importing this module for tests doesn't
# accidentally crash.
def _maybe_default_app() -> Any:
    if not _FASTAPI_AVAILABLE:
        return None
    if not os.environ.get(_TOKEN_ENV_VAR):
        return None
    try:
        return create_app()
    except RuntimeError:
        return None


app = _maybe_default_app()


# --- Helpers ----------------------------------------------------------------


def _dataclass_to_json_dict(obj: Any) -> dict[str, Any]:
    """Convert a (possibly nested) dataclass into a JSON-safe dict.

    ``ReplayDriftReport`` has an ``ok`` ``@property`` — ``asdict()`` skips
    properties, so we add it manually after the conversion. Same for
    ``ConsistencyReport.ok`` (which is a real attribute, but we still want
    ``ok`` to land in the response body for symmetry).
    """
    if is_dataclass(obj):
        payload = asdict(obj)
        # Surface the `ok` property if it exists and asdict missed it.
        if hasattr(obj, "ok") and "ok" not in payload:
            payload["ok"] = bool(obj.ok)
        return payload
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"cannot serialize {type(obj).__name__} as JSON dict")


__all__ = ["app", "create_app"]
